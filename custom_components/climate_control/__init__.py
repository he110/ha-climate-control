"""Climate Control: virtual thermostats that drive followers and boosters."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .engine import Engine

type ClimateControlConfigEntry = ConfigEntry[Engine]


async def async_setup_entry(hass: HomeAssistant, entry: ClimateControlConfigEntry) -> bool:
    engine = Engine(hass, entry)
    entry.runtime_data = engine
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Start after the entities exist: they register their demand with the engine.
    await engine.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ClimateControlConfigEntry) -> None:
    """Adding, changing or removing a thermostat/actuator rebuilds the whole graph."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ClimateControlConfigEntry) -> bool:
    await entry.runtime_data.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
