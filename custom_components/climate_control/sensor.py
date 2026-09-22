"""What each actuator is doing and why — the place to look when the house behaves oddly.

One sensor per (thermostat, actuator) link, on the thermostat's device: a room in one place.
A shared actuator shows the same decision under each of its thermostats.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .climate import thermostat_device
from .engine import ActuatorRuntime, Engine, signal_actuator
from .logic import Status


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine: Engine = entry.runtime_data
    for sid in engine.thermostats:
        sub = entry.subentries[sid]
        sensors = [
            ActuatorStatusSensor(engine, act, sid, sub.title)
            for act in engine.actuators.values()
            if sid in act.thermostats
        ]
        if sensors:
            async_add_entities(sensors, config_subentry_id=sid)


class ActuatorStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_should_poll = False
    _attr_icon = "mdi:hvac"
    _attr_translation_key = "actuator_status"  # state names; the entity name is the actuator's

    def __init__(
        self, engine: Engine, act: ActuatorRuntime, thermostat_id: str, thermostat_title: str
    ) -> None:
        self._engine = engine
        self._act = act
        self._attr_options = [s.value for s in Status]
        self._attr_name = act.title
        self._attr_unique_id = f"{thermostat_id}_{act.entity_id}_status"
        self._attr_device_info = thermostat_device(thermostat_id, thermostat_title)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_actuator(self._engine.entry.entry_id, self._act.entity_id),
                self.async_write_ha_state,
            )
        )

    @property
    def native_value(self) -> str | None:
        return self._act.plan.status.value if self._act.plan else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self._act.plan
        subs = self._engine.entry.subentries
        attrs: dict[str, Any] = {
            "entity": self._act.entity_id,
            "thermostats": [subs[t].title for t in self._act.thermostats if t in subs],
        }
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
