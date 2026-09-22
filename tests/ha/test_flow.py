"""Hub and thermostat flows; devices are managed from a thermostat's «Configure» menu."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.climate_control.const import DOMAIN

DIR = {"start": 2.0, "stop": 0.5, "min_on": 0, "min_off": 0}
THERMOSTAT = {"min_temp": 9, "max_temp": 30, "target_temp_step": 0.5}


async def _hub(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    return result["result"]


async def _thermostat(hass: HomeAssistant, entry, name: str, sensor: str, presets=None) -> str:
    before = set(entry.subentries)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "thermostat"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": name, "sensor": sensor, **THERMOSTAT, "presets": presets or {}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return (set(entry.subentries) - before).pop()


async def _menu(hass: HomeAssistant, entry, sid: str, option: str):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "thermostat"), context={"source": SOURCE_RECONFIGURE, "subentry_id": sid}
    )
    assert result["type"] is FlowResultType.MENU
    return result, await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": option}
    )


async def test_thermostat_and_devices(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.office_t", "21.0", {"device_class": "temperature"})
    hass.states.async_set("sensor.bedroom_t", "21.0", {"device_class": "temperature"})
    hass.states.async_set(
        "climate.breezer",
        "fan_only",
        {
            "friendly_name": "Office breezer",
            "hvac_modes": ["off", "fan_only"],
            "fan_modes": ["1", "2", "7"],
            "preset_modes": ["manual", "night"],
            "min_temp": 5,
            "max_temp": 25,
            "supported_features": 1 | 8 | 16,
        },
    )
    hass.states.async_set("climate.ac", "off", {"friendly_name": "Hall AC", "hvac_modes": ["off", "cool"]})
    entry = await _hub(hass)
    office = await _thermostat(
        hass, entry, "Office", "sensor.office_t", {"home_heat": 23, "home_cool": 24, "sleep_heat": 22}
    )
    bedroom = await _thermostat(hass, entry, "Bedroom", "sensor.bedroom_t")
    assert hass.states.get("climate.office").attributes["preset_modes"] == ["none", "home", "sleep"]

    # No devices yet: the menu offers only settings and «add».
    menu, result = await _menu(hass, entry, office, "add_device")
    assert menu["menu_options"] == ["settings", "add_device"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"entity_id": "climate.breezer"}
    )
    assert result["step_id"] == "device"
    assert result["data_schema"]({})["name"] == "Office breezer"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Breezer", "manage_mode": False, "veto": True}
    )
    assert result["step_id"] == "heat"
    assert "boost_fan_mode" in result["data_schema"].schema
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"idle": "follow", "boost": "extreme", **DIR, "start": 0.5, "stop": 1.0}
    )
    assert result["errors"] == {"base": "stop_above_start"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"idle": "follow", "boost": "extreme", **DIR, "boost_fan_mode": "7"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"idle": "follow", "boost": "extreme", **DIR}
    )
    assert result["step_id"] == "night"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"night_preset": "night", "night_no_boost": False}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    breezer = entry.options["actuators"]["climate.breezer"]
    assert breezer["thermostats"] == [office]
    assert breezer["heat"]["boost_fan_mode"] == "7"
    # Status sensor lives on the thermostat's device, no device of its own.
    assert hass.states.get("sensor.office_breezer") is not None

    # The same AC added to two thermostats is one shared actuator.
    for sid in (office, bedroom):
        _, result = await _menu(hass, entry, sid, "add_device")
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"entity_id": "climate.ac"}
        )
        if sid == bedroom:
            assert result["description_placeholders"]["shared"] == "Office"
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"name": "Hall AC", "manage_mode": True, "veto": True}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"idle": "off", "boost": "none", **DIR}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"idle": "off", "boost": "on_target", **DIR}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"night_no_boost": False}
        )
        await hass.async_block_till_done()
    assert entry.options["actuators"]["climate.ac"]["thermostats"] == [office, bedroom]
    assert hass.states.get("sensor.bedroom_hall_ac") is not None

    # Removing from one thermostat keeps it for the other.
    _, result = await _menu(hass, entry, office, "remove_device")
    await hass.config_entries.subentries.async_configure(result["flow_id"], {"entity_id": "climate.ac"})
    await hass.async_block_till_done()
    assert entry.options["actuators"]["climate.ac"]["thermostats"] == [bedroom]

    # Removing the thermostat drops its devices that serve nobody else.
    hass.config_entries.async_remove_subentry(entry, office)
    await hass.async_block_till_done()
    assert "climate.breezer" not in entry.options["actuators"]
    assert "climate.ac" in entry.options["actuators"]


async def test_settings_from_menu(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.t", "21.0")
    entry = await _hub(hass)
    sid = await _thermostat(hass, entry, "Kids", "sensor.t")
    _, result = await _menu(hass, entry, sid, "settings")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Nursery", "sensor": "sensor.t", **THERMOSTAT, "presets": {"away_cool": 27}},
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries[sid].title == "Nursery"
    assert entry.subentries[sid].data["presets"] == {"away_cool": 27}


async def test_switch_device_offers_only_on_off(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.t", "21.0")
    hass.states.async_set("switch.stove", "off")
    entry = await _hub(hass)
    sid = await _thermostat(hass, entry, "Kids", "sensor.t")
    _, result = await _menu(hass, entry, sid, "add_device")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"entity_id": "switch.stove"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Stove", "manage_mode": True, "veto": True}
    )
    assert result["data_schema"].schema["boost"].config["options"] == ["none", "turn_on"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"idle": "off", "boost": "turn_on", **DIR}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"idle": "ignore", "boost": "none", **DIR}
    )
    assert result["reason"] == "reconfigure_successful"  # not a climate: no night step


async def test_migration_from_actuator_subentries(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.t", "21.0")
    hass.states.async_set("switch.stove", "off")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Climate Control",
        version=1,
        data={},
        subentries_data=[
            {
                "subentry_id": "kids",
                "subentry_type": "thermostat",
                "title": "Kids",
                "unique_id": None,
                "data": {"sensor": "sensor.t", **THERMOSTAT, "presets": {}},
            },
            {
                "subentry_id": "a1",
                "subentry_type": "actuator",
                "title": "Stove",
                "unique_id": None,
                "data": {
                    "entity_id": "switch.stove",
                    "thermostats": ["kids"],
                    "heat": {"idle": "off", "boost": "turn_on", **DIR},
                    "cool": {"idle": "ignore", "boost": "none", **DIR},
                },
            },
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2
    assert [s.subentry_type for s in entry.subentries.values()] == ["thermostat"]
    stove = entry.options["actuators"]["switch.stove"]
    assert (stove["name"], stove["thermostats"]) == ("Stove", ["kids"])
    assert hass.states.get("sensor.kids_stove") is not None


async def test_renamed_thermostat_keeps_driving(hass: HomeAssistant) -> None:
    """Renaming the entity_id re-adds the entity: it must re-register with the engine."""
    hass.states.async_set("sensor.t", "20.0")
    entry = await _hub(hass)
    sid = await _thermostat(hass, entry, "Kids", "sensor.t")
    er.async_get(hass).async_update_entity("climate.kids", new_entity_id="climate.kids_renamed")
    await hass.async_block_till_done()
    assert entry.runtime_data.thermostats[sid].demand_fn is not None
