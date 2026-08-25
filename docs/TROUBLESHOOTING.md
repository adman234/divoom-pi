# Troubleshooting

Start with:

```bash
divoom-pi doctor
```

It checks Bluetooth, Avahi, the systemd units and the gateway port, and prints the configuration it
is actually using. Most problems show up there.

The other thing to have open, in a second SSH session, is the log:

```bash
journalctl -u divoom-pi -f
```

Setting `log_level = DEBUG` in `/etc/divoom-pi/config.ini` (then
`sudo systemctl restart divoom-pi`) logs every byte in both directions, which answers most "is it
even getting that far?" questions.

---

## `divoom-pi scan` finds nothing

**The Divoom is connected to your phone.** This is the most common cause by a wide margin. Divoom
devices accept one Bluetooth connection at a time, and the Divoom app holds it. Close the app,
disconnect the device in your phone's Bluetooth settings, or turn Bluetooth off on the phone.

**The adapter is down or blocked:**

```bash
sudo rfkill unblock bluetooth
sudo systemctl restart bluetooth
sudo bluetoothctl power on
```

**Nothing at all is found, not even other devices.** Then it is the Pi's Bluetooth, not the Divoom.
`divoom-pi doctor` will show whether a controller exists and is powered. On Raspberry Pi OS, check
that nothing has disabled the on-board Bluetooth in `/boot/firmware/config.txt` (older images:
`/boot/config.txt`) — a `dtoverlay=disable-bt` line does exactly that.

## The device is found, but connecting fails

**Wrong RFCOMM channel.** The channel is per model: 2 for Ditoo, Ditoo Mic and Timoo, 4 for Aurabox
and Timebox Mini, 1 for everything else. A wrong channel usually fails immediately with
"Connection refused". Try:

```bash
divoom-pi selftest <MAC> --channel 2 --direct
```

**It needs pairing:**

```bash
divoom-pi pair <MAC>
```

**It needs a PIN.** Some older models (Aurabox in particular) use legacy pairing with a fixed PIN,
usually `0000`. That needs an interactive agent, so do it by hand once:

```bash
bluetoothctl
```

```
[bluetooth]# agent KeyboardOnly
[bluetooth]# default-agent
[bluetooth]# scan on
[bluetooth]# pair AA:BB:CC:DD:EE:FF
        (enter 0000 when prompted)
[bluetooth]# trust AA:BB:CC:DD:EE:FF
[bluetooth]# quit
```

After that divoom-pi connects without any further help.

**It connects, then drops.** Something else is grabbing the device — a phone reconnecting
automatically is the usual culprit. Forget the Pi's Divoom on the phone, or leave phone Bluetooth
off while testing.

## Home Assistant never discovers it

**Check the record was published:**

```bash
ls /etc/avahi/services/divoom-pi-*.service
```

Nothing there means no Divoom has been discovered yet — go back to `divoom-pi scan`. Discovery runs
5 seconds after startup and then every 60 seconds, backing off to every 15 minutes once it stops
finding anything new, so give it a moment after a restart.

**Check Home Assistant can see it.** From the Home Assistant host:

```bash
avahi-browse -rt _divoom_esp32._tcp
```

If the Pi's record does not appear there, mDNS is not crossing your network. That usually means
Home Assistant and the Pi are on different subnets or VLANs, or a router is not forwarding
multicast, or Home Assistant is in Docker with a bridge network instead of host networking. In any
of those cases, **add the device by hand instead** — discovery is a convenience, and manual setup
works identically:

*Settings → Devices & Services → Add Integration → Divoom*, then enter the Divoom's MAC address,
the RFCOMM channel, and the Pi's IP address as the host.

**Check the integration is installed.** Discovery only fires if Home Assistant has the `divoom`
custom integration; without it, the mDNS record means nothing to Home Assistant. See the README.

## Home Assistant shows the device but nothing happens

Watch the gateway log while pressing the button in Home Assistant:

```bash
journalctl -u divoom-pi -f
```

**No client connection appears at all.** Home Assistant is not reaching the Pi. Confirm it can:

```bash
nc -vz <pi-address> 7777
```

from the Home Assistant host. If the port is open but no connection is logged, the integration is
not attempting one — check for a duplicate Divoom config entry in Home Assistant (repeated
discovery can leave more than one), and check that the entry's *host* is set. A Divoom entry with
no host configured talks to Bluetooth directly from the Home Assistant machine and never contacts
the Pi at all.

Home Assistant's own log is the other half of this:

```yaml
logger:
  logs:
    custom_components.divoom: debug
```

**A connection appears, and the log shows `no Bluetooth connection`.** The TCP side is fine and the
Bluetooth side is not — go back to the connection section above, and confirm with
`divoom-pi selftest <MAC> --channel N`.

**A connection appears and bytes flow, but the device ignores them.** Almost always the wrong
device type in the Home Assistant config entry: the command set differs per model. Remove the entry
and add it again with the right type.

## The display goes dark instead of lighting up

`--action ha-on`, and Home Assistant's own light "on", send `show_light(color=[1,1,1])` - RGB
(1, 1, 1), which is all but black. The device is in light mode and lit; it is showing you almost
no light. Nothing is broken.

Use `divoom-pi selftest <MAC> --channel N --action on` for a white test (add `--color ff8800` for
something else), and `--action clock` to put the display back to its clock face.

## The selftest says nothing came back

Most Divoom commands are not acknowledged at all, so for `on`, `off` and `clock` an empty reply is
expected - the display is the real result. `--action ping` asks for the current view, which the
device does answer, and is the one to use when you want proof the round trip works.

If the reply is a single `0x69`, the Bluetooth connect had not finished yet; run it again. A single
`0x96` means there is no Bluetooth connection at all.

## Everything worked, then stopped

```bash
systemctl status divoom-pi
journalctl -u divoom-pi -n 100
```

The service restarts itself on failure, so a crash loop shows up as repeated startup lines. If the
Bluetooth link drops repeatedly under load, try limiting how much is written to the device at once:

```ini
[bluetooth]
write_chunk = 200
write_delay = 0.01
```

That is off by default because it is not normally needed.

## Reporting a problem

Useful to include:

```bash
divoom-pi doctor
journalctl -u divoom-pi -n 100 --no-pager
bluetoothctl show
```

plus the Divoom model, the Pi model, and the Raspberry Pi OS version (`cat /etc/os-release`).
