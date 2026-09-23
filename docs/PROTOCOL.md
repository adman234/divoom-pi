# The gateway wire protocol

divoom-pi speaks the protocol the ESP32 `divoom-gateway` firmware speaks, byte for byte, so Home
Assistant's `divoom` integration works with either without knowing which it is talking to. This
document is a description of that existing protocol, not a proposal: anything here that looks odd
is odd in the original too, and divoom-pi matches it deliberately.

The reference implementations are `input/tcp.cpp` and `output/bluetooth.cpp` in
[esp32-divoom](https://github.com/d03n3rfr1tz3/esp32-divoom), and `devices/divoom.py` in
[hass-divoom](https://github.com/d03n3rfr1tz3/hass-divoom).

## Transport

TCP, port **7777**. The port is hard-coded in the integration's `Divoom.connect()`:

```python
self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
self.socket.connect((self.host, 7777))
```

so changing it on the gateway only makes sense behind a port forward.

There is one Bluetooth link at a time, shared by every connected TCP client. In practice there is
one client: Home Assistant.

## Client to gateway

| Bytes | Meaning |
| --- | --- |
| `69 <mac:6> <channel:1>` | Connect to that Divoom on that RFCOMM channel |
| `69 <mac:6>` | The same, defaulting to channel 1 |
| `96 <mac:6>` | Disconnect |
| `01 ... 02` | A Divoom payload, relayed to the device untouched |

The MAC is six raw bytes in display order, so `B1:21:81:BF:A8:EB` is `b1 21 81 bf a8 eb`.

The channel is the RFCOMM channel of the device's serial port, and differs per model: 2 for
Ditoo/Ditoo Mic/Timoo, 4 for Aurabox/Timebox Mini, 1 for everything else. The integration picks a
default from the device name and lets you override it during setup.

Everything between `01` and `02` is the Divoom's own protocol and the gateway does not interpret
it. For the record, the integration builds it as:

```
01 | length:2 LE | command | args... | checksum:2 LE | 02
```

where `length` is `len(args) + 3` and `checksum` is the sum of every byte between the framing
markers, widening to four bytes if that sum exceeds 65535.

## Gateway to client

| Bytes | Meaning |
| --- | --- |
| `<anything>` | Relayed verbatim from the Divoom device |
| `69` | A payload was dropped: the Bluetooth connect is still in progress |
| `96` | A payload was dropped: there is no Bluetooth connection |
| `00 <mac:6> <len:1> <name>` | A Divoom device was discovered over Bluetooth |

The two single-byte replies are not cosmetic. The integration's `reconnect()` sends a ping and
looks at the last byte of the answer:

```python
if (self.host != None and not isinstance(ping, int) and list(ping)[-1] == 0x69):
    time.sleep(0.5)
    ping = self.send_ping()          # ... twice more, backing off
if (self.host != None and not isinstance(ping, int) and list(ping)[-1] == 0x96):
    self.socket_errno = 696          # give up and run the reconnect loop
```

So `69` buys the gateway time to finish connecting, and `96` tells Home Assistant to stop waiting.
Getting these wrong looks, from Home Assistant's side, like a device that silently does nothing.

The `00` advertisement is informational; the integration ignores it. divoom-pi sends it after every
scan and to each client as it connects, matching the firmware.

## Framing

The catch: **the payload is not escaped**. The integration has an `escape_payload()` that would
escape `01`, `02` and `03`, but it defaults to off for every device type ("escaping is not needed
anymore as some smarter guys found out"). So a `02` can appear *inside* a payload, and scanning the
stream for the next `02` would cut frames in the wrong place.

The firmware sidesteps this by treating each TCP read as a message boundary: a payload ends at the
first read whose last byte is `02`, and an incomplete tail is carried into the next read. divoom-pi
does the same. This works because the integration sends each message with its own `sendall()`, so
reads and messages line up.

`divoom_pi/protocol.py` implements exactly this, and `tests/test_protocol.py` pins the behaviour
down, including a payload with `02` bytes inside it, messages split across reads, and two messages
arriving in one read.

## Discovery

The gateway scans for Bluetooth devices whose name contains `aurabox`, `timebox`, `ditoo`, `pixoo`,
`timoo`, `tivoo` or `divoom` (the firmware's list, case-insensitively), and publishes each one as an
mDNS service:

```
type:  _divoom_esp32._tcp
port:  7777
txt:   device_mac=B1:21:81:BF:A8:EB
       device_name=DitooPro-Audio
```

The integration's `manifest.json` has a zeroconf matcher for that service type with a `device_name`
property, and `async_step_zeroconf()` reads `device_mac`, `device_name` and the announcing host
straight out of the record. That is what makes the device appear in Home Assistant on its own.

divoom-pi publishes these through Avahi by writing service files into `/etc/avahi/services` rather
than running a second mDNS responder: Avahi is already running on Raspberry Pi OS, and this keeps
divoom-pi free of Python dependencies. It also means one record per device, where the ESP32's
`ESPmDNS` could only ever hold the TXT records of the most recently seen one.
