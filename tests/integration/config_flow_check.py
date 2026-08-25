"""Drive the Divoom config flow and diagnostics with Home Assistant stubbed.

Home Assistant cannot be installed everywhere this repository is checked out,
so this fakes just enough of it to import the real config flow and run both
discovery paths. It cannot prove how Home Assistant itself drives a flow, but
it does catch typos, wrong names and bad call signatures - it caught a change
that had been applied to async_step_bluetooth instead of async_step_zeroconf,
whose body is identical.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ha_stubs  # noqa: E402  (installs the fake homeassistant modules)

from ha_stubs import AbortFlow, ConfigEntry, FakeHass  # noqa: E402

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(REPO))


from custom_components.divoom import config_flow as cf  # noqa: E402
from custom_components.divoom import diagnostics as diag  # noqa: E402

MAC = "b1:21:81:bf:a8:eb"
PI = "192.168.1.50"
checks = []


def check(name, condition, detail=""):
    checks.append((name, condition, detail))
    print("[%s] %s%s" % (" ok " if condition else "FAIL", name, "  " + detail if detail else ""))


class Discovery:
    def __init__(self, host, properties):
        self.host = host
        self.properties = properties


async def main():
    # -- reconfigure: form is prefilled from the entry -----------------------
    entry = ConfigEntry(data={"mac": MAC, "port": 2, "device_type": "ditoo", "host": None})
    flow = cf.DivoomBluetoothConfigFlow()
    flow.hass = FakeHass(entry)
    flow.context = {"entry_id": entry.entry_id}

    result = await flow.async_step_reconfigure()
    check("reconfigure shows a form", result["type"] == "form" and result["step_id"] == "reconfigure")
    keys = {str(k): k.default() for k in result["data_schema"].schema}
    check("host prefilled empty for a hostless entry", keys.get("host") == "")
    check("port prefilled from the entry", keys.get("port") == 2)
    check("device type prefilled from the entry", keys.get("device_type") == "ditoo")
    check("placeholders name the current transport",
          result["description_placeholders"]["current"] == "direct Bluetooth")

    # -- reconfigure: submitting a host rewrites and reloads the entry -------
    result = await flow.async_step_reconfigure(
        {"host": "http://%s/" % PI, "port": 2, "device_type": "ditoo"}
    )
    check("reconfigure aborts with success", result["type"] == "abort"
          and result["reason"] == "reconfigure_successful")
    check("host is normalised by clean_host", entry.data["host"] == PI,
          "got %r" % (entry.data["host"],))
    check("entry was reloaded", entry.reloaded is True)
    check("unrelated entry data survives", entry.data["mac"] == MAC)

    # -- reconfigure: a deleted entry aborts cleanly --------------------------
    flow.hass = FakeHass(None)
    result = await flow.async_step_reconfigure()
    check("missing entry aborts", result["type"] == "abort" and result["reason"] == "unknown_entry")

    # -- zeroconf: adopts the gateway for a hostless entry -------------------
    hostless = ConfigEntry(data={"mac": MAC, "port": 2, "device_type": "ditoo"})
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

    # -- diagnostics ---------------------------------------------------------
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
        device = FakeDevice()

    hass = FakeHass(gateway_entry)
    hass.data = {"divoom": {"hubs": {gateway_entry.entry_id: FakeHub()}}}
    report = await diag.async_get_config_entry_diagnostics(hass, gateway_entry)
    check("diagnostics reports the loaded hub", report["hub"]["protocol_class"] == "FakeDevice"
          and report["hub"]["socket_open"] is False)

    failed = len([c for c in checks if not c[1]])
    print("\n%d checks, %d failed" % (len(checks), failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
