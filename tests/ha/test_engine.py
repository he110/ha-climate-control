"""End to end: thermostats drive fake devices through mocked services."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.climate import DOMAIN as CLIMATE
from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.climate_control.const import DOMAIN

CONVECTOR = {
    "hvac_modes": ["off", "heat"],
    "min_temp": 5,
    "max_temp": 30,
    "target_temp_step": 0.5,
    "supported_features": 1 | 16 | 128 | 256,
    "temperature": 5,
}
BREEZER = {
    "hvac_modes": ["off", "fan_only"],
    "min_temp": 5,
    "max_temp": 25,
    "target_temp_step": 1,
    "fan_modes": ["1", "2", "7"],
    "fan_mode": "2",
    "preset_modes": ["manual", "night", "turbo"],
    "preset_mode": "manual",
    "supported_features": 1 | 8 | 16,
    "temperature": 5,
}
AC = {
    "hvac_modes": ["off", "cool", "heat", "fan_only"],
    "min_temp": 16,
    "max_temp": 30,
    "target_temp_step": 1,
    "supported_features": 1 | 128 | 256,
    "temperature": 24,
}

DIR = {"start": 2.0, "stop": 0.5, "min_on": 0, "min_off": 0}


def _thermostat(sid: str, title: str, sensor: str) -> dict[str, Any]:
    return {
        "subentry_id": sid,
        "subentry_type": "thermostat",
        "title": title,
        "unique_id": None,
        "data": {
            "sensor": sensor,
            "min_temp": 9,
            "max_temp": 30,
            "target_temp_step": 0.5,
            "presets": {"home_heat": 23, "sleep_heat": 21, "home_cool": 24, "sleep_cool": 25},
        },
    }


def _actuator(sid: str, title: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"subentry_id": sid, "subentry_type": "actuator", "title": title, "unique_id": None, "data": data}


class Calls:
    """Mocked device services that also update the fake state, like a real device would."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.log: list[tuple[str, str, dict[str, Any]]] = []

    def register(self) -> None:
        for service in (
            "set_temperature",
            "set_hvac_mode",
            "set_fan_mode",
            "set_preset_mode",
            "turn_on",
            "turn_off",
        ):
            self._wrap(CLIMATE, service)
        for service in ("turn_on", "turn_off"):
            self._wrap("homeassistant", service)

    def _wrap(self, domain: str, service: str) -> None:
        existing = self.hass.services._services.get(domain, {}).get(service)

        async def handler(call: ServiceCall) -> None:
            entity_ids = call.data.get("entity_id")
            entity_ids = [entity_ids] if isinstance(entity_ids, str) else list(entity_ids or [])
            ours = [
                e for e in entity_ids if e.startswith(("climate.office", "climate.bedroom", "climate.kids"))
            ]
            if ours:
                # Our own thermostats go to the real service.
                result = existing.job.target(call)
                if hasattr(result, "__await__"):
                    await result
                return
            for eid in entity_ids:
                data = {k: v for k, v in call.data.items() if k != "entity_id"}
                self.log.append((eid, service, data))
                self._apply(eid, service, data)

        self.hass.services.async_register(domain, service, handler)

    def _apply(self, eid: str, service: str, data: dict[str, Any]) -> None:
        state = self.hass.states.get(eid)
        attrs = dict(state.attributes)
        value = state.state
        match service:
            case "set_temperature":
                attrs["temperature"] = data["temperature"]
            case "set_hvac_mode":
                value = data["hvac_mode"]
            case "set_fan_mode":
                attrs["fan_mode"] = data["fan_mode"]
            case "set_preset_mode":
                attrs["preset_mode"] = data["preset_mode"]
            case "turn_on":
                value = "on" if not eid.startswith("climate.") else value
            case "turn_off":
                value = "off"
        self.hass.states.async_set(eid, value, attrs)

    def take(self) -> list[tuple[str, str, dict[str, Any]]]:
        out, self.log = self.log, []
        return out


