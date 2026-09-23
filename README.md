# divoom-pi

**Control your Divoom from Home Assistant, over the network, through a Raspberry Pi.**

Both halves live here: the **Home Assistant integration** (installed through HACS, giving you a
real device with a light, a clock, channel and style selectors, brightness, volume and buttons) and
the **Pi gateway** (a service that bridges Bluetooth Classic to TCP, installed with one command).

Flash a Pi, SSH in, run one command:

```bash
curl -fsSL https://raw.githubusercontent.com/adman234/divoom-pi/main/install.sh | sudo bash
```

That is the whole install. The Pi then finds your Divoom over Bluetooth, announces it on your
network, and Home Assistant offers to add it.

The Home Assistant integration and the Divoom protocol come from
[d03n3rfr1tz3](https://github.com/d03n3rfr1tz3)'s [hass-divoom](https://github.com/d03n3rfr1tz3/hass-divoom),
and the gateway speaks the same TCP protocol as his
[esp32-divoom](https://github.com/d03n3rfr1tz3/esp32-divoom) firmware. See [Credits](#credits).

---

## Why this exists

Divoom devices (Pixoo, Ditoo, Timebox, Tivoo, Timoo, Aurabox) talk **Bluetooth Classic (SPP)**,
not BLE. That single fact causes all the trouble:

- Home Assistant's ESPHome Bluetooth proxies are **BLE-only** and cannot talk to a Divoom at all.
- Plenty of Home Assistant hosts have no working Bluetooth Classic, or aren't near the device.

The established answer is [d03n3rfr1tz3/esp32-divoom](https://github.com/d03n3rfr1tz3/esp32-divoom):
an ESP32 that bridges Bluetooth Classic to TCP, with
[hass-divoom](https://github.com/d03n3rfr1tz3/hass-divoom) driving it from Home Assistant. It works,
but only the original ESP32 chip has Bluetooth Classic at all, WiFi and Bluetooth fight over the one
radio, and every change means a compile-and-flash cycle.

A Raspberry Pi has BlueZ, so the bridge is a plain socket and an ordinary Linux service: one that
you can restart, read logs from, and debug over SSH. **divoom-pi speaks the exact same TCP protocol
as the ESP32 firmware**, so the Home Assistant integration cannot tell the difference and needs no
changes.

```
   Home Assistant                    Raspberry Pi                     Divoom
  ┌───────────────┐              ┌──────────────────┐            ┌──────────────┐
  │ divoom        │   TCP 7777   │    divoom-pi     │  RFCOMM    │   Ditoo /    │
  │ integration   │─────────────>│  (this project)  │───────────>│   Pixoo /    │
  │               │<─────────────│                  │<───────────│   Timebox    │
  └───────────────┘   replies    └──────────────────┘  Bluetooth └──────────────┘
         ▲                                │                          Classic
         └────────────────────────────────┘
            mDNS: "there is a Divoom here"
              -> Home Assistant offers to add the device
```

## What you need

| | |
| --- | --- |
| **A Raspberry Pi with Bluetooth** | Pi Zero W, Zero 2 W, 3, 4, 5: anything with built-in Bluetooth. A Pi Zero W (1st gen) is plenty; this daemon is idle almost all the time. |
| **Raspberry Pi OS** | The default image, Lite or Desktop, Bullseye or newer. Enable SSH in Raspberry Pi Imager. |
| **Network** | WiFi or Ethernet, on the same network as Home Assistant so mDNS discovery works. |
| **Home Assistant** | 2024.4 or newer, with the Divoom integration from this repository installed through HACS: see below. |

No Python packages to install: divoom-pi uses only the standard library and the `bluez` and
`avahi-daemon` packages that the installer sets up. That matters on a Pi Zero W, where compiling a
single Python wheel can take longer than the rest of the install put together.

> **Pi Zero W note.** WiFi and Bluetooth share one antenna on the Pi Zero W, so divoom-pi keeps
> Bluetooth scanning deliberately unhurried and never scans while it is relaying to your device.
> If you want that behaviour on a busier board too, it is all in `[discovery]` in the config.

## Install

Flash Raspberry Pi OS with SSH enabled, boot the Pi, SSH in, and run:

```bash
curl -fsSL https://raw.githubusercontent.com/adman234/divoom-pi/main/install.sh | sudo bash
```

The installer:

1. installs `bluez`, `avahi-daemon` and `python3` if they are missing,
2. copies the code to `/opt/divoom-pi` and a config file to `/etc/divoom-pi/config.ini`,
3. installs the `divoom-pi` command,
4. enables and starts a `divoom-pi` systemd service.

To update later, run the same command again: your config file is kept. To remove everything:

```bash
curl -fsSL https://raw.githubusercontent.com/adman234/divoom-pi/main/install.sh | sudo bash -s -- --uninstall
```

## Find your Divoom and test it

Turn the Divoom on, and **disconnect it from your phone**: Divoom devices accept one Bluetooth
connection at a time, and the Divoom app holds onto it.

```bash
divoom-pi scan
```

```
  B1:21:81:BF:A8:EB  DitooPro-Audio  <-- Divoom
  4C:E1:73:2A:90:11  (no name)
```

Now prove the whole path works, without involving Home Assistant at all:

```bash
divoom-pi selftest B1:21:81:BF:A8:EB --channel 2 --action on
```

This connects to the running gateway over TCP, asks it to open Bluetooth, and lights the display
white. If your Divoom lights up, everything below Home Assistant is working.

Most Divoom commands are not acknowledged, so "nothing came back" is normal for `on`, `off` and
`clock` - trust the display. `--action ping` sends the one command the device does answer, and
`--action clock` puts the display back to its clock face afterwards.

One to know about: `--action ha-on` sends exactly what the integration's `send_on()` sends, which
is RGB (1, 1, 1) - very nearly black. On a Ditoo that looks like the display switching *off*. It is
not a fault, and it is worth remembering if Home Assistant's light toggle ever seems to do nothing.

**The channel matters.** It is the RFCOMM channel of the device's serial port:

| Device | Channel |
| --- | --- |
| Ditoo, Ditoo Mic, Timoo | 2 |
| Aurabox, Timebox Mini | 4 |
| Pixoo, Pixoo Max, Timebox, Tivoo, Backpack | 1 |

If a device refuses to connect, `divoom-pi pair <MAC>` pairs it first.

## Set it up in Home Assistant

The Pi moves bytes; the integration in [`custom_components/divoom`](custom_components/divoom) is
what creates the device and its entities.

1. **Install the integration.** In HACS, add `https://github.com/adman234/divoom-pi` as a custom
   repository of type *Integration*, install **Divoom**, and restart Home Assistant. HACS copies
   only `custom_components/divoom`: the gateway code stays out of your Home Assistant config.

   > Already using `adman234/divoom-gateway`? Remove it from HACS first. Both provide the `divoom`
   > domain and they will collide. Your existing device survives the swap: the config entry is keyed
   > by domain and MAC, not by which repository the code came from.

2. **Add the device.** Within a minute or two of the gateway finding your Divoom, a **discovered**
   *Divoom* entry appears under *Settings → Devices & Services* with the Pi already filled in as the
   host. Click **Configure**, confirm the channel and device type, done.

   To add it by hand: *Add Integration → Divoom*, then your Divoom's **MAC address**, the **channel**
   from the table above, and the **host**: the Pi's IP address.

   **Use an IP address, or a name that actually resolves.** A bare hostname that Home Assistant
   cannot resolve is the single most common way to end up with a device that accepts every command
   and does nothing.

3. **Got it wrong?** *Settings → Devices & Services → Divoom → ⋮ → **Reconfigure*** changes the host,
   channel and device type in place, keeping your entity IDs. *Download diagnostics* on the same
   menu states plainly whether the device is being reached through the gateway or through Home
   Assistant's own Bluetooth adapter.

### What you get

| Entity | What it does |
| --- | --- |
| **Light** | The light channel: full RGB and brightness. |
| **Clock** | The colour and brightness of the clock face. Turning it off deactivates the clock, not the display. |
| **Clock style** | The clock faces and the music visualisers (see below). |
| **Channel** | Clock, light, effects, visualisation, design, lyrics. |
| **Brightness**, **Volume** | Device brightness and speaker volume. |
| **Buttons** | Device-specific extras: keyboard, equaliser and so on. |

Plus the full set of Divoom actions from upstream: text, images, GIFs, scoreboards, countdowns,
alarms, noise meter, radio, and the rest.

**About the clock styles.** The names Divoom's app uses do not match what the device renders, so
these were checked by hand against a Ditoo Pro: index 1 is the *negative* fullscreen face, not the
rainbow one, index 5 is the rainbow one, and indices 2–4 are not clock faces at all but music
visualisers. They are listed as clock faces first, then visualisers. **Another model may map these
differently**: if yours does, the mapping is one dict in
[`custom_components/divoom/const.py`](custom_components/divoom/const.py).

**About clock brightness.** The device's own brightness control only affects the effects drawn
around the clock; the digits keep their own colour and brightness. So the Clock entity applies
brightness by scaling its colour, which is the only lever the protocol offers over how bright the
digits appear.

## Commands

| Command | What it does |
| --- | --- |
| `divoom-pi doctor` | Checks everything (Bluetooth, Avahi, the service, the port) and says what is wrong. Start here. |
| `divoom-pi scan` | Scans for Bluetooth devices and flags the Divooms. |
| `divoom-pi pair <MAC>` | Pairs with a Divoom that will not connect without it. |
| `divoom-pi selftest <MAC> --channel N` | Does what Home Assistant does, and prints what came back. `--action ping\|on\|off\|clock`, `--color RRGGBB`, `--direct` to bypass the gateway. |
| `divoom-pi run` | Runs the gateway in the foreground (what the service does). |

Service management is ordinary systemd:

```bash
sudo systemctl restart divoom-pi
journalctl -u divoom-pi -f
```

## Configuration

`/etc/divoom-pi/config.ini`, documented inline: see
[`config.example.ini`](config.example.ini). Every value has a sensible default, so the file is only
there for when you need it. The one you are most likely to touch:

```ini
[gateway]
log_level = DEBUG
```

which logs every byte in both directions. Restart the service to apply changes.

## Troubleshooting

Run `divoom-pi doctor` first: it catches most of it. Beyond that, see
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md), which covers the usual suspects: the Divoom
still being connected to your phone, the wrong RFCOMM channel, Home Assistant and the Pi being on
different networks so mDNS never arrives, and pairing PINs.

## How it works

The gateway is a TCP server on port 7777 that relays to one Bluetooth RFCOMM link at a time, plus a
background scan that publishes discovered devices over mDNS. The wire protocol is four message
types and is documented in [docs/PROTOCOL.md](docs/PROTOCOL.md).

The layout:

| File | What is in it |
| --- | --- |
| [`divoom_pi/protocol.py`](divoom_pi/protocol.py) | The wire protocol: framing, parsing, message building. |
| [`divoom_pi/gateway.py`](divoom_pi/gateway.py) | The TCP server and the relay loop. |
| [`divoom_pi/rfcomm.py`](divoom_pi/rfcomm.py) | The Bluetooth link, with connecting done off the main loop. |
| [`divoom_pi/bluez.py`](divoom_pi/bluez.py) | Scanning and pairing, via `bluetoothctl`. |
| [`divoom_pi/mdns.py`](divoom_pi/mdns.py) | The Avahi records that make Home Assistant notice. |
| [`divoom_pi/cli.py`](divoom_pi/cli.py) | `run`, `scan`, `pair`, `selftest`, `doctor`. |
| [`custom_components/divoom/`](custom_components/divoom) | The Home Assistant integration HACS installs. |
| [`install.sh`](install.sh) | The one-command Pi installer. |

Run the tests with:

```bash
python3 -m unittest discover -s tests -v
```

They cover the protocol and the whole TCP side of the gateway with Bluetooth faked out, so they run
anywhere. The Bluetooth half needs a real Divoom: that is what `divoom-pi selftest` is for.

The integration has its own checks, which fake Home Assistant rather than installing it:

```bash
python3 tests/integration/config_flow_check.py .
python3 tests/integration/clock_check.py .
```

They import the real config flow, diagnostics and entities and run them. That is weaker than
testing inside Home Assistant, but it catches the errors that matter here.

## Credits

This project stands on [@d03n3rfr1tz3](https://github.com/d03n3rfr1tz3)'s work:

- [hass-divoom](https://github.com/d03n3rfr1tz3/hass-divoom): the Home Assistant integration that
  does all the interesting work, and the source of the Divoom protocol details used here.
- [esp32-divoom](https://github.com/d03n3rfr1tz3/esp32-divoom): the ESP32 gateway firmware whose
  TCP protocol divoom-pi reimplements.

The integration here is derived from hass-divoom by way of
[adman234/divoom-gateway](https://github.com/adman234/divoom-gateway), which remains the home of the
ESP32 and ESPHome gateway firmware. See [UPSTREAM.md](UPSTREAM.md) for what changed and how to merge
upstream fixes back in.

## Licence

MIT. See [LICENSE](LICENSE).
