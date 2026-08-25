"""Device-targeted actions for the Divoom integration."""
from __future__ import annotations

import logging
import os

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN
from .hub import DivoomHub

_LOGGER = logging.getLogger(__package__)

GAME_CONTROLS = {"go": 0, "left": 1, "right": 2, "up": 3, "down": 4, "ok": 5}
KEYBOARD_ACTIONS = {"toggle": 0, "next": 1, "previous": -1}

WEATHER_MODES = {
    'clear-night': 1, 'cloudy': 3, 'exceptional': 3, 'fog': 9, 'hail': 6,
    'lightning': 5, 'lightning-rainy': 5, 'partlycloudy': 3, 'pouring': 6,
    'rainy': 6, 'snowy': 8, 'snowy-rainy': 8, 'sunny': 1, 'windy': 3,
    'windy-variant': 3,
}


def _resolve_hubs(hass: HomeAssistant, call: ServiceCall) -> list[DivoomHub]:
    """Resolve the targeted devices/entities of a service call into hubs."""
    device_ids = call.data.get("device_id") or []
    if isinstance(device_ids, str):
        device_ids = [device_ids]
    device_ids = list(device_ids)

    entity_ids = call.data.get("entity_id") or []
    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]

    entity_registry = er.async_get(hass)
    for entity_id in entity_ids:
        entry = entity_registry.async_get(entity_id)
        if entry is not None and entry.device_id is not None:
            device_ids.append(entry.device_id)

    device_registry = dr.async_get(hass)
    hubs: dict[str, DivoomHub] = hass.data.get(DOMAIN, {}).get("hubs", {})

    resolved: list[DivoomHub] = []
    for device_id in device_ids:
        device = device_registry.async_get(device_id)
        if device is None:
            continue
        macs = [identifier[1] for identifier in device.identifiers if identifier[0] == DOMAIN]
        for hub in hubs.values():
            if hub.mac in macs and hub not in resolved:
                resolved.append(hub)

    if not resolved:
        raise HomeAssistantError("No Divoom device was targeted. Choose a device in the action's target.")
    return resolved


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the divoom.* actions once."""
    if hass.services.has_service(DOMAIN, "show_image"):
        return

    async def _run(call: ServiceCall, command: str, *args, **kwargs) -> None:
        for hub in _resolve_hubs(hass, call):
            await hub.async_execute(command, *args, **kwargs)

    async def show_image(call: ServiceCall) -> None:
        for hub in _resolve_hubs(hass, call):
            image_path = os.path.join(hub.media_directory, call.data["file"])
            await hub.async_execute("show_image", image_path, time=call.data.get("time"))

    async def show_text(call: ServiceCall) -> None:
        for hub in _resolve_hubs(hass, call):
            font = call.data.get("font")
            font_path = os.path.join(hub.font_directory, font) if font else None
            color1 = call.data.get("color")
            color2 = call.data.get("background_color")
            await hub.async_execute(
                "show_text", call.data["text"], font_path,
                size=call.data.get("size"), time=call.data.get("time"),
                color1=list(color1) if color1 else None,
                color2=list(color2) if color2 else None,
            )

    async def show_clock(call: ServiceCall) -> None:
        color = call.data.get("color")
        await _run(
            call, "show_clock",
            clock=call.data.get("style"),
            twentyfour=call.data.get("twentyfour"),
            weather=call.data.get("weather"),
            temp=call.data.get("temperature"),
            calendar=call.data.get("calendar"),
            color=list(color) if color else None,
            hot=call.data.get("hot"),
        )

    async def show_effects(call: ServiceCall) -> None:
        await _run(call, "show_effects", call.data.get("number", 0))

    async def show_visualization(call: ServiceCall) -> None:
        await _run(call, "show_visualization", call.data.get("number", 0), None, None)

    async def show_design(call: ServiceCall) -> None:
        await _run(call, "show_design", number=call.data.get("number"))

    async def show_lyrics(call: ServiceCall) -> None:
        await _run(call, "show_lyrics")

    async def show_scoreboard(call: ServiceCall) -> None:
        await _run(call, "show_scoreboard", blue=call.data.get("player1", 0), red=call.data.get("player2", 0))

    async def show_timer(call: ServiceCall) -> None:
        await _run(call, "show_timer", value=1 if call.data.get("start", True) else 0)

    async def show_countdown(call: ServiceCall) -> None:
        await _run(
            call, "show_countdown",
            value=1 if call.data.get("start", True) else 0,
            countdown=call.data.get("countdown"),
        )

    async def show_noise(call: ServiceCall) -> None:
        await _run(call, "show_noise", value=1 if call.data.get("start", True) else 0)

    async def show_radio(call: ServiceCall) -> None:
        await _run(
            call, "show_radio",
            value=1 if call.data.get("on", True) else 0,
            frequency=call.data.get("frequency"),
        )

    async def show_sleep(call: ServiceCall) -> None:
        color = call.data.get("color")
        await _run(
            call, "show_sleep",
            1 if call.data.get("start", True) else 0,
            call.data.get("time"),
            call.data.get("sleepmode"),
            call.data.get("volume"),
            list(color) if color else None,
            call.data.get("brightness"),
            call.data.get("frequency"),
        )

    async def show_equalizer(call: ServiceCall) -> None:
        await _run(
            call, "show_equalizer", call.data.get("number", 0),
            audioMode=call.data.get("audiomode", False),
            backgroundMode=call.data.get("backgroundmode", False),
            streamMode=call.data.get("streammode", False),
        )

    async def play_game(call: ServiceCall) -> None:
        await _run(call, "show_game", value=call.data.get("number"))

    async def game_control(call: ServiceCall) -> None:
        action = call.data.get("action", "go")
        await _run(call, "send_gamecontrol", value=GAME_CONTROLS.get(action, 0))

    async def set_keyboard(call: ServiceCall) -> None:
        action = call.data.get("action", "toggle")
        await _run(call, "send_keyboard", value=KEYBOARD_ACTIONS.get(action, 0))

    async def set_playstate(call: ServiceCall) -> None:
        await _run(call, "send_playstate", value=1 if call.data.get("playing", True) else 0)

    async def set_datetime(call: ServiceCall) -> None:
        await _run(call, "send_datetime", value=call.data.get("datetime"))

    async def show_temperature(call: ServiceCall) -> None:
        color = call.data.get("color")
        await _run(
            call, "show_temperature",
            value=1 if call.data.get("fahrenheit") else 0,
            color=list(color) if color else None,
        )

    async def set_weather(call: ServiceCall) -> None:
        weather = call.data.get("weather")
        if isinstance(weather, str) and not weather.isdigit():
            weather = WEATHER_MODES.get(weather)
        elif weather is not None:
            weather = int(weather)
        await _run(call, "send_weather", value=call.data.get("temperature"), weather=weather)

    async def set_alarm(call: ServiceCall) -> None:
        await _run(
            call, "show_alarm",
            number=call.data.get("number", 0),
            time=call.data.get("time"),
            weekdays=call.data.get("weekdays"),
            alarmMode=call.data.get("alarmmode"),
            triggerMode=call.data.get("triggermode"),
            frequency=call.data.get("frequency"),
            volume=call.data.get("volume"),
        )

    async def set_memorial(call: ServiceCall) -> None:
        await _run(
            call, "show_memorial",
            number=call.data.get("number", 0),
            value=call.data.get("datetime"),
            text=call.data.get("text"),
            animate=True,
        )

    async def send_raw(call: ServiceCall) -> None:
        raw = call.data["raw"]
        if isinstance(raw, str):
            raw = [int(part, 16) for part in raw.replace(",", " ").split()]
        await _run(call, "send_command", command=raw[0], args=raw[1:])

    services = {
        "show_image": show_image,
        "show_text": show_text,
        "show_clock": show_clock,
        "show_effects": show_effects,
        "show_visualization": show_visualization,
        "show_design": show_design,
        "show_lyrics": show_lyrics,
        "show_scoreboard": show_scoreboard,
        "show_timer": show_timer,
        "show_countdown": show_countdown,
        "show_noise": show_noise,
        "show_radio": show_radio,
        "show_sleep": show_sleep,
        "show_temperature": show_temperature,
        "show_equalizer": show_equalizer,
        "play_game": play_game,
        "game_control": game_control,
        "set_keyboard": set_keyboard,
        "set_playstate": set_playstate,
        "set_datetime": set_datetime,
        "set_weather": set_weather,
        "set_alarm": set_alarm,
        "set_memorial": set_memorial,
        "send_raw": send_raw,
    }

    for name, handler in services.items():
        hass.services.async_register(DOMAIN, name, handler)
