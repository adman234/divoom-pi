"""Announce discovered Divoom devices over mDNS so Home Assistant finds them.

Home Assistant's `divoom` integration has a zeroconf matcher for
`_divoom_esp32._tcp.local.` with a `device_name` TXT record; when it sees one it
opens a discovery flow pre-filled with the gateway's host and the Divoom's MAC.
Publishing the same service from the Pi is what makes the device show up in
Home Assistant on its own, with no manual configuration.

The records are published by dropping service files into Avahi's watched
directory rather than by running a second mDNS responder in-process. Avahi is
already running on Raspberry Pi OS (it is what makes `raspberrypi.local` work),
it picks the files up immediately, and it keeps divoom-pi dependency-free.
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional
from xml.sax.saxutils import escape

SERVICE_TYPE = "_divoom_esp32._tcp"
DEFAULT_SERVICE_DIR = "/etc/avahi/services"
FILE_PREFIX = "divoom-pi-"

_TEMPLATE = """<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<!-- Written by divoom-pi. Edits are overwritten; change config.ini instead. -->
<service-group>
  <name replace-wildcards="yes">{instance}</name>
  <service>
    <type>{type}</type>
    <port>{port}</port>
    <txt-record>device_mac={mac}</txt-record>
    <txt-record>device_name={name}</txt-record>
    <txt-record>gateway=divoom-pi</txt-record>
  </service>
</service-group>
"""


def _safe_instance(name: str, mac: str) -> str:
    """A readable, unique-per-device mDNS service instance name."""
    cleaned = re.sub(r"[^A-Za-z0-9 _.-]", "", name).strip() or "Divoom"
    return "%s %s on %%h" % (cleaned, mac.replace(":", "")[-6:].upper())


class AvahiPublisher:
    """Publishes one `_divoom_esp32._tcp` record per discovered Divoom device."""

    def __init__(
        self,
        logger,
        port: int,
        service_dir: str = DEFAULT_SERVICE_DIR,
        enabled: bool = True,
    ):
        self._log = logger
        self._port = port
        self._dir = service_dir
        self._enabled = enabled
        self._published: Dict[str, str] = {}
        self._warned = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def available(self) -> Optional[str]:
        """None if publishing will work, otherwise why it won't."""
        if not self._enabled:
            return "disabled in the configuration"
        if not os.path.isdir(self._dir):
            return "%s does not exist (is avahi-daemon installed?)" % self._dir
        if not os.access(self._dir, os.W_OK):
            return "%s is not writable (divoom-pi needs to run as root)" % self._dir
        return None

    def path_for(self, mac: str) -> str:
        return os.path.join(self._dir, FILE_PREFIX + mac.replace(":", "").lower() + ".service")

    def published(self) -> Dict[str, str]:
        return dict(self._published)

    def clear(self) -> int:
        """Remove every service file divoom-pi owns.

        Called at startup so a device that has been given away or renamed does
        not linger as a stale discovery entry forever.
        """
        removed = 0
        if not os.path.isdir(self._dir):
            return removed
        for entry in os.listdir(self._dir):
            if entry.startswith(FILE_PREFIX) and entry.endswith(".service"):
                try:
                    os.remove(os.path.join(self._dir, entry))
                    removed += 1
                except OSError as err:
                    self._log.warning("could not remove %s: %s", entry, err)
        self._published.clear()
        return removed

    def publish(self, mac: str, name: str) -> bool:
        """Write (or refresh) the record for one device. True if it changed."""
        if not self._enabled:
            return False
        reason = self.available()
        if reason is not None:
            if not self._warned:
                self._log.warning("not publishing mDNS records: %s", reason)
                self._warned = True
            return False

        if self._published.get(mac) == name:
            return False

        content = _TEMPLATE.format(
            instance=escape(_safe_instance(name, mac)),
            type=SERVICE_TYPE,
            port=self._port,
            mac=escape(mac),
            name=escape(name),
        )
        path = self.path_for(mac)
        temporary = path + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(temporary, path)
        except OSError as err:
            self._log.error("could not publish mDNS record for %s: %s", mac, err)
            try:
                os.remove(temporary)
            except OSError:
                pass
            return False

        self._published[mac] = name
        self._log.info("published mDNS record for %s (%s) on port %d", name, mac, self._port)
        return True

    def unpublish(self, mac: str) -> bool:
        path = self.path_for(mac)
        self._published.pop(mac, None)
        try:
            os.remove(path)
            return True
        except OSError:
            return False
