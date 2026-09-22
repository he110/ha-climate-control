"""What each actuator is doing and why — the place to look when the house behaves oddly."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, SUBENTRY_ACTUATOR
from .engine import Engine, signal_actuator
from .logic import Status


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine: Engine = entry.runtime_data
    for sub in entry.subentries.values():
        if sub.subentry_type == SUBENTRY_ACTUATOR:
            async_add_entities(
                [ActuatorStatusSensor(engine, sub.subentry_id)], config_subentry_id=sub.subentry_id
            )


class ActuatorStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "actuator_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_should_poll = False

    def __init__(self, engine: Engine, subentry_id: str) -> None:
        self._engine = engine
        self._attr_options = [s.value for s in Status]
        self._act = engine.actuators[subentry_id]
        self._attr_unique_id = f"{subentry_id}_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry_id)},
            name=self._act.title,
            manufacturer="Climate Control",
            model="Actuator",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_actuator(self._engine.entry.entry_id, self._act.subentry_id),
                self.async_write_ha_state,
            )
        )

    @property
    def native_value(self) -> str | None:
        return self._act.plan.status.value if self._act.plan else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self._act.plan
        thermostats = [
            self._engine.entry.subentries[t].title
            for t in self._act.thermostats
            if t in self._engine.entry.subentries
        ]
        attrs: dict[str, Any] = {"entity": self._act.entity_id, "thermostats": thermostats}
        if plan is not None:
            attrs |= {
                "action": plan.action.value,
                "direction": plan.direction.value if plan.direction else None,
                "setpoint": plan.setpoint,
                "delta": plan.delta,
                "night": plan.night,
                "reason": plan.reason,
            }
        if self._act.last_error:
            attrs["last_error"] = self._act.last_error
        return attrs
