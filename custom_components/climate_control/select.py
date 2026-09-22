"""Season: one switch for the whole house."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .engine import Engine
from .logic import Season


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([SeasonSelect(entry.runtime_data)])


def hub_device(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Climate Control",
        model="Hub",
    )


class SeasonSelect(SelectEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "season"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._attr_options = [s.value for s in Season]
        self._attr_unique_id = f"{engine.entry.entry_id}_season"
        self._attr_device_info = hub_device(engine.entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self._attr_options:
            self._engine.set_season(Season(last.state))

    @property
    def current_option(self) -> str:
        return self._engine.season.value

    async def async_select_option(self, option: str) -> None:
        self._engine.set_season(Season(option))
        self.async_write_ha_state()
