"""The divoom component."""
import logging
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.components.notify import SERVICE_NOTIFY
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import DATA_CUSTOM_COMPONENTS
from homeassistant.util import slugify

from homeassistant.const import CONF_NAME, CONF_HOST, CONF_MAC, CONF_PORT, Platform
from .const import (  # pylint:disable=unused-import
    CONF_DEVICE_TYPE,
    CONF_MEDIA_DIR,
    CONF_MEDIA_DIR_DEFAULT,
    CONF_ESCAPE_PAYLOAD,
    DOMAIN,
    ENTITY_PLATFORMS,
    PLATFORMS,
)
from .hub import DivoomHub
from .services import async_setup_services

_LOGGER = logging.getLogger(__package__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_NAME): cv.string,
                vol.Required(CONF_MAC): cv.string,
                vol.Optional(CONF_PORT, default=1): cv.port,
                vol.Required(CONF_DEVICE_TYPE): cv.string,
                vol.Required(CONF_MEDIA_DIR, default=CONF_MEDIA_DIR_DEFAULT): cv.string,
                vol.Optional(CONF_ESCAPE_PAYLOAD, default=False): cv.boolean
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Divoom from a config."""

    hass.data.setdefault(DOMAIN, {})
    async_setup_services(hass)

    _LOGGER.debug("Divoom: successfully setup a config")
    return True

async def async_setup_entry(hass: HomeAssistant, config: ConfigEntry) -> bool:
    """Set the config entry up."""

    mac = config.data[CONF_MAC]
    name = config.data[CONF_NAME]

    hass.data.setdefault(DOMAIN, {})
    domainConfig = hass.data.get(DOMAIN)
    domainConfig.setdefault('hubs', {})

    hub = DivoomHub(
        hass,
        config.data.get(CONF_DEVICE_TYPE),
        config.data.get(CONF_HOST) or None,
        mac,
        config.data.get(CONF_PORT) or 1,
        config.data.get(CONF_ESCAPE_PAYLOAD),
        name=name,
        media_directory=hass.config.path(config.data.get(CONF_MEDIA_DIR) or CONF_MEDIA_DIR_DEFAULT),
        font_directory=hass.config.path(f"{DATA_CUSTOM_COMPONENTS}/{DOMAIN}/fonts/"),
    )
    domainConfig['hubs'][config.entry_id] = hub

    # legacy notify platform (discovery based) plus the real entity platforms
    discovery = dict(config.data)
    discovery['entry_id'] = config.entry_id
    hass.async_create_task(async_load_platform(hass, Platform.NOTIFY, DOMAIN, discovery, discovery))

    await hass.config_entries.async_forward_entry_setups(config, ENTITY_PLATFORMS)

    _LOGGER.debug("Divoom: successfully setup a config entry for {} ({})".format(name, mac))
    return True

async def async_unload_entry(hass: HomeAssistant, config: ConfigEntry) -> bool:
    """Unload a config entry."""

    mac = config.data[CONF_MAC]
    name = config.data[CONF_NAME]

    hass.data.setdefault(DOMAIN, {})
    domainConfig = hass.data.get(DOMAIN)
    domainConfig.setdefault('loaded', {})
    domainConfig.setdefault('hubs', {})

    loadedServices = domainConfig.get('loaded')
    if mac in loadedServices:
        del loadedServices[mac]

    hub = domainConfig['hubs'].pop(config.entry_id, None)
    if hub is not None:
        await hass.async_add_executor_job(hub.disconnect)

    hass.services.async_remove(SERVICE_NOTIFY, slugify(name))
    await hass.config_entries.async_unload_platforms(config, ENTITY_PLATFORMS)

    _LOGGER.debug("Divoom: successfully unloaded a config entry for {} ({})".format(name, mac))
    return True
