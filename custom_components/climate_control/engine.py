"""The engine: gathers thermostat demands, runs the decision per actuator, and commands devices."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_PRESET_MODE,
    ATTR_PRESET_MODES,
    ATTR_TARGET_TEMP_STEP,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    CONF_NAME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTUATORS,
    CONF_BOOST,
    CONF_BOOST_FAN_MODE,
    CONF_BOOST_PRESET,
    CONF_IDLE,
    CONF_MANAGE_MODE,
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_NIGHT_FAN_MODE,
    CONF_NIGHT_NO_BOOST,
    CONF_NIGHT_PRESET,
    CONF_START,
    CONF_STOP,
    CONF_THERMOSTATS,
    CONF_VETO,
    DEFAULT_START,
    DEFAULT_STOP,
    DOMAIN,
    EVALUATE_INTERVAL,
    STARTUP_DELAY,
    SUBENTRY_THERMOSTAT,
)
from .logic import (
    Action,
    ActuatorConfig,
    Boost,
    BoostState,
    Demand,
    Direction,
    DirectionConfig,
    Idle,
    Plan,
    Season,
    Status,
    clamp_to_device,
    decide,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
UNAVAILABLE = (STATE_UNAVAILABLE, STATE_UNKNOWN)


def signal_actuator(entry_id: str, entity_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_{entity_id}_actuator"


def signal_thermostat(entry_id: str, subentry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_{subentry_id}_thermostat"


def _opt(value: Any) -> str | None:
    return value or None


def direction_config(data: dict[str, Any] | None) -> DirectionConfig:
    data = data or {}
    return DirectionConfig(
        idle=Idle(data.get(CONF_IDLE, Idle.IGNORE)),
        boost=Boost(data.get(CONF_BOOST, Boost.NONE)),
        start=float(data.get(CONF_START, DEFAULT_START)),
        stop=float(data.get(CONF_STOP, DEFAULT_STOP)),
        min_on=float(data.get(CONF_MIN_ON, 0)) * 60,
        min_off=float(data.get(CONF_MIN_OFF, 0)) * 60,
        boost_fan_mode=_opt(data.get(CONF_BOOST_FAN_MODE)),
        boost_preset=_opt(data.get(CONF_BOOST_PRESET)),
    )


def actuator_config(data: dict[str, Any]) -> ActuatorConfig:
    return ActuatorConfig(
        heat=direction_config(data.get(Direction.HEAT)),
        cool=direction_config(data.get(Direction.COOL)),
        veto=bool(data.get(CONF_VETO, True)),
        night_fan_mode=_opt(data.get(CONF_NIGHT_FAN_MODE)),
        night_preset=_opt(data.get(CONF_NIGHT_PRESET)),
        night_no_boost=bool(data.get(CONF_NIGHT_NO_BOOST, False)),
    )


@dataclass
class ThermostatRuntime:
    """What the engine needs from a thermostat entity; the entity keeps it up to date."""

    demand_fn: Callable[[], Demand | None] | None = None
    boost_allowed: bool = True
    boosting: set[str] = field(default_factory=set)  # entity_ids of actuators boosting for it


@dataclass
class ActuatorRuntime:
    title: str
    entity_id: str
    thermostats: list[str]
    config: ActuatorConfig
    manage_mode: bool
    boost: BoostState = field(default_factory=BoostState)
    plan: Plan | None = None
    sent: dict[str, Any] = field(default_factory=dict)  # last command per key; resend only on change
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)  # key -> {"base", "applied"}
    last_error: str | None = None


class Engine:
    """One per config entry. Owns the season and runs every actuator."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.season = Season.HEAT
        self.thermostats: dict[str, ThermostatRuntime] = {
            sid: ThermostatRuntime() for sid in thermostat_ids(entry)
        }
        # Actuators live in the hub's options keyed by entity_id: a shared device is described once.
        self.actuators: dict[str, ActuatorRuntime] = {
            eid: self._build_actuator(eid, data) for eid, data in actuators_of(entry).items()
        }
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._unsubs: list[Callable[[], None]] = []
        self._ready = False
        self._season_listeners: list[Callable[[], None]] = []
        self._debouncer = Debouncer(
            hass, _LOGGER, cooldown=1.0, immediate=False, function=self.async_evaluate
        )

    def _build_actuator(self, entity_id: str, data: dict[str, Any]) -> ActuatorRuntime:
        return ActuatorRuntime(
            title=data.get(CONF_NAME) or entity_id,
            entity_id=entity_id,
            thermostats=[t for t in data.get(CONF_THERMOSTATS, []) if t in self.thermostats],
            config=actuator_config(data),
            manage_mode=bool(data.get(CONF_MANAGE_MODE, True)),
        )

    # --- lifecycle -------------------------------------------------------------------------------

    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        for sid, raw in stored.get("actuators", {}).items():
            act = self.actuators.get(sid)
            if act is None:
                continue
            boost = raw.get("boost", {})
            act.boost = BoostState(
                bool(boost.get("on")),
                Direction(boost["direction"]) if boost.get("direction") else None,
                boost.get("changed_at"),
            )
            act.overrides = raw.get("overrides", {})

        entities = [a.entity_id for a in self.actuators.values()]
        if entities:
            self._unsubs.append(async_track_state_change_event(self.hass, entities, self._on_actuator_state))
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._on_interval, timedelta(seconds=EVALUATE_INTERVAL), cancel_on_shutdown=True
            )
        )
        if self.hass.is_running:
            # Reload after a reconfigure: devices are already up.
            self._go(None)
        else:
            self._unsubs.append(async_at_started(self.hass, self._on_started))

    @callback
    def _on_started(self, _hass: HomeAssistant) -> None:
        self._unsubs.append(async_call_later(self.hass, STARTUP_DELAY, self._go))

    @callback
    def _go(self, _now: Any) -> None:
        self._ready = True
        self.request_update()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._debouncer.async_shutdown()
        await self._store.async_save(self._snapshot())

    def _snapshot(self) -> dict[str, Any]:
        return {
            "actuators": {
                sid: {"boost": asdict(act.boost), "overrides": act.overrides}
                for sid, act in self.actuators.items()
            }
        }

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(self._snapshot, 5)

    # --- inputs ----------------------------------------------------------------------------------

    @callback
    def request_update(self) -> None:
        if self._ready:
            self.hass.async_create_task(self._debouncer.async_call(), eager_start=True)

    @callback
    def set_season(self, season: Season) -> None:
        if season is self.season:
            return
        self.season = season
        for listener in self._season_listeners:
            listener()
        self.request_update()

    @callback
    def add_season_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._season_listeners.append(listener)
        return lambda: self._season_listeners.remove(listener)

    @callback
    def _on_interval(self, _now: Any) -> None:
        self.request_update()

    @callback
    def _on_actuator_state(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        was_up = old is not None and old.state not in UNAVAILABLE
        is_up = new is not None and new.state not in UNAVAILABLE
        if is_up and not was_up:
            # The device came back (or restarted): it may have forgotten what we told it.
            for act in self.actuators.values():
                if act.entity_id == event.data["entity_id"]:
                    act.sent.clear()
            self.request_update()

    # --- evaluation ------------------------------------------------------------------------------

    async def async_evaluate(self) -> None:
        now = dt_util.utcnow().timestamp()
        changed_thermostats: set[str] = set()
        # Once per pass: a heat_cool thermostat updates its dead-band memory while computing.
        demands = {tid: rt.demand_fn() if rt.demand_fn else None for tid, rt in self.thermostats.items()}
        for act in self.actuators.values():
            votes = [(demand, 1.0) for tid in act.thermostats if (demand := demands.get(tid)) is not None]
            plan, boost = decide(act.config, votes, act.boost, now)
            if boost != act.boost:
                act.boost = boost
                self._save()
            for tid in act.thermostats:
                rt = self.thermostats[tid]
                was = act.entity_id in rt.boosting
                if plan.status is Status.BOOST and not was:
                    rt.boosting.add(act.entity_id)
                    changed_thermostats.add(tid)
                elif plan.status is not Status.BOOST and was:
                    rt.boosting.discard(act.entity_id)
                    changed_thermostats.add(tid)
            act.plan = plan
            await self._apply(act, plan)
            async_dispatcher_send(self.hass, signal_actuator(self.entry.entry_id, act.entity_id))
        for tid in changed_thermostats:
            async_dispatcher_send(self.hass, signal_thermostat(self.entry.entry_id, tid))

    async def _apply(self, act: ActuatorRuntime, plan: Plan) -> None:
        state = self.hass.states.get(act.entity_id)
        if state is None or state.state in UNAVAILABLE:
            return
        domain = act.entity_id.split(".", 1)[0]
        commands = (
            self._climate_commands(act, plan, state) if domain == "climate" else self._onoff_commands(plan)
        )
        commands += self._override_commands(act, plan, state)
        act.last_error = None
        for key, value, service, data in commands:
            if act.sent.get(key, object()) == value:
                continue
            svc_domain = (
                "homeassistant" if service in ("turn_on", "turn_off") and domain != "climate" else domain
            )
            try:
                await self.hass.services.async_call(
                    svc_domain, service, {ATTR_ENTITY_ID: act.entity_id, **data}, blocking=True
                )
            except (HomeAssistantError, ValueError) as err:
                act.last_error = str(err)
                act.sent.pop(key, None)
                _LOGGER.warning("%s: %s %s failed: %s", act.title, service, data, err)
                continue
            act.sent[key] = value

    @staticmethod
    def _onoff_commands(plan: Plan) -> list[tuple[str, Any, str, dict[str, Any]]]:
        if plan.action is Action.OFF:
            return [("power", "off", "turn_off", {})]
        if plan.action in (Action.TURN_ON, Action.ON_TARGET, Action.EXTREME):
            return [("power", "on", "turn_on", {})]
        return []

    def _climate_commands(
        self, act: ActuatorRuntime, plan: Plan, state: State
    ) -> list[tuple[str, Any, str, dict[str, Any]]]:
        attrs = state.attributes
        modes = attrs.get(ATTR_HVAC_MODES) or []
        features = attrs.get(ATTR_SUPPORTED_FEATURES, 0)
        has_temp = bool(features & ClimateEntityFeature.TARGET_TEMPERATURE)
        direction = plan.direction.value if plan.direction else None
        out: list[tuple[str, Any, str, dict[str, Any]]] = []

        def mode(value: str) -> None:
            out.append(("mode", value, "set_hvac_mode", {ATTR_HVAC_MODE: value}))

        def temp(value: float) -> None:
            if has_temp:
                out.append(("temperature", value, "set_temperature", {ATTR_TEMPERATURE: value}))

        def fit(value: float) -> float:
            return clamp_to_device(
                value, attrs.get(ATTR_MIN_TEMP), attrs.get(ATTR_MAX_TEMP), attrs.get(ATTR_TARGET_TEMP_STEP)
            )

        match plan.action:
            case Action.OFF:
                mode(HVACMode.OFF)
            case Action.FOLLOW:
                if act.manage_mode and direction in modes:
                    mode(direction)
                temp(fit(plan.setpoint))
            case Action.ON_TARGET:
                if direction in modes:
                    mode(direction)
                else:
                    out.append(("mode", "on", "turn_on", {}))
                temp(fit(plan.setpoint))
            case Action.TURN_ON:
                out.append(("mode", "on", "turn_on", {}))
            case Action.EXTREME:
                if act.manage_mode and direction in modes:
                    mode(direction)
                edge = (
                    attrs.get(ATTR_MAX_TEMP) if plan.direction is Direction.HEAT else attrs.get(ATTR_MIN_TEMP)
                )
                if edge is not None:
                    temp(float(edge))
        return out

    def _override_commands(
        self, act: ActuatorRuntime, plan: Plan, state: State
    ) -> list[tuple[str, Any, str, dict[str, Any]]]:
        """Hold boost/night fan speed and preset; give the old value back when we are done.

        The old value is restored only if the device still shows ours: if a person switched it in
        the meantime, their choice stays.
        """
        if not act.entity_id.startswith("climate."):
            return []
        out: list[tuple[str, Any, str, dict[str, Any]]] = []
        for key, wanted, attr, options_attr, service in (
            ("preset", plan.preset, ATTR_PRESET_MODE, ATTR_PRESET_MODES, "set_preset_mode"),
            ("fan", plan.fan_mode, ATTR_FAN_MODE, ATTR_FAN_MODES, "set_fan_mode"),
        ):
            current = state.attributes.get(attr)
            options = state.attributes.get(options_attr) or []
            held = act.overrides.get(key)
            if wanted and wanted in options:
                if held is None:
                    act.overrides[key] = {"base": current, "applied": wanted}
                    self._save()
                elif held["applied"] != wanted:
                    held["applied"] = wanted
                    self._save()
                out.append((key, wanted, service, {attr: wanted}))
            elif held is not None:
                del act.overrides[key]
                self._save()
                act.sent.pop(key, None)
                base = held.get("base")
                if current == held.get("applied") and base and base in options and base != current:
                    out.append((key, base, service, {attr: base}))
        return out


def actuators_of(entry: ConfigEntry) -> dict[str, dict[str, Any]]:
    return dict(entry.options.get(CONF_ACTUATORS) or {})


def thermostat_ids(entry: ConfigEntry) -> list[str]:
    return [s.subentry_id for s in entry.subentries.values() if s.subentry_type == SUBENTRY_THERMOSTAT]
