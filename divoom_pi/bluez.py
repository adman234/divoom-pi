"""Thin wrapper around `bluetoothctl` for the things sockets can't do.

Scanning, pairing and adapter power go through BlueZ's D-Bus API, and driving
that directly would mean a dbus binding - a compiled dependency, on a board
(Pi Zero W, single-core ARMv6) where "pip install" of anything with C
extensions is a multi-minute affair. `bluetoothctl` ships with BlueZ, is always
present on Raspberry Pi OS, and covers everything we need, so divoom-pi has no
Python dependencies at all outside the standard library.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

# The device names the ESP32 firmware treats as Divoom hardware. Kept
# identical so both gateways discover exactly the same set of devices.
DIVOOM_NAME_TOKENS = (
    "aurabox",
    "timebox",
    "ditoo",
    "pixoo",
    "timoo",
    "tivoo",
    "divoom",
)

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_DEVICE = re.compile(
    r"Device\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})(?:\s+(.*))?$"
)
# `[CHG] Device AA:.. RSSI: -60` and friends carry a property, not a name.
_PROPERTY = re.compile(r"^[A-Za-z][A-Za-z0-9 ]*:\s")


def is_divoom(name: Optional[str]) -> bool:
    """Does this Bluetooth name look like a Divoom device?"""
    if not name:
        return False
    lowered = name.lower()
    return any(token in lowered for token in DIVOOM_NAME_TOKENS)


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


class BlueZ:
    """Runs bluetoothctl and parses what comes back."""

    def __init__(self, logger, executable: str = "bluetoothctl", agent: str = "NoInputNoOutput"):
        self._log = logger
        self._executable = executable
        self._agent = agent
        self._features: Optional[Dict[str, bool]] = None

    # -- plumbing ------------------------------------------------------------

    @property
    def path(self) -> Optional[str]:
        return shutil.which(self._executable)

    def available(self) -> bool:
        return self.path is not None

    def _features_of_bluetoothctl(self) -> Dict[str, bool]:
        """Which command-line flags this BlueZ's bluetoothctl understands.

        `--timeout` landed in BlueZ 5.55 and `--agent` in 5.56; Raspberry Pi OS
        Bullseye ships 5.55 and Bookworm 5.66, so the flags are normally there,
        but degrading gracefully is cheaper than a version comparison.
        """
        if self._features is None:
            helptext = ""
            try:
                result = subprocess.run(
                    [self._executable, "--help"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=10,
                )
                helptext = result.stdout.decode("utf-8", "replace")
            except (OSError, subprocess.SubprocessError) as err:
                self._log.debug("could not query bluetoothctl --help: %s", err)
            self._features = {
                "timeout": "--timeout" in helptext,
                "agent": "--agent" in helptext,
            }
            self._log.debug("bluetoothctl features: %s", self._features)
        return self._features

    def _run(self, args: List[str], timeout: float) -> Tuple[int, str]:
        command = [self._executable] + args
        self._log.debug("running %s", " ".join(command))
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            self._log.warning("%s timed out after %.0fs", " ".join(command), timeout)
            return 124, ""
        except OSError as err:
            self._log.error("could not run %s: %s", self._executable, err)
            return 127, ""
        return result.returncode, strip_ansi(result.stdout.decode("utf-8", "replace"))

    # -- adapter -------------------------------------------------------------

    def power_on(self) -> bool:
        code, output = self._run(["power", "on"], timeout=15)
        ok = code == 0 and "fail" not in output.lower()
        if not ok:
            self._log.warning("could not power on the Bluetooth adapter: %s", output.strip())
        return ok

    def adapter_info(self) -> Dict[str, str]:
        """Parse `bluetoothctl show` into a plain dict."""
        _, output = self._run(["show"], timeout=15)
        info: Dict[str, str] = {}
        for line in output.splitlines():
            line = line.strip()
            match = re.match(r"^Controller\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})", line)
            if match:
                info["Address"] = match.group(1)
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                if key and " " not in key:
                    info[key] = value.strip()
        return info

    # -- discovery -----------------------------------------------------------

    def _parse_devices(self, output: str) -> Dict[str, str]:
        found: Dict[str, str] = {}
        for line in output.splitlines():
            match = _DEVICE.search(line.strip())
            if not match:
                continue
            mac = match.group(1).upper()
            name = (match.group(2) or "").strip()
            if not name or _PROPERTY.match(name):
                # A property-change line, or a device we have no name for yet.
                found.setdefault(mac, "")
                continue
            if name.replace("-", ":").upper() == mac:
                # bluetoothctl falls back to printing the address as the name.
                found.setdefault(mac, "")
                continue
            found[mac] = name
        return found

    def scan(self, duration: int) -> Dict[str, str]:
        """Run an inquiry scan and return {MAC: name} for everything seen.

        Merges the live scan output with BlueZ's device cache, because a device
        that was already known does not necessarily produce a [NEW] line.
        """
        args: List[str] = []
        features = self._features_of_bluetoothctl()
        if features.get("agent"):
            args += ["--agent", self._agent]
        if features.get("timeout"):
            args += ["--timeout", str(int(duration))]
            args += ["scan", "on"]
            code, output = self._run(args, timeout=duration + 20)
            devices = self._parse_devices(output)
        else:
            # Old bluetoothctl: no --timeout, so drive it over stdin instead.
            self._log.debug("bluetoothctl has no --timeout, scanning over stdin")
            devices = self._scan_via_stdin(duration)

        devices.update(
            {mac: name for mac, name in self.known_devices().items() if name or mac not in devices}
        )
        return devices

    def _scan_via_stdin(self, duration: int) -> Dict[str, str]:
        try:
            process = subprocess.Popen(
                [self._executable],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except OSError as err:
            self._log.error("could not start %s: %s", self._executable, err)
            return {}
        script = "agent %s\ndefault-agent\nscan on\n" % self._agent
        try:
            output, _ = process.communicate(script.encode(), timeout=duration)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                output, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                process.kill()
                output = b""
        return self._parse_devices(strip_ansi(output.decode("utf-8", "replace")))

    def known_devices(self) -> Dict[str, str]:
        """Everything currently in BlueZ's device cache (paired or just seen)."""
        _, output = self._run(["devices"], timeout=20)
        return self._parse_devices(output)

    def divoom_devices(self, devices: Dict[str, str]) -> Dict[str, str]:
        return {mac: name for mac, name in devices.items() if is_divoom(name)}

    # -- pairing -------------------------------------------------------------

    def device_info(self, mac: str) -> Dict[str, str]:
        _, output = self._run(["info", mac], timeout=20)
        info: Dict[str, str] = {}
        for line in output.splitlines():
            line = line.strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            if key and " " not in key:
                info[key] = value.strip()
        return info

    def is_paired(self, mac: str) -> bool:
        return self.device_info(mac).get("Paired", "no").lower() == "yes"

    def is_trusted(self, mac: str) -> bool:
        return self.device_info(mac).get("Trusted", "no").lower() == "yes"

    def pair(self, mac: str, timeout: float = 45.0) -> bool:
        """Attempt a just-works pairing. Devices with a fixed PIN need
        `bluetoothctl` run interactively once instead - see the README."""
        args: List[str] = []
        if self._features_of_bluetoothctl().get("agent"):
            args += ["--agent", self._agent]
        args += ["pair", mac]
        code, output = self._run(args, timeout=timeout)
        lowered = output.lower()
        ok = "successful" in lowered or "already exists" in lowered
        if not ok:
            self._log.warning("pairing with %s did not succeed: %s", mac, output.strip() or code)
        return ok

    def trust(self, mac: str) -> bool:
        _, output = self._run(["trust", mac], timeout=20)
        return "trust succeeded" in output.lower()

    def remove(self, mac: str) -> bool:
        _, output = self._run(["remove", mac], timeout=20)
        return "removed" in output.lower()