@pytest.fixture
async def house(hass: HomeAssistant):
    hass.states.async_set("sensor.office_t", "23.0")
    hass.states.async_set("sensor.bedroom_t", "23.0")
    hass.states.async_set("sensor.kids_t", "23.0")
    hass.states.async_set("climate.convector", "off", CONVECTOR)
    hass.states.async_set("climate.breezer", "fan_only", BREEZER)
    hass.states.async_set("switch.stove", "off")
    hass.states.async_set("climate.corridor_ac", "off", AC)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Climate Control",
        data={},
        subentries_data=[
            _thermostat("office", "Office", "sensor.office_t"),
            _thermostat("bedroom", "Bedroom", "sensor.bedroom_t"),
            _thermostat("kids", "Kids", "sensor.kids_t"),
            _actuator(
                "a_conv",
                "Convector",
                {
                    "entity_id": "climate.convector",
                    "thermostats": ["office"],
                    "manage_mode": True,
                    "heat": {"idle": "follow", "boost": "none", **DIR},
                    "cool": {"idle": "off", "boost": "none", **DIR},
                },
            ),
            _actuator(
                "a_breezer",
                "Breezer",
                {
                    "entity_id": "climate.breezer",
                    "thermostats": ["office"],
                    "manage_mode": False,
                    "heat": {"idle": "follow", "boost": "extreme", "boost_fan_mode": "7", **DIR},
                    "cool": {"idle": "follow", "boost": "extreme", "boost_fan_mode": "7", **DIR},
                    "night_preset": "night",
                },
            ),
            _actuator(
                "a_stove",
                "Stove",
                {
                    "entity_id": "switch.stove",
                    "thermostats": ["kids"],
                    "heat": {"idle": "off", "boost": "turn_on", **DIR},
                    "cool": {"idle": "ignore", "boost": "none", **DIR},
                    "night_no_boost": True,
                },
            ),
            _actuator(
                "a_ac",
                "Corridor AC",
                {
                    "entity_id": "climate.corridor_ac",
                    "thermostats": ["bedroom", "kids"],
                    "manage_mode": True,
                    "veto": True,
                    "heat": {"idle": "off", "boost": "none", **DIR},
                    "cool": {"idle": "off", "boost": "on_target", **{**DIR, "start": 1.5}},
                },
            ),
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    calls = Calls(hass)
    calls.register()
    engine = entry.runtime_data

    async def settle() -> list[tuple[str, str, dict[str, Any]]]:
        await engine.async_evaluate()
        await hass.async_block_till_done()
        return calls.take()

    for tid in ("office", "bedroom", "kids"):
        await hass.services.async_call(
            CLIMATE, "set_preset_mode", {"entity_id": f"climate.{tid}", "preset_mode": "home"}, blocking=True
        )
    await settle()
    return hass, engine, settle


async def test_setpoint_is_mirrored_and_modes_managed(house) -> None:
    hass, _, settle = house
    await hass.services.async_call(
        CLIMATE, "set_temperature", {"entity_id": "climate.office", "temperature": 23.5}, blocking=True
    )
    log = await settle()
    assert ("climate.convector", "set_temperature", {"temperature": 23.5}) in log
    # Breezer: step 1, and its mode is ventilation, so we never touch it.
    assert ("climate.breezer", "set_temperature", {"temperature": 24}) in log
    assert not [c for c in log if c[0] == "climate.breezer" and c[1] == "set_hvac_mode"]
    assert hass.states.get("climate.convector").state == "heat"


async def test_boost_extreme_then_back(house) -> None:
    hass, _, settle = house
    hass.states.async_set("sensor.office_t", "20.0")
    log = await settle()
    assert ("climate.breezer", "set_temperature", {"temperature": 25}) in log  # its max
    assert ("climate.breezer", "set_fan_mode", {"fan_mode": "7"}) in log
    office = hass.states.get("climate.office")
    assert office.attributes["hvac_action"] == "heating"
    assert office.attributes["boosting_actuators"] == ["Breezer"]
    assert hass.states.get("sensor.breezer_status").state == "boost"

    hass.states.async_set("sensor.office_t", "22.6")
    log = await settle()
    assert ("climate.breezer", "set_temperature", {"temperature": 23}) in log
    assert ("climate.breezer", "set_fan_mode", {"fan_mode": "2"}) in log  # speed given back
    assert hass.states.get("climate.office").attributes["hvac_action"] == "idle"


async def test_person_changed_speed_during_boost_is_respected(house) -> None:
    hass, _, settle = house
    hass.states.async_set("sensor.office_t", "20.0")
    await settle()
    hass.states.async_set(
        "climate.breezer", "fan_only", {**hass.states.get("climate.breezer").attributes, "fan_mode": "1"}
    )
    hass.states.async_set("sensor.office_t", "22.6")
    log = await settle()
    assert not [c for c in log if c[1] == "set_fan_mode"]


async def test_night_preset_and_restore(house) -> None:
    hass, _, settle = house
    await hass.services.async_call(
        CLIMATE, "set_preset_mode", {"entity_id": "climate.office", "preset_mode": "sleep"}, blocking=True
    )
    log = await settle()
    assert ("climate.breezer", "set_preset_mode", {"preset_mode": "night"}) in log
    assert ("climate.convector", "set_temperature", {"temperature": 21}) in log
    await hass.services.async_call(
        CLIMATE, "set_preset_mode", {"entity_id": "climate.office", "preset_mode": "home"}, blocking=True
    )
    log = await settle()
    assert ("climate.breezer", "set_preset_mode", {"preset_mode": "manual"}) in log


async def test_stove_boost_and_night_ban(house) -> None:
    hass, _, settle = house
    hass.states.async_set("sensor.kids_t", "20.5")
    log = await settle()
    assert ("switch.stove", "turn_on", {}) in log
    await hass.services.async_call(
        CLIMATE, "set_preset_mode", {"entity_id": "climate.kids", "preset_mode": "sleep"}, blocking=True
    )
    log = await settle()
    assert ("switch.stove", "turn_off", {}) in log


async def test_boost_switch_mutes_boosters(house) -> None:
    hass, _, settle = house
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.kids_boosters_allowed"}, blocking=True
    )
    hass.states.async_set("sensor.kids_t", "19.0")
    log = await settle()
    assert ("switch.stove", "turn_on", {}) not in log


