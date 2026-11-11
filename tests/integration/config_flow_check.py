"""Drive the Divoom config flow, diagnostics and hub with Home Assistant stubbed.

Home Assistant cannot be installed everywhere this repository is checked out,
so this fakes just enough of it to import the real config flow and run both
discovery paths. It cannot prove how Home Assistant itself drives a flow, but
it does catch typos, wrong names and bad call signatures - it caught a change
that had been applied to async_step_bluetooth instead of async_step_zeroconf,
whose body is identical.

It also covers what this fork keeps on top of upstream's flow (zeroconf
adoption, clean_host), the hub that lets the entities share upstream's notify
service connection, and the errno fix in Divoom.reconnect().
"""

import asyncio
import logging
import socket as socket_module
import sys
import threading
import time as time_module
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# Importing this installs the fake homeassistant modules, which has to happen
# before anything under custom_components is imported.
from ha_stubs import AbortFlow, ConfigEntry, FakeHass, HomeAssistantError  # noqa: E402

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(REPO))


from custom_components.divoom import config_flow as cf  # noqa: E402
from custom_components.divoom import diagnostics as diag  # noqa: E402
from custom_components.divoom.devices.divoom import DivoomUnsupportedError  # noqa: E402
from custom_components.divoom.devices.ditoo import Ditoo  # noqa: E402
from custom_components.divoom.hub import DivoomHub  # noqa: E402

MAC = "11:22:33:44:55:66"
PI = "192.168.1.50"
checks = []


def check(name, condition, detail=""):
    checks.append((name, condition, detail))
    print("[%s] %s%s" % (" ok " if condition else "FAIL", name, "  " + detail if detail else ""))


class Discovery:
    def __init__(self, host, properties):
        self.host = host
        self.properties = properties


async def check_flow():
    # -- reconfigure: upstream's menu offers both transports ----------------
    entry = ConfigEntry(data={"mac": MAC, "port": 2, "device_type": "ditoo", "host": None,
                              "adapter": "hci0"})
    flow = cf.DivoomBluetoothConfigFlow()
    flow.hass = FakeHass(entry)
    flow.context = {"entry_id": entry.entry_id}

    result = await flow.async_step_reconfigure()
    check("reconfigure shows a menu", result["type"] == "menu"
          and result["menu_options"] == ["reconfigure_direct", "reconfigure_proxy"])

    result = await flow.async_step_reconfigure_proxy()
    check("reconfigure_proxy shows a form", result["type"] == "form"
          and result["step_id"] == "reconfigure_proxy")
    check("form is prefilled from the entry", flow.suggested_values.get("device_type") == "ditoo"
          and flow.suggested_values.get("port") == "2", str(flow.suggested_values))

    # -- reconfigure: submitting a gateway rewrites and reloads the entry ----
    result = await flow.async_step_reconfigure_proxy(
        {"host": "http://%s/" % PI, "port": "2", "device_type": "ditoo"}
    )
    check("reconfigure aborts with success", result["type"] == "abort"
          and result["reason"] == "reconfigure_successful")
    check("host is normalised by clean_host", entry.data["host"] == PI,
          "got %r" % (entry.data["host"],))
    check("switching to a gateway clears the adapter", entry.data["adapter"] is None)
    check("entry was reloaded", entry.reloaded is True)
    check("unrelated entry data survives", entry.data["mac"] == MAC)

    # -- user setup through a gateway cleans a pasted URL --------------------
    flow = cf.DivoomBluetoothConfigFlow()
    flow.hass = FakeHass(None)
    flow.context = {}
    seen = {}

    async def stop_at_device_type(user_input=None):
        seen["host"] = flow._device_host
        return {"type": "form", "step_id": "device_type"}

    flow.async_step_device_type = stop_at_device_type
    await flow.async_step_proxy({"host": " http://divoom-pi.local:80/x ", "mac": MAC, "port": "0"})
    check("proxy setup cleans the host", seen.get("host") == "divoom-pi.local:80",
          "got %r" % (seen.get("host"),))

    # -- zeroconf: adopts the gateway for a hostless entry -------------------
    hostless = ConfigEntry(data={"mac": MAC, "port": 2, "device_type": "ditoo", "adapter": "hci0"})
    flow = cf.DivoomBluetoothConfigFlow()
    flow.hass = FakeHass(hostless)
    flow._existing_entry = hostless
    try:
        await flow.async_step_zeroconf(
            Discovery(PI, {"device_mac": MAC.upper(), "device_name": "DitooPro-Audio"})
        )
        check("zeroconf aborts once configured", False, "no abort raised")
    except AbortFlow as err:
        check("zeroconf aborts once configured", err.reason == "already_configured")
    check("hostless entry adopts the announcing gateway", hostless.data.get("host") == PI,
          "got %r" % (hostless.data.get("host"),))
    check("adopting clears the local adapter", hostless.data.get("adapter") is None)
    check("adopting reloads the entry", hostless.reloaded is True)

    # -- zeroconf: an entry that already names a gateway is left alone -------
    configured = ConfigEntry(data={"mac": MAC, "host": "10.0.0.9", "port": 2})
    flow = cf.DivoomBluetoothConfigFlow()
    flow.hass = FakeHass(configured)
    flow._existing_entry = configured
    try:
        await flow.async_step_zeroconf(
            Discovery(PI, {"device_mac": MAC.upper(), "device_name": "DitooPro-Audio"})
        )
    except AbortFlow:
        pass
    check("an existing gateway is not overwritten", configured.data["host"] == "10.0.0.9",
          "got %r" % (configured.data["host"],))

    # -- zeroconf: a record with no MAC is rejected, not crashed on ----------
    flow = cf.DivoomBluetoothConfigFlow()
    flow.hass = FakeHass(None)
    result = await flow.async_step_zeroconf(Discovery(PI, {"device_name": "Ditoo"}))
    check("a malformed record aborts", result["type"] == "abort"
          and result["reason"] == "invalid_discovery_info")


