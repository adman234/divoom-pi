"""Just enough of Home Assistant, faked, to import the Divoom integration.

Home Assistant will not install here, so this fakes just enough of it to import
custom_components.divoom.config_flow and diagnostics for real and run the new
code paths. It cannot prove the flow behaves correctly inside Home Assistant,
but it does catch typos, bad names and wrong call signatures in the new code -
which is otherwise entirely unverified.
"""

import asyncio
import sys
import types
from pathlib import Path

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

import voluptuous as vol  # noqa: E402


def module(name, **attributes):
    mod = types.ModuleType(name)
    mod.__path__ = []  # make every stub a package, so submodules can be stubbed too
    for key, value in attributes.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


# ---------------------------------------------------------------- stubs ----

class AbortFlow(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class ConfigFlow:
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)

    def __init__(self):
        self.context = {}
        self.hass = None
        self.unique_id = None

    async def async_set_unique_id(self, unique_id, raise_on_progress=True):
        self.unique_id = unique_id
        return getattr(self, "_existing_entry", None)

    def _abort_if_unique_id_configured(self, updates=None, reload_on_update=True):
        entry = getattr(self, "_existing_entry", None)
        if entry is None:
            return
        if updates:
            entry.data = {**entry.data, **updates}
            entry.reloaded = True
        raise AbortFlow("already_configured")

    def _async_abort_entries_match(self, match_dict=None):
        return None

    def async_abort(self, reason):
        return {"type": "abort", "reason": reason}

    def async_show_form(self, step_id=None, data_schema=None, errors=None,
                        description_placeholders=None):
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "description_placeholders": description_placeholders,
        }

    def async_create_entry(self, title=None, data=None):
        return {"type": "create_entry", "title": title, "data": data}

    def async_update_reload_and_abort(self, entry, data=None, reason="reconfigure_successful",
                                      **kwargs):
        entry.data = data
        entry.reloaded = True
        return {"type": "abort", "reason": reason, "data": data}


class Platform:
    NOTIFY = "notify"
    LIGHT = "light"
    SELECT = "select"
    NUMBER = "number"
    BUTTON = "button"


class _Registry:
    def register(self, domain):
        def decorate(cls):
            return cls
        return decorate


class ConfigEntry:
    def __init__(self, entry_id="abc123", title="Divoom Ditoo", data=None, source="bluetooth"):
        self.entry_id = entry_id
        self.title = title
        self.data = data or {}
        self.source = source
        self.version = 1
        self.unique_id = self.data.get("mac")
        self.reloaded = False


class FakeConfigEntries:
    def __init__(self, entry):
        self._entry = entry

    def async_get_entry(self, entry_id):
        return self._entry if self._entry and entry_id == self._entry.entry_id else None


class FakeHass:
    def __init__(self, entry=None):
        self.config_entries = FakeConfigEntries(entry)
        self.data = {}


def selector_passthrough(*args, **kwargs):
    return args[0] if args else kwargs


module("homeassistant")
module("homeassistant.const", CONF_NAME="name", CONF_HOST="host", CONF_MAC="mac",
       CONF_PORT="port", Platform=Platform)
module("homeassistant.core", HomeAssistant=object, ServiceCall=object,
       callback=lambda func: func, HomeAssistantError=Exception)
module("homeassistant.exceptions", HomeAssistantError=Exception)
module("homeassistant.config_entries", ConfigFlow=ConfigFlow, ConfigEntry=ConfigEntry,
       HANDLERS=_Registry())
module("homeassistant.data_entry_flow", AbortFlow=AbortFlow, FlowResult=dict)
module("homeassistant.helpers")
_cv = module("homeassistant.helpers.config_validation", port=vol.Coerce(int), string=str)
# config_validation exposes dozens of validators; anything this integration
# reaches for that is not needed to exercise the flow becomes a pass-through.
_cv.__getattr__ = lambda name: (lambda value=None: value)
module("homeassistant.helpers.device_registry", CONNECTION_BLUETOOTH="bluetooth",
       DeviceInfo=dict)
module("homeassistant.helpers.entity", Entity=object)
module("homeassistant.helpers.selector",
       SelectOptionDict=lambda value=None, label=None: {"value": value, "label": label},
       SelectSelector=selector_passthrough,
       SelectSelectorConfig=lambda **kwargs: kwargs,
       SelectSelectorMode=types.SimpleNamespace(DROPDOWN="dropdown", LIST="list"))
module("homeassistant.helpers.service_info")
module("homeassistant.helpers.service_info.zeroconf", ZeroconfServiceInfo=object)
module("homeassistant.components")
module("homeassistant.components.bluetooth", BluetoothServiceInfo=object,
       BluetoothServiceInfoBleak=object, async_discovered_service_info=lambda *a, **k: [])
module("homeassistant.components.diagnostics", async_redact_data=lambda data, keys: data)
module("homeassistant.components.notify", SERVICE_NOTIFY="notify", ATTR_MESSAGE="message",
       ATTR_TITLE="title", ATTR_DATA="data", BaseNotificationService=object)
module("homeassistant.helpers.discovery", async_load_platform=lambda *a, **k: None)
module("homeassistant.helpers.typing", ConfigType=dict, DiscoveryInfoType=dict,
       HomeAssistantType=object)
module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
module("homeassistant.loader", DATA_CUSTOM_COMPONENTS="custom_components")
module("homeassistant.util", slugify=lambda value: str(value).lower().replace(" ", "_"))
module("homeassistant.helpers.entity_registry", async_get=lambda hass: None)
module("homeassistant.helpers.entity_component", EntityComponent=object)
module("homeassistant.helpers.event", async_track_time_interval=lambda *a, **k: None)
module("homeassistant.helpers.service", async_extract_config_entry_ids=lambda *a, **k: set())



# Platforms the entity modules import.
module("homeassistant.components.light",
       ATTR_BRIGHTNESS="brightness", ATTR_RGB_COLOR="rgb_color",
       ColorMode=types.SimpleNamespace(RGB="rgb"), LightEntity=object)
module("homeassistant.components.select", SelectEntity=object)
module("homeassistant.components.number", NumberEntity=object,
       NumberMode=types.SimpleNamespace(SLIDER="slider"))
module("homeassistant.components.button", ButtonEntity=object)
