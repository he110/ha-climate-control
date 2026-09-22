"""Constants for Climate Control."""

from __future__ import annotations

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_HOME,
    PRESET_SLEEP,
)
from homeassistant.const import Platform

DOMAIN = "climate_control"
PLATFORMS = [Platform.CLIMATE, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

SUBENTRY_THERMOSTAT = "thermostat"
SUBENTRY_ACTUATOR = "actuator"

# Thermostat subentry
CONF_SENSOR = "sensor"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_STEP = "target_temp_step"
CONF_PRESETS = "presets"  # section: {"<preset>_heat": float, "<preset>_cool": float}
PRESETS = [PRESET_HOME, PRESET_AWAY, PRESET_SLEEP, PRESET_ECO, PRESET_COMFORT]
NIGHT_PRESET = PRESET_SLEEP

DEFAULT_MIN_TEMP = 7.0
DEFAULT_MAX_TEMP = 35.0
DEFAULT_STEP = 0.5
DEFAULT_TARGET = {"heat": 21.0, "cool": 25.0, "heat_cool_low": 21.0, "heat_cool_high": 25.0}

# Actuator subentry
CONF_ENTITY = "entity_id"
CONF_THERMOSTATS = "thermostats"
CONF_MANAGE_MODE = "manage_mode"
CONF_VETO = "veto"
CONF_IDLE = "idle"
CONF_BOOST = "boost"
CONF_START = "start"
CONF_STOP = "stop"
CONF_MIN_ON = "min_on"  # minutes in the UI
CONF_MIN_OFF = "min_off"
CONF_BOOST_FAN_MODE = "boost_fan_mode"
CONF_BOOST_PRESET = "boost_preset"
CONF_NIGHT_FAN_MODE = "night_fan_mode"
CONF_NIGHT_PRESET = "night_preset"
CONF_NIGHT_NO_BOOST = "night_no_boost"

DEFAULT_START = 2.0
DEFAULT_STOP = 0.5

ACTUATOR_DOMAINS = ["climate", "switch", "fan", "input_boolean", "light"]

# Wait for devices to come up after a restart before commanding them.
STARTUP_DELAY = 30
EVALUATE_INTERVAL = 60