async def test_thermostat_off_stops_boost(house) -> None:
    hass, _, settle = house
    hass.states.async_set("sensor.kids_t", "19.0")
    await settle()
    await hass.services.async_call(
        CLIMATE, "set_hvac_mode", {"entity_id": "climate.kids", "hvac_mode": "off"}, blocking=True
    )
    log = await settle()
    assert ("switch.stove", "turn_off", {}) in log


async def test_sensor_lost_stops_boost(house) -> None:
    hass, _, settle = house
    hass.states.async_set("sensor.kids_t", "19.0")
    await settle()
    hass.states.async_set("sensor.kids_t", "unavailable")
    log = await settle()
    assert ("switch.stove", "turn_off", {}) in log


async def test_season_switch(house) -> None:
    hass, _, settle = house
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.climate_control_season", "option": "cool"},
        blocking=True,
    )
    office = hass.states.get("climate.office")
    assert office.state == "cool"
    assert office.attributes["hvac_modes"] == ["off", "cool"]
    assert office.attributes["temperature"] == 24  # summer "home"
    log = await settle()
    assert ("climate.convector", "set_hvac_mode", {"hvac_mode": "off"}) in log
    # No cooling in winter, no heating in summer: the thermostat refuses the other mode.
    with pytest.raises(Exception, match="not valid"):
        await hass.services.async_call(
            CLIMATE, "set_hvac_mode", {"entity_id": "climate.office", "hvac_mode": "heat"}, blocking=True
        )


async def test_winter_never_cools_with_ac(house) -> None:
    hass, _, settle = house
    hass.states.async_set("sensor.bedroom_t", "28.0")
    hass.states.async_set("sensor.kids_t", "28.0")
    log = await settle()
    assert not [c for c in log if c[0] == "climate.corridor_ac" and c[2].get("hvac_mode") == "cool"]


async def test_shared_ac_average_and_veto(house) -> None:
    hass, _, settle = house
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.climate_control_season", "option": "cool"},
        blocking=True,
    )
    await settle()
    # Bedroom +3 over, kids -1 under: average 1 < 1.5, stay off.
    hass.states.async_set("sensor.bedroom_t", "27.0")
    hass.states.async_set("sensor.kids_t", "23.0")
    log = await settle()
    assert not [c for c in log if c[0] == "climate.corridor_ac" and c[1] == "set_hvac_mode"]
    # Both warm: average 2.5, cool at the average setpoint.
    hass.states.async_set("sensor.kids_t", "26.0")
    log = await settle()
    assert ("climate.corridor_ac", "set_hvac_mode", {"hvac_mode": "cool"}) in log
    assert ("climate.corridor_ac", "set_temperature", {"temperature": 24}) in log
    # Kids overshoot by 2 (= start... veto uses start 1.5): stop at once although the average is 1.5.
    hass.states.async_set("sensor.bedroom_t", "29.0")
    hass.states.async_set("sensor.kids_t", "22.0")
    log = await settle()
    assert ("climate.corridor_ac", "set_hvac_mode", {"hvac_mode": "off"}) in log
    assert hass.states.get("sensor.corridor_ac_status").attributes["reason"] == "overshoot veto"


async def test_restart_restores_boost_memory(hass: HomeAssistant, house) -> None:
    hass, engine, settle = house
    hass.states.async_set("sensor.office_t", "20.0")
    await settle()
    entry = engine.entry
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    engine = entry.runtime_data
    assert engine.actuators["a_breezer"].boost.on
    assert engine.actuators["a_breezer"].overrides["fan"] == {"base": "2", "applied": "7"}
