"""Drive the clock style, colour and brightness entities with HA stubbed.

The clock's style and text colour travel in one "set view" command, so the
style select and the clock light have to agree about what the other one set.
This checks that they do, and that brightness reaches the device as a scaled
colour, since the device has no separate brightness for the clock digits.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ha_stubs  # noqa: E402  (installs the fake homeassistant modules)

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(REPO))

from custom_components.divoom import light as light_module  # noqa: E402
from custom_components.divoom import select as select_module  # noqa: E402
from custom_components.divoom.const import CLOCK_STYLES  # noqa: E402
from custom_components.divoom.hub import DivoomHub  # noqa: E402
from custom_components.divoom.services import tristate  # noqa: E402

MAC = "b1:21:81:bf:a8:eb"
checks = []


def check(name, condition, detail=""):
    checks.append(condition)
    print("[%s] %s%s" % (" ok " if condition else "FAIL", name, "  " + detail if detail else ""))


def make_hub():
    """A hub whose device calls are recorded instead of sent."""
    hub = DivoomHub(ha_stubs.FakeHass(None), None, None, MAC, 2, False, name="Divoom Ditoo")
    hub.calls = []

    async def record(command, *args, **kwargs):
        hub.calls.append((command, args, kwargs))
        return None

    hub.async_execute = record
    return hub


def attach(entity):
    entity.async_write_ha_state = lambda: None
    return entity


async def main():
    # -- the corrected style map --------------------------------------------
    check("fullscreen is index 0", CLOCK_STYLES["Fullscreen"] == 0)
    check("fullscreen negative is index 1", CLOCK_STYLES["Fullscreen negative"] == 1,
          "was 4 before")
    check("rainbow is index 5", CLOCK_STYLES["Rainbow"] == 5, "was 1 before")
    check("green drips is index 6", CLOCK_STYLES["Green drips"] == 6)
    check("every index 0-6 is reachable once", sorted(CLOCK_STYLES.values()) == list(range(7)),
          str(sorted(CLOCK_STYLES.values())))
    check("visualisers are labelled as such",
          len([name for name in CLOCK_STYLES if name.startswith("Visualizer:")]) == 3)

    # -- clock colour and brightness ----------------------------------------
    hub = make_hub()
    entity = attach(light_module.DivoomClockLight(hub))

    await entity.async_turn_on(rgb_color=(255, 0, 0))
    command, _args, kwargs = hub.calls[-1]
    check("turning the clock on sends show_clock", command == "show_clock")
    check("full brightness passes the colour through", kwargs["color"] == [255, 0, 0],
          str(kwargs["color"]))
    check("the clock light reports on", entity._attr_is_on is True)

    await entity.async_turn_on(brightness=128)
    kwargs = hub.calls[-1][2]
    check("brightness scales the colour", kwargs["color"] == [128, 0, 0], str(kwargs["color"]))
    check("the colour itself is remembered", hub.clock_color == [255, 0, 0])

    await entity.async_turn_on(brightness=0)
    check("zero brightness goes black, not off", hub.calls[-1][2]["color"] == [0, 0, 0])

    await entity.async_turn_off()
    check("turning it off deactivates the clock face", hub.calls[-1][2]["clock"] == -1,
          str(hub.calls[-1][2]))
    check("the clock light reports off", entity._attr_is_on is False)

    # -- style and colour survive each other --------------------------------
    hub = make_hub()
    clock_light = attach(light_module.DivoomClockLight(hub))
    style_select = attach(select_module.DivoomClockStyleSelect(hub))

    await clock_light.async_turn_on(rgb_color=(0, 255, 0), brightness=255)
    await style_select.async_select_option("Rainbow")
    command, _args, kwargs = hub.calls[-1]
    check("selecting a style keeps the colour", kwargs["color"] == [0, 255, 0], str(kwargs))
    check("selecting a style sends the right index", kwargs["clock"] == 5, str(kwargs["clock"]))

    await clock_light.async_turn_on(rgb_color=(0, 0, 255))
    kwargs = hub.calls[-1][2]
    check("changing the colour keeps the style", kwargs["clock"] == 5, str(kwargs["clock"]))

    await style_select.async_select_option("Visualizer: boxed")
    check("a visualiser style is sent as index 4", hub.calls[-1][2]["clock"] == 4)

    # -- the channel select goes through the same state ----------------------
    channel = attach(select_module.DivoomChannelSelect(hub))
    await channel.async_select_option("clock")
    command, _args, kwargs = hub.calls[-1]
    check("switching to the clock channel keeps style and colour",
          command == "show_clock" and kwargs["clock"] == 4 and kwargs["color"] == [0, 0, 255],
          str(kwargs))

    # -- three-state service fields ------------------------------------------
    check("'off' means off", tristate("off") is False)
    check("'on' means on", tristate("on") is True)
    check("unset stays unset", tristate(None) is None)
    check("booleans still work", tristate(False) is False and tristate(True) is True)
    check("nonsense is treated as unset", tristate("maybe") is None)

    # -- an untouched clock sends no colour at all ---------------------------
    hub = make_hub()
    await hub.async_apply_clock()
    check("an untouched clock sends no colour", hub.calls[-1][2]["color"] is None)

    failed = len([c for c in checks if not c])
    print("\n%d checks, %d failed" % (len(checks), failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
