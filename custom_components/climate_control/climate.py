"""The virtual thermostat: what the user talks to."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.climate import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .const import (
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_PRESETS,
    CONF_SENSOR,
    CONF_STEP,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_STEP,
    DEFAULT_TARGET,
    DOMAIN,
    NIGHT_PRESET,
    PRESETS,
    SUBENTRY_THERMOSTAT,
)
from .engine import Engine, signal_thermostat
from .logic import Demand, Direction, Season, compute_demand

SEASON_MODE = {Season.HEAT: HVACMode.HEAT, Season.COOL: HVACMode.COOL, Season.HEAT_COOL: HVACMode.HEAT_COOL}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine: Engine = entry.runtime_data
    for sub in entry.subentries.values():
        if sub.subentry_type == SUBENTRY_THERMOSTAT:
            async_add_entities(
                [VirtualThermostat(engine, sub.subentry_id, sub.title, dict(sub.data))],
                config_subentry_id=sub.subentry_id,
            )


def thermostat_device(subentry_id: str, title: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, subentry_id)},
        name=title,
        manufacturer="Climate Control",
        model="Virtual thermostat",
    )


@dataclass
class ThermostatMemory(ExtraStoredData):
    hvac_on: bool = True
    preset: str = PRESET_NONE
    manual: dict[str, Any] = field(default_factory=dict)
    last_direction: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "hvac_on": self.hvac_on,
            "preset": self.preset,
            "manual": self.manual,
            "last_direction": self.last_direction,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThermostatMemory:
        return cls(
            bool(data.get("hvac_on", True)),
            data.get("preset") or PRESET_NONE,
            dict(data.get("manual") or {}),
            data.get("last_direction"),
        )


class VirtualThermostat(ClimateEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, engine: Engine, subentry_id: str, title: str, data: dict[str, Any]) -> None:
        self._engine = engine
        self._sid = subentry_id
        self._sensor: str = data[CONF_SENSOR]
        self._presets: dict[str, float] = {k: float(v) for k, v in (data.get(CONF_PRESETS) or {}).items()}
        self._attr_unique_id = f"{subentry_id}_climate"
        self._attr_device_info = thermostat_device(subentry_id, title)
        self._attr_min_temp = float(data.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP))
        self._attr_max_temp = float(data.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP))
        self._attr_target_temperature_step = float(data.get(CONF_STEP, DEFAULT_STEP))
        self._attr_precision = 0.1
        self._mem = ThermostatMemory()
        self._current: float | None = None
        self._runtime = engine.thermostats[subentry_id]
        self._runtime.demand_fn = self._demand

    # --- lifecycle -------------------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (extra := await self.async_get_last_extra_data()) is not None:
            self._mem = ThermostatMemory.from_dict(extra.as_dict())
        self._update_current(self.hass.states.get(self._sensor))
        self.async_on_remove(async_track_state_change_event(self.hass, [self._sensor], self._on_sensor))
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_thermostat(self._engine.entry.entry_id, self._sid),
                self.async_write_ha_state,
            )
        )
        self.async_on_remove(self._engine.add_season_listener(self._on_season))

    async def async_will_remove_from_hass(self) -> None:
        self._runtime.demand_fn = None

    @property
    def extra_restore_state_data(self) -> ThermostatMemory:
        return self._mem

    # --- inputs ----------------------------------------------------------------------------------

    @callback
    def _update_current(self, state: State | None) -> None:
        try:
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                raise ValueError
            self._current = float(state.state)
        except ValueError:
            self._current = None

    @callback
    def _on_sensor(self, event: Event[EventStateChangedData]) -> None:
        self._update_current(event.data["new_state"])
        self._changed()

    @callback
    def _on_season(self) -> None:
        # A preset missing in the new season reads as "none" but is kept for the next season.
        self.async_write_ha_state()

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
        self._engine.request_update()

    # --- model -----------------------------------------------------------------------------------

    @property
    def _season(self) -> Season:
        return self._engine.season

    def _preset_value(self, preset: str, direction: str) -> float | None:
        return self._presets.get(f"{preset}_{direction}")

    def _available_presets(self) -> list[str]:
        season = self._season
        wanted = ["heat", "cool"] if season is Season.HEAT_COOL else [season.value]
        return [p for p in PRESETS if all(self._preset_value(p, d) is not None for d in wanted)]

    def _manual(self, key: str) -> float:
        value = self._mem.manual.get(key)
        return float(value) if value is not None else DEFAULT_TARGET[key]

    def _targets(self) -> tuple[float | None, float | None, float | None]:
        """(target, low, high) for the current season and preset."""
        preset = self._mem.preset
        use_preset = preset != PRESET_NONE and preset in self._available_presets()
        if self._season is Season.HEAT_COOL:
            if use_preset:
                return None, self._preset_value(preset, "heat"), self._preset_value(preset, "cool")
            return None, self._manual("heat_cool_low"), self._manual("heat_cool_high")
        key = self._season.value
        if use_preset:
            return self._preset_value(preset, key), None, None
        return self._manual(key), None, None

    def _demand(self) -> Demand | None:
        if not self._mem.hvac_on:
            return None
        target, low, high = self._targets()
        last = Direction(self._mem.last_direction) if self._mem.last_direction else None
        demand = compute_demand(
            self._season,
            self._current,
            target,
            low,
            high,
            last,
            night=self._mem.preset == NIGHT_PRESET,
            boost_allowed=self._runtime.boost_allowed,
        )
        if demand is not None and demand.direction.value != self._mem.last_direction:
            self._mem.last_direction = demand.direction.value
        return demand

    # --- ClimateEntity ---------------------------------------------------------------------------

    @property
    def available(self) -> bool:
        return True

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if self._season is Season.HEAT_COOL:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        else:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self._available_presets():
            features |= ClimateEntityFeature.PRESET_MODE
        return features

    @property
    def hvac_modes(self) -> list[HVACMode]:
        return [HVACMode.OFF, SEASON_MODE[self._season]]

    @property
    def hvac_mode(self) -> HVACMode:
        return SEASON_MODE[self._season] if self._mem.hvac_on else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        if not self._mem.hvac_on:
            return HVACAction.OFF
        if self._runtime.boosting and self._mem.last_direction:
            return HVACAction.HEATING if self._mem.last_direction == "heat" else HVACAction.COOLING
        return HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        return self._current

    @property
    def target_temperature(self) -> float | None:
        return self._targets()[0]

    @property
    def target_temperature_low(self) -> float | None:
        return self._targets()[1]

    @property
    def target_temperature_high(self) -> float | None:
        return self._targets()[2]

    @property
    def preset_modes(self) -> list[str] | None:
        available = self._available_presets()
        return [PRESET_NONE, *available] if available else None

    @property
    def preset_mode(self) -> str | None:
        if not self._available_presets():
            return None
        return self._mem.preset if self._mem.preset in self._available_presets() else PRESET_NONE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"boosting_actuators": sorted(self._engine.actuators[a].title for a in self._runtime.boosting)}

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            self._mem.hvac_on = False
        elif hvac_mode == SEASON_MODE[self._season]:
            self._mem.hvac_on = True
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="mode_not_in_season",
                translation_placeholders={"mode": str(hvac_mode), "season": self._season.value},
            )
        self._changed()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(SEASON_MODE[self._season])

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (mode := kwargs.get("hvac_mode")) is not None:
            await self.async_set_hvac_mode(mode)
        season = self._season
        if season is Season.HEAT_COOL:
            if ATTR_TARGET_TEMP_LOW not in kwargs or ATTR_TARGET_TEMP_HIGH not in kwargs:
                # Keep the current preset's range as the base for a one-sided change.
                _, low, high = self._targets()
                kwargs.setdefault(ATTR_TARGET_TEMP_LOW, low)
                kwargs.setdefault(ATTR_TARGET_TEMP_HIGH, high)
            self._mem.manual["heat_cool_low"] = float(kwargs[ATTR_TARGET_TEMP_LOW])
            self._mem.manual["heat_cool_high"] = float(kwargs[ATTR_TARGET_TEMP_HIGH])
        elif (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._mem.manual[season.value] = float(temp)
        else:
            return
        self._mem.preset = PRESET_NONE
        self._changed()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode != PRESET_NONE and preset_mode not in self._available_presets():
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="preset_not_available",
                translation_placeholders={"preset": preset_mode},
            )
        self._mem.preset = preset_mode
        self._changed()
