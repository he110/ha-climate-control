"""Pure decision logic: thermostat demand, arbitration between thermostats, boost hysteresis.

Nothing here imports Home Assistant, so the rules can be tested exhaustively and cheaply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Direction(StrEnum):
    HEAT = "heat"
    COOL = "cool"


class Season(StrEnum):
    HEAT = "heat"
    COOL = "cool"
    HEAT_COOL = "heat_cool"


class Idle(StrEnum):
    """What an actuator does while the gap is small."""

    FOLLOW = "follow"
    OFF = "off"
    IGNORE = "ignore"


class Boost(StrEnum):
    """What an actuator does while boosting."""

    NONE = "none"
    TURN_ON = "turn_on"
    ON_TARGET = "on_target"
    EXTREME = "extreme"


class Action(StrEnum):
    """Abstract command for an actuator; the HA layer maps it onto services per domain."""

    NONE = "none"
    OFF = "off"
    FOLLOW = "follow"
    TURN_ON = "turn_on"
    ON_TARGET = "on_target"
    EXTREME = "extreme"


class Status(StrEnum):
    MANUAL = "manual"
    NO_DEMAND = "no_demand"
    CONFLICT = "conflict"
    IDLE = "idle"
    BOOST = "boost"


@dataclass(frozen=True)
class Demand:
    """What a single thermostat wants right now.

    ``delta`` is positive while the room lags behind the setpoint in ``direction``
    and negative once it has overshot.
    """

    direction: Direction
    setpoint: float
    delta: float
    night: bool = False
    boost_allowed: bool = True


def compute_demand(
    season: Season,
    current: float | None,
    target: float | None,
    low: float | None,
    high: float | None,
    last_direction: Direction | None,
    *,
    night: bool = False,
    boost_allowed: bool = True,
) -> Demand | None:
    """Demand of an active thermostat, or None when it cannot vote (no sensor value)."""
    if current is None:
        return None
    if season is Season.HEAT_COOL:
        if low is None or high is None:
            return None
        if current < low:
            direction = Direction.HEAT
        elif current > high:
            direction = Direction.COOL
        else:
            # Dead band: keep going the way we went, so the room is not flipped back and forth.
            direction = last_direction or Direction.HEAT
        setpoint = low if direction is Direction.HEAT else high
    else:
        if target is None:
            return None
        direction = Direction(season.value)
        setpoint = target
    delta = setpoint - current if direction is Direction.HEAT else current - setpoint
    return Demand(direction, setpoint, round(delta, 3), night, boost_allowed)


@dataclass(frozen=True)
class DirectionConfig:
    idle: Idle = Idle.IGNORE
    boost: Boost = Boost.NONE
    start: float = 2.0
    stop: float = 0.5
    min_on: float = 0.0  # seconds
    min_off: float = 0.0  # seconds
    boost_fan_mode: str | None = None
    boost_preset: str | None = None


@dataclass(frozen=True)
class ActuatorConfig:
    heat: DirectionConfig = field(default_factory=DirectionConfig)
    cool: DirectionConfig = field(default_factory=DirectionConfig)
    veto: bool = True
    night_fan_mode: str | None = None
    night_preset: str | None = None
    night_no_boost: bool = False

    def for_direction(self, direction: Direction) -> DirectionConfig:
        return self.heat if direction is Direction.HEAT else self.cool


@dataclass(frozen=True)
class BoostState:
    """Persisted between evaluations: hysteresis needs memory."""

    on: bool = False
    direction: Direction | None = None
    changed_at: float | None = None  # epoch seconds


@dataclass(frozen=True)
class Plan:
    status: Status
    action: Action = Action.NONE
    direction: Direction | None = None
    setpoint: float | None = None
    delta: float | None = None
    night: bool = False
    fan_mode: str | None = None  # override to hold, None = release ours
    preset: str | None = None
    reason: str = ""


_BOOST_ACTION = {
    Boost.TURN_ON: Action.TURN_ON,
    Boost.ON_TARGET: Action.ON_TARGET,
    Boost.EXTREME: Action.EXTREME,
}
_IDLE_ACTION = {Idle.FOLLOW: Action.FOLLOW, Idle.OFF: Action.OFF, Idle.IGNORE: Action.NONE}


def _release(
    cfg: ActuatorConfig, state: BoostState, now: float, status: Status, reason: str
) -> tuple[Plan, BoostState]:
    """Nobody (coherent) is asking: drop a running boost, otherwise leave the device alone."""
    action = Action.NONE
    # A boost-only device (idle=off) must not keep running without a demand behind it.
    if state.on and state.direction is not None and cfg.for_direction(state.direction).idle is Idle.OFF:
        action = Action.OFF
    new_state = BoostState(False, None, now) if state.on else state
    return Plan(status, action, reason=reason), new_state


def _held(state: BoostState, now: float, minimum: float) -> bool:
    return state.changed_at is not None and now - state.changed_at < minimum


def decide(
    cfg: ActuatorConfig,
    votes: list[tuple[Demand, float]],
    state: BoostState,
    now: float,
) -> tuple[Plan, BoostState]:
    """Combine the demands of all linked thermostats into one plan for the actuator.

    ``votes`` are (demand, weight) pairs of thermostats that are on and have a sensor value.
    """
    if not votes:
        return _release(cfg, state, now, Status.NO_DEMAND, "no active thermostat")
    directions = {demand.direction for demand, _ in votes}
    if len(directions) > 1:
        return _release(cfg, state, now, Status.CONFLICT, "thermostats want opposite directions")

    direction = directions.pop()
    dc = cfg.for_direction(direction)
    total = sum(weight for _, weight in votes) or 1.0
    setpoint = sum(d.setpoint * w for d, w in votes) / total
    delta = round(sum(d.delta * w for d, w in votes) / total, 3)
    night = any(d.night for d, _ in votes)

    allowed = dc.boost is not Boost.NONE and all(d.boost_allowed for d, _ in votes)
    if night and cfg.night_no_boost:
        allowed = False
    vetoed = cfg.veto and any(d.delta <= -dc.start for d, _ in votes)

    boosting = state.on and state.direction is direction
    reason = ""
    if boosting:
        if not allowed:
            boosting, reason = False, "boost not allowed"
        elif vetoed:
            boosting, reason = False, "overshoot veto"
        elif delta <= dc.stop and not _held(state, now, dc.min_on):
            boosting, reason = False, "gap closed"
        else:
            reason = "boosting"
    elif allowed and not vetoed and delta >= dc.start and not _held(state, now, dc.min_off):
        boosting, reason = True, "gap too large"
    else:
        reason = "within threshold"

    if boosting != state.on or (boosting and state.direction is not direction):
        new_state = BoostState(boosting, direction if boosting else None, now)
    else:
        new_state = state

    action = _BOOST_ACTION[dc.boost] if boosting else _IDLE_ACTION[dc.idle]
    fan_mode = preset = None
    if action not in (Action.NONE, Action.OFF):
        # Night beats boost: a quiet room is worth a slower catch-up.
        if night and (cfg.night_fan_mode or cfg.night_preset):
            fan_mode, preset = cfg.night_fan_mode, cfg.night_preset
        elif boosting:
            fan_mode, preset = dc.boost_fan_mode, dc.boost_preset

    plan = Plan(
        Status.BOOST if boosting else Status.IDLE,
        action,
        direction,
        round(setpoint, 2),
        delta,
        night,
        fan_mode,
        preset,
        reason,
    )
    return plan, new_state


def clamp_to_device(
    value: float, min_temp: float | None, max_temp: float | None, step: float | None
) -> float:
    """Fit a setpoint into what the device accepts: its range and its step."""
    if step and step > 0:
        value = round(round(value / step) * step, 3)
    if min_temp is not None:
        value = max(value, min_temp)
    if max_temp is not None:
        value = min(value, max_temp)
    return value


__all__ = [
    "Action",
    "ActuatorConfig",
    "Boost",
    "BoostState",
    "Demand",
    "Direction",
    "DirectionConfig",
    "Idle",
    "Plan",
    "Season",
    "Status",
    "clamp_to_device",
    "compute_demand",
    "decide",
]
