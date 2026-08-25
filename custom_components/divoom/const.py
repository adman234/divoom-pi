from typing import Final
from homeassistant.const import Platform

# notify is set up via discovery (legacy platform), the rest are real entity platforms
PLATFORMS = [Platform.NOTIFY]
ENTITY_PLATFORMS = [Platform.LIGHT, Platform.SELECT, Platform.NUMBER, Platform.BUTTON]

DOMAIN: Final = "divoom"

CONF_DEVICE_TYPE: Final = 'device_type'
CONF_MEDIA_DIR: Final = 'media_directory'
CONF_MEDIA_DIR_DEFAULT: Final = "pixelart"
CONF_ESCAPE_PAYLOAD: Final = 'escape_payload'

# per device type feature flags, used to decide which entities get created
DEVICE_CAPABILITIES: Final = {
    "aurabox":     {"audio": False, "keyboard": False, "lyrics": False},
    "backpack":    {"audio": False, "keyboard": False, "lyrics": False},
    "ditoo":       {"audio": True,  "keyboard": True,  "lyrics": True},
    "ditoomic":    {"audio": True,  "keyboard": True,  "lyrics": True},
    "pixoo":       {"audio": False, "keyboard": False, "lyrics": False},
    "pixoomax":    {"audio": False, "keyboard": False, "lyrics": False},
    "timebox":     {"audio": True,  "keyboard": False, "lyrics": False},
    "timeboxmini": {"audio": True,  "keyboard": False, "lyrics": False},
    "timoo":       {"audio": True,  "keyboard": False, "lyrics": True},
    "tivoo":       {"audio": True,  "keyboard": False, "lyrics": True},
}

DEVICE_MODEL_NAMES: Final = {
    "aurabox": "Aurabox",
    "backpack": "Backpack",
    "ditoo": "Ditoo",
    "ditoomic": "Ditoo Mic",
    "pixoo": "Pixoo",
    "pixoomax": "Pixoo Max",
    "timebox": "Timebox",
    "timeboxmini": "Timebox Mini",
    "timoo": "Timoo",
    "tivoo": "Tivoo",
}

# channels selectable through the channel select entity
CHANNEL_CLOCK: Final = "clock"
CHANNEL_LIGHT: Final = "light"
CHANNEL_EFFECTS: Final = "effects"
CHANNEL_VISUALIZATION: Final = "visualization"
CHANNEL_DESIGN: Final = "design"
CHANNEL_LYRICS: Final = "lyrics"

# Clock styles selectable through the clock style select entity.
#
# Checked by hand against a Ditoo Pro, because the inherited names did not
# describe what the device actually renders: index 1 is the negative
# fullscreen face rather than the rainbow one, index 5 is the rainbow one,
# and indices 2-4 are not clock faces at all - they are music visualisers.
# Listed here as clock faces first, then visualisers. Other models may map
# these indices differently; this is what a Ditoo Pro does.
CLOCK_STYLES: Final = {
    "Fullscreen": 0,
    "Fullscreen negative": 1,
    "Rainbow": 5,
    "Green drips": 6,
    "Visualizer: green sides": 2,
    "Visualizer: rainbow bars": 3,
    "Visualizer: boxed": 4,
}
