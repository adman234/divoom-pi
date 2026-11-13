"""Base entity for the Divoom integration."""
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DEVICE_MODEL_NAMES, DOMAIN
from .hub import DivoomHub

# HA 2026.x replaced DeviceInfo's via_device with via_device_id (removed in 2027.8).
# Older cores only know via_device, so pick whichever this core understands.
_VIA_KEY = (
    "via_device_id"
    if "via_device_id" in getattr(DeviceInfo, "__annotations__", {})
    else "via_device"
)


class DivoomEntity(Entity):
    """Common base for all Divoom entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: DivoomHub) -> None:
        self._hub = hub
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.mac)},
            connections={(CONNECTION_BLUETOOTH, hub.mac)},
            name=hub.name,
            manufacturer="Divoom",
            model=DEVICE_MODEL_NAMES.get(hub.device_type, hub.device_type),
            **{_VIA_KEY: None},
        )
