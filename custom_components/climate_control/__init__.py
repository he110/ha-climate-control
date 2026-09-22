"""Climate Control: virtual thermostats that drive followers and boosters."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_ACTUATORS,
    CONF_ENTITY,
    CONF_THERMOSTATS,
    PLATFORMS,
    SUBENTRY_ACTUATOR,
    SUBENTRY_THERMOSTAT,
)
from .engine import Engine

_LOGGER = logging.getLogger(__name__)

type ClimateControlConfigEntry = ConfigEntry[Engine]


async def async_setup_entry(hass: HomeAssistant, entry: ClimateControlConfigEntry) -> bool:
    _prune_orphans(hass, entry)
    engine = Engine(hass, entry)
    entry.runtime_data = engine
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _remove_stale_status_sensors(hass, entry, engine)
    # Start after the entities exist: they register their demand with the engine.
    await engine.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _remove_stale_status_sensors(
    hass: HomeAssistant, entry: ClimateControlConfigEntry, engine: Engine
) -> None:
    """A device detached from a thermostat leaves its status sensor in the registry: drop it."""
    wanted = {f"{sid}_{act.entity_id}_status" for act in engine.actuators.values() for sid in act.thermostats}
    registry = er.async_get(hass)
    for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg.domain == "sensor" and reg.unique_id.endswith("_status") and reg.unique_id not in wanted:
            registry.async_remove(reg.entity_id)


def _prune_orphans(hass: HomeAssistant, entry: ClimateControlConfigEntry) -> None:
    """A removed thermostat leaves its devices behind: drop the link, and the device if nothing is left."""
    thermostats = {s.subentry_id for s in entry.subentries.values() if s.subentry_type == SUBENTRY_THERMOSTAT}
    actuators = entry.options.get(CONF_ACTUATORS) or {}
    pruned = {}
    for eid, data in actuators.items():
        links = [t for t in data.get(CONF_THERMOSTATS, []) if t in thermostats]
        if links:
            pruned[eid] = {**data, CONF_THERMOSTATS: links}
    if pruned != actuators:
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ACTUATORS: pruned})


async def _async_reload(hass: HomeAssistant, entry: ClimateControlConfigEntry) -> None:
    """Any change of thermostats or actuators rebuilds the whole graph."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ClimateControlConfigEntry) -> bool:
    await entry.runtime_data.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ClimateControlConfigEntry) -> bool:
    """v1 kept every actuator as its own subentry; v2 keeps them in options, keyed by entity_id."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        actuators = dict(entry.options.get(CONF_ACTUATORS) or {})
        old = [s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_ACTUATOR]
        for sub in old:
            data = dict(sub.data)
            eid = data.pop(CONF_ENTITY)
            if eid in actuators:
                # Same device configured twice in v1: merge the thermostat links, keep the first settings.
                links = actuators[eid].get(CONF_THERMOSTATS, [])
                actuators[eid][CONF_THERMOSTATS] = links + [
                    t for t in data.get(CONF_THERMOSTATS, []) if t not in links
                ]
                continue
            actuators[eid] = {CONF_NAME: sub.title, **data}
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_ACTUATORS: actuators}, version=2
        )
        for sub in old:
            hass.config_entries.async_remove_subentry(entry, sub.subentry_id)
        _LOGGER.info("Migrated %d actuators out of subentries", len(old))
    return True