async def check_diagnostics():
    gateway_entry = ConfigEntry(data={"mac": MAC, "host": PI, "port": 2, "device_type": "ditoo"})
    hass = FakeHass(gateway_entry)
    report = await diag.async_get_config_entry_diagnostics(hass, gateway_entry)
    check("diagnostics names the gateway", "gateway at %s:7777" % PI == report["connects_via"],
          report["connects_via"])
    check("diagnostics reports the source", report["entry"]["source"] == "bluetooth")
    check("diagnostics handles an unloaded hub", report["hub"] == "not loaded")

    direct_entry = ConfigEntry(data={"mac": MAC, "port": 2, "device_type": "ditoo"})
    report = await diag.async_get_config_entry_diagnostics(FakeHass(direct_entry), direct_entry)
    check("diagnostics calls out direct Bluetooth", "no gateway configured" in report["connects_via"],
          report["connects_via"])

    class FakeDevice:
        screensize = 16
        escapePayload = False
        socket = None
        socket_errno = 0

    class FakeHub:
        host = PI
        device_type = "ditoo"
        service = object()
        device = FakeDevice()

    hass = FakeHass(gateway_entry)
    hass.data = {"divoom": {"hubs": {gateway_entry.entry_id: FakeHub()}}}
    report = await diag.async_get_config_entry_diagnostics(hass, gateway_entry)
    check("diagnostics reports the loaded hub", report["hub"]["protocol_class"] == "FakeDevice"
          and report["hub"]["socket_open"] is False)

    class UnloadedHub(FakeHub):
        service = None
        device = None

    hass.data = {"divoom": {"hubs": {gateway_entry.entry_id: UnloadedHub()}}}
    report = await diag.async_get_config_entry_diagnostics(hass, gateway_entry)
    check("diagnostics says when the notify service is missing",
          "not loaded" in report["hub"].get("error", ""), str(report["hub"]))


def check_hub():
    """The hub borrows the notify service's connection instead of opening its own."""
    hass = FakeHass(None)
    hub = DivoomHub(hass, MAC, "ditoo", host="http://%s/" % PI, name="Ditoo")
    check("hub cleans its host", hub.host == PI, repr(hub.host))
    try:
        hub.execute("send_brightness", 50)
        check("an unloaded service is a clear error", False, "no error raised")
    except HomeAssistantError as err:
        check("an unloaded service is a clear error", "not loaded" in str(err), str(err))

    class RecordingDevice:
        reconnect_result = True

        def __init__(self):
            self.calls = []

        def reconnect(self, skipPing=None):
            self.calls.append(("reconnect", skipPing))
            return self.reconnect_result

        def send_brightness(self, value):
            self.calls.append(("send_brightness", value))
            return "sent"

        def send_raw(self, data):
            self.calls.append(("send_raw", data))

        def show_lyrics(self):
            raise DivoomUnsupportedError("Pixoo", "lyrics")

        def disconnect(self):
            self.calls.append(("disconnect",))

    class FakeService:
        def __init__(self):
            self._device = RecordingDevice()
            self._lock = threading.RLock()

    service = FakeService()
    hass.data = {"divoom": {"loaded": {MAC: service}}}
    check("hub finds the loaded service's device", hub.device is service._device)
    check("a command reaches the shared device", hub.execute("send_brightness", 50) == "sent"
          and service._device.calls[-2:] == [("reconnect", False), ("send_brightness", 50)],
          str(service._device.calls))
    hub.execute("send_raw", "00")
    check("raw commands skip the ping", ("reconnect", True) in service._device.calls)
    try:
        hub.execute("show_lyrics")
        check("an unsupported mode is a clear error", False, "no error raised")
    except HomeAssistantError as err:
        check("an unsupported mode is a clear error", "lyrics" in str(err), str(err))
    service._device.reconnect_result = False
    try:
        hub.execute("send_brightness", 50)
        check("a failed reconnect is a clear error", False, "no error raised")
    except HomeAssistantError as err:
        check("a failed reconnect is a clear error", PI in str(err), str(err))


def check_errno():
    """An unresolvable gateway hostname is a failure, not a success.

    socket.gaierror carries a negative errno (-2 on Linux); upstream's
    reconnect() treated errno <= 0 as success and gave up silently.
    """

    class UnresolvableSocket:
        def __init__(self, *args, **kwargs):
            pass

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            raise socket_module.gaierror(-2, "Name or service not known")

        def close(self):
            pass

    messages = []

    class Collect(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    logger = logging.getLogger("divoom-errno-check")
    logger.addHandler(Collect())
    logger.propagate = False
    real_socket, real_sleep = socket_module.socket, time_module.sleep
    socket_module.socket, time_module.sleep = UnresolvableSocket, lambda seconds: None
    try:
        device = Ditoo(host="divoom-pi", mac=MAC, port=2, logger=logger)
        ok = device.reconnect()
    finally:
        socket_module.socket, time_module.sleep = real_socket, real_sleep
    check("an unresolvable host makes reconnect fail", ok is False,
          "errno %r" % (device.socket_errno,))
    check("the failure names the host and hints at resolution",
          any("divoom-pi" in m and "resolves" in m for m in messages), str(messages[-1:]))


async def main():
    await check_flow()
    await check_diagnostics()
    check_hub()
    check_errno()

    failed = len([c for c in checks if not c[1]])
    print("\n%d checks, %d failed" % (len(checks), failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
