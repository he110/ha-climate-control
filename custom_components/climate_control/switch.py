"""Per-thermostat mute for boosters."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .climate import thermostat_device
from .const import SUBENTRY_THERMOSTAT
from .engine import Engine


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine: Engine = entry.runtime_data
    for sub in entry.subentries.values():
        if sub.subentry_type == SUBENTRY_THERMOSTAT:
            async_add_entities(
                [BoostAllowedSwitch(engine, sub.subentry_id, sub.title)], config_subentry_id=sub.subentry_id
            )


class BoostAllowedSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "boost_allowed"
    _attr_should_poll = False

    def __init__(self, engine: Engine, subentry_id: str, title: str) -> None:
        self._engine = engine
        self._runtime = engine.thermostats[subentry_id]
        self._attr_unique_id = f"{subentry_id}_boost_allowed"
        self._attr_device_info = thermostat_device(subentry_id, title)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self._runtime.boost_allowed = not (last is not None and last.state == STATE_OFF)

    @property
    def is_on(self) -> bool:
        return self._runtime.boost_allowed

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set(False)

    def _set(self, value: bool) -> None:
        self._runtime.boost_allowed = value
        self.async_write_ha_state()
        self._engine.request_update()
