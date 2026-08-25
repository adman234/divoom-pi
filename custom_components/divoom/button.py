"""Button platform for Divoom one-shot actions."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
    """Set up the Divoom buttons from a config entry."""
    hub: DivoomHub = hass.data[DOMAIN]["hubs"][entry.entry_id]

    entities = [DivoomDatetimeButton(hub)]
    if DEVICE_CAPABILITIES.get(hub.device_type, {}).get("keyboard"):
        entities.append(DivoomKeyboardButton(hub, "Keyboard light", 0, "mdi:keyboard"))
        entities.append(DivoomKeyboardButton(hub, "Keyboard next effect", 1, "mdi:skip-next"))
        entities.append(DivoomKeyboardButton(hub, "Keyboard previous effect", -1, "mdi:skip-previous"))

    async_add_entities(entities)


class DivoomDatetimeButton(DivoomEntity, ButtonEntity):
    """Synchronizes the device clock to the current Home Assistant time."""

    _attr_name = "Sync time"
    _attr_icon = "mdi:clock-check"

    def __init__(self, hub: DivoomHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.mac}-sync-time"

    async def async_press(self) -> None:
        await self._hub.async_execute("send_datetime", value=None)


class DivoomKeyboardButton(DivoomEntity, ButtonEntity):
    """Controls the keyboard LEDs of a Ditoo."""

    def __init__(self, hub: DivoomHub, name: str, value: int, icon: str) -> None:
        super().__init__(hub)
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{hub.mac}-keyboard-{value}"
        self._value = value

    async def async_press(self) -> None:
        await self._hub.async_execute("send_keyboard", value=self._value)
