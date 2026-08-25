"""Configuration: an optional INI file, with every value overridable on the CLI."""

from __future__ import annotations

import configparser
import os
from typing import Dict, List, Optional, Tuple

DEFAULT_CONFIG_PATH = "/etc/divoom-pi/config.ini"


class Config(object):
    """Everything divoom-pi can be told to do differently."""

    # (section, key, attribute, kind)
    SPEC = [
        ("gateway", "listen", "listen", "str"),
        ("gateway", "port", "port", "int"),
        ("gateway", "max_clients", "max_clients", "int"),
        ("gateway", "log_level", "log_level", "str"),
        ("bluetooth", "connect_timeout", "connect_timeout", "float"),
        ("bluetooth", "read_timeout", "read_timeout", "float"),
        ("bluetooth", "write_chunk", "write_chunk", "int"),
        ("bluetooth", "write_delay", "write_delay", "float"),
        ("bluetooth", "auto_pair", "auto_pair", "bool"),
        ("bluetooth", "bluetoothctl", "bluetoothctl", "str"),
        ("discovery", "enabled", "scan_enabled", "bool"),
        ("discovery", "filter", "filter_names", "bool"),
        ("discovery", "initial_delay", "scan_initial_delay", "float"),
        ("discovery", "interval", "scan_interval", "float"),
        ("discovery", "idle_interval", "scan_idle_interval", "float"),
        ("discovery", "duration", "scan_duration", "int"),
        ("discovery", "while_connected", "scan_while_connected", "bool"),
        ("mdns", "enabled", "mdns_enabled", "bool"),
        ("mdns", "service_dir", "mdns_service_dir", "str"),
    ]

    def __init__(self):
        # Where Home Assistant connects. Port 7777 is hard-coded in the
        # integration's Divoom.connect(), so changing it only makes sense
        # alongside a port-forward or a patched integration.
        self.listen = "0.0.0.0"
        self.port = 7777
        self.max_clients = 4
        self.log_level = "INFO"

        # Bluetooth Classic / RFCOMM.
        self.connect_timeout = 15.0
        self.read_timeout = 1.0
        # 0 = write each message to the device in one go. Raise this only if a
        # device turns out to choke on large image/animation frames.
        self.write_chunk = 0
        self.write_delay = 0.0
        self.auto_pair = True
        self.bluetoothctl = "bluetoothctl"

        # Discovery. The Pi Zero W shares one antenna between WiFi and
        # Bluetooth, so scanning is deliberately unhurried: an inquiry scan
        # while Home Assistant is pushing an animation is exactly the kind of
        # radio contention that made the ESP32 build flaky.
        self.scan_enabled = True
        self.filter_names = True
        self.scan_initial_delay = 5.0
        self.scan_interval = 60.0
        # Backed off to once a round finds nothing new - discovery only has to
        # succeed once for Home Assistant to create the device.
        self.scan_idle_interval = 900.0
        self.scan_duration = 12
        self.scan_while_connected = False

        # mDNS.
        self.mdns_enabled = True
        self.mdns_service_dir = "/etc/avahi/services"

        self.path: Optional[str] = None
        self.warnings: List[str] = []

    # -- loading -------------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[str] = None, required: bool = False) -> "Config":
        config = cls()
        if path is None:
            path = os.environ.get("DIVOOM_PI_CONFIG", DEFAULT_CONFIG_PATH)
        if not os.path.isfile(path):
            if required:
                raise FileNotFoundError(path)
            return config

        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        config.path = path

        known: Dict[str, set] = {}
        for section, key, attribute, kind in cls.SPEC:
            known.setdefault(section, set()).add(key)
            if not parser.has_option(section, key):
                continue
            try:
                setattr(config, attribute, _coerce(parser, section, key, kind))
            except ValueError as err:
                config.warnings.append("[%s] %s: %s" % (section, key, err))

        for section in parser.sections():
            if section not in known:
                config.warnings.append("unknown section [%s]" % section)
                continue
            for key in parser.options(section):
                if key not in known[section]:
                    config.warnings.append("unknown option [%s] %s" % (section, key))

        config.validate()
        return config

    def validate(self) -> None:
        if not 1 <= self.port <= 65535:
            self.warnings.append("port %d out of range, using 7777" % self.port)
            self.port = 7777
        if self.scan_duration < 1:
            self.warnings.append("discovery duration must be at least 1s, using 12")
            self.scan_duration = 12
        if self.scan_interval < self.scan_duration:
            self.warnings.append(
                "discovery interval (%.0fs) is shorter than a scan (%ds), raising it"
                % (self.scan_interval, self.scan_duration)
            )
            self.scan_interval = float(self.scan_duration)
        if self.scan_idle_interval < self.scan_interval:
            self.scan_idle_interval = self.scan_interval
        if self.max_clients < 1:
            self.max_clients = 1

    def describe(self) -> List[Tuple[str, str]]:
        rows = [("config file", self.path or "(none, using defaults)")]
        for section, key, attribute, _kind in self.SPEC:
            rows.append(("%s.%s" % (section, key), str(getattr(self, attribute))))
        return rows


def _coerce(parser: configparser.ConfigParser, section: str, key: str, kind: str):
    if kind == "int":
        return parser.getint(section, key)
    if kind == "float":
        return parser.getfloat(section, key)
    if kind == "bool":
        return parser.getboolean(section, key)
    return parser.get(section, key).strip()
