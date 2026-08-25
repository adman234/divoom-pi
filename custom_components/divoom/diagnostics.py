"""Diagnostics for the Divoom integration.

Exists to answer one question that is otherwise impossible to answer from the
UI: is this device reached through a gateway on the network, or over the
Bluetooth adapter of the Home Assistant host? An entry created by Bluetooth
discovery has no host and takes the second path, which looks identical in every
screen Home Assistant shows.
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PORT
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_TYPE, DOMAIN
from .hub import clean_host

# The gateway firmware and divoom-pi both listen here; the port is not
# configurable on the Home Assistant side.
GATEWAY_PORT = 7777


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    host = clean_host(entry.data.get(CONF_HOST))

    diagnostics: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            # "bluetooth", "zeroconf" or "user" - how this entry came to be,
            # which is usually the explanation for an unexpected host.
            "source": entry.source,
            "unique_id": entry.unique_id,
            "data": dict(entry.data),
        },
        "connects_via": (
            "gateway at {}:{}".format(host, GATEWAY_PORT)
            if host
            else "the Bluetooth adapter of this Home Assistant host (no gateway configured)"
        ),
        "device": {
            "mac": entry.data.get(CONF_MAC),
            "rfcomm_channel": entry.data.get(CONF_PORT),
            "device_type": entry.data.get(CONF_DEVICE_TYPE),
        },
    }

    hub = hass.data.get(DOMAIN, {}).get("hubs", {}).get(entry.entry_id)
    if hub is None:
        diagnostics["hub"] = "not loaded"
        return diagnostics

    device = hub.device
    if device is None:
        # create_device() returns None for an unrecognised device type, and
        # the failure only surfaces later as an AttributeError.
        diagnostics["hub"] = {
            "host": hub.host,
            "device_type": hub.device_type,
            "error": "no protocol implementation for device type {!r}".format(hub.device_type),
        }
        return diagnostics

    diagnostics["hub"] = {
        "host": hub.host,
        "device_type": hub.device_type,
        "protocol_class": type(device).__name__,
        "screensize": getattr(device, "screensize", None),
        "escape_payload": getattr(device, "escapePayload", None),
        "socket_open": getattr(device, "socket", None) is not None,
        # Non-zero means the last operation failed; 696 is this integration's
        # own marker for "the gateway said there is no Bluetooth connection".
        "socket_errno": getattr(device, "socket_errno", None),
    }
    return diagnostics
