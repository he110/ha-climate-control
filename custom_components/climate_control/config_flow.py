"""Config flow: a single hub entry; thermostats and actuators are its subentries."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.climate import ATTR_FAN_MODES, ATTR_PRESET_MODES
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    ACTUATOR_DOMAINS,
    CONF_ACTUATORS,
    CONF_BOOST,
    CONF_BOOST_FAN_MODE,
    CONF_BOOST_PRESET,
    CONF_ENTITY,
    CONF_IDLE,
    CONF_MANAGE_MODE,
    CONF_MAX_TEMP,
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_MIN_TEMP,
    CONF_NIGHT_FAN_MODE,
    CONF_NIGHT_NO_BOOST,
    CONF_NIGHT_PRESET,
    CONF_PRESETS,
    CONF_SENSOR,
    CONF_START,
    CONF_STEP,
    CONF_STOP,
    CONF_THERMOSTATS,
    CONF_VETO,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_START,
    DEFAULT_STEP,
    DEFAULT_STOP,
    DOMAIN,
    PRESETS,
    SUBENTRY_THERMOSTAT,
)
from .logic import Boost, Direction, Idle


class ClimateControlConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Climate Control", data={})
        return self.async_show_form(step_id="user")

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_THERMOSTAT: ThermostatSubentryFlow}


def _number(minimum: float, maximum: float, step: float, unit: str | None = "°C") -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _suggest(values: dict[str, Any], key: str, default: Any = None) -> dict[str, Any]:
    value = values.get(key, default)
    return {} if value is None else {"suggested_value": value}


# --- thermostat and its devices ------------------------------------------------------------------


def _select(
    key: str, options: list[str], *, multiple: bool = False, custom: bool = False
) -> selector.SelectSelector:
    config = selector.SelectSelectorConfig(
        options=options, multiple=multiple, custom_value=custom, mode=selector.SelectSelectorMode.DROPDOWN
    )
    if not custom:
        # Device-provided values (fan speeds, presets) are shown as is; our enums are translated.
        config["translation_key"] = key
    return selector.SelectSelector(config)


class ThermostatSubentryFlow(ConfigSubentryFlow):
    """A thermostat and, under «Reconfigure», the devices attached to it.

    Devices are stored once per entity in the hub options: a device shared by two thermostats has one
    set of settings, whichever thermostat it is edited from.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._title = ""
        self._entity = ""

    # --- thermostat itself --------------------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._form("user", user_input, {})

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        options = ["settings", "add_device"]
        if self._linked():
            options += ["edit_device", "remove_device"]
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=options,
            description_placeholders={"name": self._get_reconfigure_subentry().title},
        )

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        sub = self._get_reconfigure_subentry()
        return await self._form("settings", user_input, {CONF_NAME: sub.title, **sub.data})

    async def _form(
        self, step_id: str, user_input: dict[str, Any] | None, current: dict[str, Any]
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = dict(user_input)
            title = data.pop(CONF_NAME)
            data[CONF_PRESETS] = {k: v for k, v in (data.get(CONF_PRESETS) or {}).items() if v is not None}
            if data[CONF_MIN_TEMP] >= data[CONF_MAX_TEMP]:
                errors["base"] = "min_above_max"
            else:
                if step_id == "settings":
                    return self.async_update_and_abort(
                        self._get_entry(), self._get_reconfigure_subentry(), title=title, data=data
                    )
                return self.async_create_entry(title=title, data=data)
            current = {CONF_NAME: title, **data}

        presets = current.get(CONF_PRESETS) or {}
        preset_fields: dict[Any, Any] = {}
        for preset in PRESETS:
            for direction in (Direction.HEAT, Direction.COOL):
                key = f"{preset}_{direction}"
                preset_fields[vol.Optional(key, description=_suggest(presets, key))] = _number(5, 35, 0.5)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, description=_suggest(current, CONF_NAME)): str,
                vol.Required(
                    CONF_SENSOR, description=_suggest(current, CONF_SENSOR)
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                vol.Required(CONF_MIN_TEMP, default=current.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)): _number(
                    0, 40, 0.5
                ),
                vol.Required(CONF_MAX_TEMP, default=current.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)): _number(
                    0, 40, 0.5
                ),
                vol.Required(CONF_STEP, default=current.get(CONF_STEP, DEFAULT_STEP)): _number(0.1, 1, 0.1),
                vol.Required(CONF_PRESETS): section(vol.Schema(preset_fields), {"collapsed": not presets}),
            }
        )
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    # --- devices -----------------------------------------------------------------------------------

    @property
    def _sid(self) -> str:
        return self._get_reconfigure_subentry().subentry_id

    def _actuators(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in (self._get_entry().options.get(CONF_ACTUATORS) or {}).items()}

    def _linked(self) -> dict[str, dict[str, Any]]:
        return {k: v for k, v in self._actuators().items() if self._sid in v.get(CONF_THERMOSTATS, [])}

    def _device_options(self, actuators: dict[str, dict[str, Any]]) -> list[selector.SelectOptionDict]:
        return [selector.SelectOptionDict(value=k, label=v.get(CONF_NAME) or k) for k, v in actuators.items()]

    def _others(self) -> str:
        subs = self._get_entry().subentries
        names = [subs[t].title for t in self._data.get(CONF_THERMOSTATS, []) if t != self._sid and t in subs]
        return ", ".join(names)

    @property
    def _is_climate(self) -> bool:
        return self._entity.startswith("climate.")

    def _attr_options(self, attr: str) -> list[str]:
        state = self.hass.states.get(self._entity)
        return [str(o) for o in (state.attributes.get(attr) or [])] if state else []

    async def async_step_add_device(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._entity = user_input[CONF_ENTITY]
            existing = self._actuators().get(self._entity)
            if existing is not None:
                # Already serves another thermostat: link it here and edit the shared settings.
                self._data = existing
            else:
                state = self.hass.states.get(self._entity)
                self._data = {CONF_NAME: state.name if state else self._entity, CONF_THERMOSTATS: []}
            if self._sid not in self._data[CONF_THERMOSTATS]:
                self._data[CONF_THERMOSTATS] = [*self._data[CONF_THERMOSTATS], self._sid]
            return await self.async_step_device()
        schema = vol.Schema(
            {
                vol.Required(CONF_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=ACTUATOR_DOMAINS, exclude_entities=list(self._linked())
                    )
                )
            }
        )
        return self.async_show_form(step_id="add_device", data_schema=schema)

    async def async_step_edit_device(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._entity = user_input[CONF_ENTITY]
            self._data = self._actuators()[self._entity]
            return await self.async_step_device()
        schema = vol.Schema(
            {vol.Required(CONF_ENTITY): _select_options(self._device_options(self._linked()))}
        )
        return self.async_show_form(step_id="edit_device", data_schema=schema)

    async def async_step_remove_device(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            actuators = self._actuators()
            data = actuators[user_input[CONF_ENTITY]]
            data[CONF_THERMOSTATS] = [t for t in data.get(CONF_THERMOSTATS, []) if t != self._sid]
            if not data[CONF_THERMOSTATS]:
                del actuators[user_input[CONF_ENTITY]]
            return self._save(actuators)
        schema = vol.Schema(
            {vol.Required(CONF_ENTITY): _select_options(self._device_options(self._linked()))}
        )
        return self.async_show_form(step_id="remove_device", data_schema=schema)

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_heat()
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=self._data.get(CONF_NAME, self._entity)): str,
                vol.Required(CONF_MANAGE_MODE, default=self._data.get(CONF_MANAGE_MODE, True)): bool,
                vol.Required(CONF_VETO, default=self._data.get(CONF_VETO, True)): bool,
            }
        )
        others = self._others()
        return self.async_show_form(
            step_id="device",
            data_schema=schema,
            description_placeholders={"entity": self._entity, "shared": others or "—"},
        )

    async def async_step_heat(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_direction(Direction.HEAT, user_input, self.async_step_cool)

    async def async_step_cool(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        next_step = self.async_step_night if self._is_climate else self._finish
        return await self._step_direction(Direction.COOL, user_input, next_step)

    async def _step_direction(self, direction: Direction, user_input: dict[str, Any] | None, next_step: Any):
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_STOP, 0) >= user_input.get(CONF_START, 0):
                errors["base"] = "stop_above_start"
            else:
                self._data[direction.value] = user_input
                return await next_step()

        current = self._data.get(direction.value) or {}
        climate = self._is_climate
        idle_options = [i.value for i in Idle] if climate else [Idle.OFF.value, Idle.IGNORE.value]
        boost_options = [b.value for b in Boost] if climate else [Boost.NONE.value, Boost.TURN_ON.value]
        default_idle = current.get(CONF_IDLE) or (
            Idle.FOLLOW.value if climate and direction is Direction.HEAT else Idle.IGNORE.value
        )
        fields: dict[Any, Any] = {
            vol.Required(CONF_IDLE, default=default_idle): _select("idle", idle_options),
            vol.Required(CONF_BOOST, default=current.get(CONF_BOOST, Boost.NONE.value)): _select(
                "boost", boost_options
            ),
            vol.Required(CONF_START, default=current.get(CONF_START, DEFAULT_START)): _number(0.1, 10, 0.1),
            vol.Required(CONF_STOP, default=current.get(CONF_STOP, DEFAULT_STOP)): _number(-5, 10, 0.1),
            vol.Required(CONF_MIN_ON, default=current.get(CONF_MIN_ON, 0)): _number(0, 240, 1, "min"),
            vol.Required(CONF_MIN_OFF, default=current.get(CONF_MIN_OFF, 0)): _number(0, 240, 1, "min"),
        }
        if climate:
            if fans := self._attr_options(ATTR_FAN_MODES):
                fields[
                    vol.Optional(CONF_BOOST_FAN_MODE, description=_suggest(current, CONF_BOOST_FAN_MODE))
                ] = _select(CONF_BOOST_FAN_MODE, fans, custom=True)
            if presets := self._attr_options(ATTR_PRESET_MODES):
                fields[vol.Optional(CONF_BOOST_PRESET, description=_suggest(current, CONF_BOOST_PRESET))] = (
                    _select(CONF_BOOST_PRESET, presets, custom=True)
                )
        return self.async_show_form(
            step_id=direction.value,
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"name": self._data.get(CONF_NAME, self._entity)},
        )

    async def async_step_night(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            for key in (CONF_NIGHT_PRESET, CONF_NIGHT_FAN_MODE):
                self._data.pop(key, None)
            self._data.update(user_input)
            return await self._finish()
        fields: dict[Any, Any] = {}
        if presets := self._attr_options(ATTR_PRESET_MODES):
            fields[vol.Optional(CONF_NIGHT_PRESET, description=_suggest(self._data, CONF_NIGHT_PRESET))] = (
                _select(CONF_NIGHT_PRESET, presets, custom=True)
            )
        if fans := self._attr_options(ATTR_FAN_MODES):
            fields[
                vol.Optional(CONF_NIGHT_FAN_MODE, description=_suggest(self._data, CONF_NIGHT_FAN_MODE))
            ] = _select(CONF_NIGHT_FAN_MODE, fans, custom=True)
        fields[vol.Required(CONF_NIGHT_NO_BOOST, default=self._data.get(CONF_NIGHT_NO_BOOST, False))] = bool
        return self.async_show_form(
            step_id="night",
            data_schema=vol.Schema(fields),
            description_placeholders={"name": self._data.get(CONF_NAME, self._entity)},
        )

    async def _finish(self) -> SubentryFlowResult:
        actuators = self._actuators()
        actuators[self._entity] = self._data
        return self._save(actuators)

    def _save(self, actuators: dict[str, dict[str, Any]]) -> SubentryFlowResult:
        entry = self._get_entry()
        # The hub's update listener reloads the entry: the engine picks the change up at once.
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_ACTUATORS: actuators}
        )
        return self.async_abort(reason="reconfigure_successful")


def _select_options(options: list[selector.SelectOptionDict]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.LIST)
    )
