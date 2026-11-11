# Upstream

`custom_components/divoom` is derived from
[d03n3rfr1tz3/hass-divoom](https://github.com/d03n3rfr1tz3/hass-divoom) by Dirk
Sarodnick, MIT licensed. The Divoom device protocol in `devices/` is his work,
and it is the reason this project exists at all.

It first arrived here by way of
[adman234/divoom-gateway](https://github.com/adman234/divoom-gateway), a fork of
hass-divoom v1.2.4 that replaced the notify-only integration with a real Home
Assistant device (light, channel and clock-style selects, brightness and volume,
buttons). That copy had no shared git history with upstream, so every upstream
fix had to be carried over by hand.

Since the merge of `hassdivoom/main` at `0e9959a`, upstream is a real git
ancestor of this repository. Pulling in its fixes is an ordinary merge.

## Syncing with upstream

```bash
git remote add hassdivoom https://github.com/d03n3rfr1tz3/hass-divoom   # once
git fetch hassdivoom
git checkout -b upstream-sync
git merge hassdivoom/main
```

Then resolve conflicts using the decisions below, and run the checks CI runs:

```bash
python -m unittest discover -s tests -v
python tests/integration/config_flow_check.py .
python tests/integration/clock_check.py .
```

Upstream also keeps its own tests, workflows and repository files outside
`custom_components`. Those stay out of this repository: on each merge, delete
anything new under `.github/` and `tests/` that came from upstream (`git rm`
it before committing the merge). This repository's own `tests/` hold the
gateway tests and the stubbed Home Assistant checks.

## What is upstream's

Take upstream's version of these files on every merge. They are the parts that
upstream maintains and this fork has no reason to diverge from.

- `devices/`: the protocol for every model, including the newer Divoom128
  family (FlowToo, MiniToo, Tiivoo 2). One local change is carried on top; see
  below.
- `notify.py`: the notify service and its connection handling. It owns the one
  connection per device that everything else shares.
- `services.py`, `services.yaml`: every Divoom action. The 23 actions this fork
  used to define all have upstream equivalents, so the fork's versions were
  dropped.
- `__init__.py`, `migration.py`, `repairs.py`: setup, entry migrations and
  repair issues. `__init__.py` has a small local addition; see below.
- `config_flow.py`: the setup and reconfigure flows, including the
  reconfigure menu (direct Bluetooth or gateway). Small local additions; see
  below.
- `brand/`, `strings.json`, `translations/`: icons and all user-facing text.
- `manifest.json`: matchers and dependencies, except the identity fields
  listed below.

## What is this fork's

These have no upstream counterpart, or deliberately differ.

- **Entities**: `light.py`, `select.py`, `number.py`, `button.py`, `entity.py`.
  Upstream is notify-only; these give the device a light, a clock light,
  channel and clock-style selects, brightness and volume, and buttons.
- **`hub.py`**: the bridge between the entities and upstream's connection.
  `DivoomHub` does not open a socket of its own. It looks up the notify
  service in `hass.data[DOMAIN]["loaded"][mac]`, takes that service's lock and
  calls the same device object, so the entities and the notify/service actions
  never fight over one RFCOMM connection. If the notify service is not loaded
  yet, entity commands fail with a clear error instead of connecting
  separately.
- **`__init__.py` addition**: creates the hub before the notify platform loads,
  and forwards and unloads the entity platforms (`ENTITY_PLATFORMS` in
  `const.py`) next to upstream's.
- **`diagnostics.py`**: states whether a device is reached through a gateway
  or through the Home Assistant host's own Bluetooth adapter, and whether its
  notify service is loaded.
- **`const.py` additions**: on top of upstream's constants, the entity
  capability table (`DEVICE_CAPABILITIES`, `DEVICE_MODEL_NAMES`, the
  `CHANNEL_*` values) and `CLOCK_STYLES`, the clock style indices corrected
  by hand against a real Ditoo Pro. `CLOCK_ID_DEVICE_TYPES` lists the
  Divoom128 models, whose `show_clock` takes a clock face id and ignores style
  and colour, so the clock light and clock style select are not created for
  them and their channel select has no clock option.
- **`config_flow.py` additions**:
  - zeroconf adopts an announcing gateway for an existing entry that has no
    host (and clears its local adapter), instead of aborting as already
    configured forever;
  - gateway hosts typed during setup or reconfigure go through
    `hub.clean_host`, so a pasted `http://host/` URL becomes a bare host.
- **`devices/divoom.py`, `reconnect()`**: only errno `0` (or none) counts as
  success. Upstream tests `<= 0`, which treats `socket.gaierror` (negative
  errno, `-2` on Linux) as a successful connection, so a gateway hostname that
  does not resolve fails silently. The failure is also logged with the host
  and a hint to check name resolution. Re-apply this if a merge conflicts
  there, unless upstream has fixed it.
- **`manifest.json` identity fields**: `documentation`, `issue_tracker` and
  `version` point at this repository, and `@adman234` is added to
  `codeowners`. The pillow and zstandard requirements follow upstream.
- **Repository files**: `README.md`, `LICENSE`, `hacs.json`, `.gitignore` and
  everything outside `custom_components` (the gateway, installer, docs, CI)
  are this repository's.

## Behaviour changes from the merge

For anyone upgrading from a version before the merge:

- Entities and the notify service now share one connection per device.
  Previously the hub opened a second connection of its own.
- Reconfigure first asks whether the device is reached directly over
  Bluetooth or through a gateway, then shows that form.
- The service actions are upstream's, and **their names changed**. Automations
  and scripts that call the old names need updating:

| Before the merge | After |
| --- | --- |
| `divoom.game_control` | `divoom.gamecontrol` |
| `divoom.play_game` | `divoom.game` |
| `divoom.send_raw` | `divoom.raw` |
| `divoom.set_alarm` | `divoom.alarm` |
| `divoom.set_datetime` | `divoom.datetime` |
| `divoom.set_keyboard` | `divoom.keyboard` |
| `divoom.set_memorial` | `divoom.memorial` |
| `divoom.set_playstate` | `divoom.playstate` |
| `divoom.set_weather` | `divoom.weather` |
| `divoom.show_clock` | `divoom.clock` |
| `divoom.show_countdown` | `divoom.countdown` |
| `divoom.show_design` | `divoom.design` |
| `divoom.show_effects` | `divoom.effects` |
| `divoom.show_equalizer` | `divoom.equalizer` |
| `divoom.show_image` | `divoom.image` |
| `divoom.show_lyrics` | `divoom.lyrics` |
| `divoom.show_noise` | `divoom.noise` |
| `divoom.show_radio` | `divoom.radio` |
| `divoom.show_scoreboard` | `divoom.scoreboard` |
| `divoom.show_sleep` | `divoom.sleep` |
| `divoom.show_temperature` | `divoom.temperature` |
| `divoom.show_text` | `divoom.text` |
| `divoom.show_timer` | `divoom.timer` |
| `divoom.show_visualization` | `divoom.visualization` |

  Upstream also adds `brightness`, `volume`, `light`, `signal`, `connect` and
  `disconnect`. Some fields follow upstream's schema now: for example, the
  clock fields that used to accept a third "keep" value (`twentyfour`, `hot`)
  are plain booleans.
