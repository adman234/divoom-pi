# Upstream

`custom_components/divoom` is derived from
[d03n3rfr1tz3/hass-divoom](https://github.com/d03n3rfr1tz3/hass-divoom) by Dirk
Sarodnick, MIT licensed. The Divoom device protocol in `devices/` is his work,
and it is the reason this project exists at all.

It arrived here by way of
[adman234/divoom-gateway](https://github.com/adman234/divoom-gateway), a fork
that had already diverged: it replaced the notify-only integration with a real
Home Assistant device (light, channel and clock-style selects, brightness and
volume, buttons) alongside the original notify service.

**Base commit:** `e97373b` of `adman234/divoom-gateway`, itself forked from
`hass-divoom` v1.2.4.

## Changes made here

- `config_flow.py` — a reconfigure step, so the gateway host, RFCOMM channel
  and device type can be changed without deleting the entry; zeroconf now
  adopts an announcing gateway for an entry that has no host instead of
  aborting forever.
- `diagnostics.py` — new; states whether a device is reached through a gateway
  or through the Home Assistant host's own Bluetooth adapter.
- `devices/divoom.py` — `reconnect()` treats any non-zero errno as a failure.
  It tested `> 0`, which silently ignored `socket.gaierror` (negative errno),
  so an unresolvable gateway hostname produced no log line anywhere.
- `const.py` — the clock style indices, corrected against a real Ditoo Pro.
- `light.py`, `hub.py`, `select.py` — a clock entity for the colour and
  brightness of the clock face.

## Merging upstream

Upstream is a live project. To pull its fixes in:

```bash
git remote add hass-divoom https://github.com/d03n3rfr1tz3/hass-divoom
git fetch hass-divoom
git diff e97373b..hass-divoom/main -- custom_components/divoom
```

`devices/` is where upstream's real work happens and where merges are worth
the effort. The platform files here have diverged too far to merge mechanically.
