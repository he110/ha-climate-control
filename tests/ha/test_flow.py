"""Hub, thermostat and actuator flows."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.climate_control.const import DOMAIN


async def _hub(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    return result["result"]


async def test_full_setup_via_flows(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.office_t", "21.0", {"device_class": "temperature"})
    hass.states.async_set(
        "climate.breezer",
        "fan_only",
        {
            "hvac_modes": ["off", "fan_only"],
            "fan_modes": ["1", "2", "7"],
            "preset_modes": ["manual", "night"],
            "min_temp": 5,
            "max_temp": 25,
            "supported_features": 1 | 8 | 16,
        },
    )
    entry = await _hub(hass)

    # An actuator without thermostats makes no sense.
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "actuator"), context={"source": "user"}
    )
    assert result["reason"] == "no_thermostats"

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "thermostat"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Office",
            "sensor": "sensor.office_t",
            "min_temp": 9,
            "max_temp": 30,
            "target_temp_step": 0.5,
            "presets": {"home_heat": 23, "home_cool": 24, "sleep_heat": 22},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    office = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "actuator"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Breezer",
            "entity_id": "climate.breezer",
            "thermostats": [office],
            "manage_mode": False,
            "veto": True,
        },
    )
    assert result["step_id"] == "heat"
    assert "boost_fan_mode" in result["data_schema"].schema
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"idle": "follow", "boost": "extreme", "start": 1.0, "stop": 1.5, "min_on": 0, "min_off": 0},
    )
    assert result["errors"] == {"base": "stop_above_start"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "idle": "follow",
            "boost": "extreme",
            "start": 2.0,
            "stop": 0.5,
            "min_on": 0,
            "min_off": 0,
            "boost_fan_mode": "7",
        },
    )
    assert result["step_id"] == "cool"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"idle": "follow", "boost": "extreme", "start": 2.0, "stop": 0.5, "min_on": 0, "min_off": 0},
    )
    assert result["step_id"] == "night"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"night_preset": "night", "night_no_boost": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    actuator = next(s for s in entry.subentries.values() if s.subentry_type == "actuator")
    assert actuator.data["heat"]["boost_fan_mode"] == "7"
    assert actuator.data["night_preset"] == "night"

    assert hass.states.get("climate.office").attributes["preset_modes"] == ["none", "home", "sleep"]
    assert hass.states.get("switch.office_boosters_allowed").state == "on"
    assert hass.states.get("select.climate_control_season").state == "heat"
    assert hass.states.get("sensor.breezer_status") is not None


async def test_switch_actuator_offers_only_on_off(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.t", "21.0")
    hass.states.async_set("switch.stove", "off")
    entry = await _hub(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "thermostat"), context={"source": "user"}
    )
    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Kids",
            "sensor": "sensor.t",
            "min_temp": 9,
            "max_temp": 30,
            "target_temp_step": 0.5,
            "presets": {},
        },
    )
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "actuator"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Stove",
            "entity_id": "switch.stove",
            "thermostats": list(entry.subentries),
            "manage_mode": True,
            "veto": True,
        },
    )
    boost = result["data_schema"].schema["boost"]
    assert boost.config["options"] == ["none", "turn_on"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"idle": "off", "boost": "turn_on", "start": 1, "stop": 0, "min_on": 0, "min_off": 0},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"idle": "ignore", "boost": "none", "start": 2, "stop": 0.5, "min_on": 0, "min_off": 0},
    )
    # Not a climate: no night step.
    assert result["type"] is FlowResultType.CREATE_ENTRY
