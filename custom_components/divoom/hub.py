"""Shared connection hub for a Divoom device."""
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .devices.divoom import DivoomUnsupportedError

_LOGGER = logging.getLogger(__package__)


def clean_host(value):
    """Normalize a user-entered gateway host: strip scheme, path and whitespace."""
    if not value:
        return None
    value = value.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.strip("/")
    if "/" in value:
        value = value.split("/", 1)[0]
    return value or None


class DivoomHub:
    """Entity-side handle on a Divoom device.

    The connection itself belongs to upstream's notify service, stored in
    hass.data[DOMAIN]['loaded'][mac], which the actions and the notify
    platform use too. The hub borrows that service's protocol instance and
    lock, so there is never more than one Bluetooth/TCP connection per
    device. The protocol classes use blocking sockets, so every call runs in
    the executor while holding the lock.
    """

    def __init__(self, hass: HomeAssistant, mac, device_type, host=None, name=None):
        self.hass = hass
        self.name = name
        self.mac = mac
        self.host = clean_host(host)
        self.device_type = device_type

        # The clock channel is write-only and its style and text colour travel
        # in the same "set view" command, so changing one means resending the
        # other. Entities that touch the clock keep their state here rather
        # than each holding half of it.
        self.clock_style = 0
        self.clock_color = None
        self.clock_brightness = 255
        self.clock_enabled = True

    @property
    def service(self):
        """The notify service that owns the connection, or None before it is loaded."""
        return self.hass.data.get(DOMAIN, {}).get('loaded', {}).get(self.mac)

    @property
    def device(self):
        """The protocol instance, or None when the service is not loaded."""
        service = self.service
        return getattr(service, '_device', None) if service is not None else None

    def execute(self, command, *args, **kwargs):
        """Run a device command while holding the connection lock. Blocking."""
        service = self.service
        device = getattr(service, '_device', None) if service is not None else None
        if device is None:
            raise HomeAssistantError(f"The Divoom device {self.name or self.mac} is not loaded yet")

        target = self.host or self.mac
        with service._lock:
            try:
                return self._execute(device, command, *args, **kwargs)
            except DivoomUnsupportedError as error:
                raise HomeAssistantError(f"The Divoom device does not support this: {error}") from error
            except OSError as error:
                # the connection may be stale (e.g. the gateway restarted):
                # tear it down and retry once before giving up
                _LOGGER.warning("Divoom: connection to %s failed (%s), reconnecting", target, error)
                try:
                    device.disconnect()
                    return self._execute(device, command, *args, **kwargs)
                except OSError as retry_error:
                    raise HomeAssistantError(
                        f"Could not reach the Divoom device via {target}: {retry_error}"
                    ) from retry_error

    def _execute(self, device, command, *args, **kwargs):
        skip_ping = command in ("send_gamecontrol", "send_raw", "send_command")
        if not device.reconnect(skipPing=skip_ping):
            raise HomeAssistantError(f"Could not reach the Divoom device via {self.host or self.mac}")
        return getattr(device, command)(*args, **kwargs)

    async def async_execute(self, command, *args, **kwargs):
        """Run a device command from the event loop."""
        return await self.hass.async_add_executor_job(
            lambda: self.execute(command, *args, **kwargs)
        )

    async def async_apply_clock(self):
        """Send the clock channel with the current style, colour and brightness.

        The device has no separate brightness for the clock text - "set
        brightness" only affects the surrounding effects - so brightness is
        applied by scaling the colour, which is the only lever the protocol
        gives us over how bright the digits appear.
        """
        color = self.clock_color
        if color is not None:
            scale = max(0, min(255, self.clock_brightness)) / 255
            color = [max(0, min(255, int(round(channel * scale)))) for channel in color]

        # show_clock() treats an out-of-range style as "clock deactivated".
        style = self.clock_style if self.clock_enabled else -1
        return await self.async_execute("show_clock", clock=style, color=color)
