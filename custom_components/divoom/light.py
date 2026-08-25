"""Light platform exposing the Divoom light channel."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import DivoomEntity
from .hub import DivoomHub

_LOGGER = logging.getLogger(__package__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Divoom light from a config entry."""
    hub: DivoomHub = hass.data[DOMAIN]["hubs"][entry.entry_id]
    async_add_entities([DivoomLight(hub), DivoomClockLight(hub)])


class DivoomLight(DivoomEntity, LightEntity):
    """The light channel of a Divoom device.

    The protocol is write-only, so the state is optimistic.
    """

    _attr_name = None  # main feature of the device: use the device name
    _attr_assumed_state = True
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(self, hub: DivoomHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.mac}-light"
        self._attr_is_on = False
        self._attr_brightness = 255
        self._attr_rgb_color = (255, 255, 255)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
        if ATTR_RGB_COLOR in kwargs:
            self._attr_rgb_color = kwargs[ATTR_RGB_COLOR]

        brightness = int(round(self._attr_brightness * 100 / 255))
        color = list(self._attr_rgb_color)

        await self._hub.async_execute("show_light", color=color, brightness=brightness, power=True)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._hub.async_execute("send_off")
        self._attr_is_on = False
        self.async_write_ha_state()


class DivoomClockLight(DivoomEntity, LightEntity):
    """The colour and brightness of the clock face.

    The device's own brightness control only affects the effects drawn around
    the clock - the digits keep their own colour and stay as bright as they
    were. The clock's colour is carried in the same "set view" command as its
    style, though, so this entity sets it there, and applies brightness by
    scaling that colour, which is the only control the protocol offers over how
    bright the digits look.

    Turning it off deactivates the clock face rather than switching the display
    off: show_clock() reads an out-of-range style as "clock deactivated".
    """

    _attr_name = "Clock"
    _attr_assumed_state = True
    _attr_icon = "mdi:clock-digital"
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(self, hub: DivoomHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.mac}-clock"
        self._attr_is_on = False
        self._attr_brightness = 255
        self._attr_rgb_color = (255, 255, 255)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
        if ATTR_RGB_COLOR in kwargs:
            self._attr_rgb_color = kwargs[ATTR_RGB_COLOR]

        self._hub.clock_color = list(self._attr_rgb_color)
        self._hub.clock_brightness = self._attr_brightness
        self._hub.clock_enabled = True

        await self._hub.async_apply_clock()
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._hub.clock_enabled = False
        await self._hub.async_apply_clock()
        self._attr_is_on = False
        self.async_write_ha_state()
