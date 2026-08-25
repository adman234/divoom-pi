"""Number platform for Divoom brightness and volume."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import PERCENTAGE
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_CAPABILITIES, DOMAIN
from .entity import DivoomEntity
from .hub import DivoomHub

_LOGGER = logging.getLogger(__package__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Divoom numbers from a config entry."""
    hub: DivoomHub = hass.data[DOMAIN]["hubs"][entry.entry_id]

    entities = [DivoomBrightnessNumber(hub)]
    if DEVICE_CAPABILITIES.get(hub.device_type, {}).get("audio"):
        entities.append(DivoomVolumeNumber(hub))

    async_add_entities(entities)


class DivoomBrightnessNumber(DivoomEntity, NumberEntity):
    """Display brightness of a Divoom device. Optimistic state."""

    _attr_name = "Brightness"
    _attr_assumed_state = True
    _attr_icon = "mdi:brightness-6"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, hub: DivoomHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.mac}-brightness"
        self._attr_native_value = 100

    async def async_set_native_value(self, value: float) -> None:
        await self._hub.async_execute("send_brightness", value=int(value))
        self._attr_native_value = value
        self.async_write_ha_state()


class DivoomVolumeNumber(DivoomEntity, NumberEntity):
    """Speaker volume of a Divoom device with audio features. Optimistic state."""

    _attr_name = "Volume"
    _attr_assumed_state = True
    _attr_icon = "mdi:volume-high"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, hub: DivoomHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.mac}-volume"
        self._attr_native_value = 50

    async def async_set_native_value(self, value: float) -> None:
        await self._hub.async_execute("send_volume", value=int(value))
        self._attr_native_value = value
        self.async_write_ha_state()
