"""Decision rules without Home Assistant."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load logic.py directly: importing the package would pull in Home Assistant.
_PATH = Path(__file__).parents[2] / "custom_components" / "climate_control" / "logic.py"
_spec = importlib.util.spec_from_file_location("cc_logic", _PATH)
L = importlib.util.module_from_spec(_spec)
sys.modules["cc_logic"] = L
_spec.loader.exec_module(L)

HEAT, COOL = L.Direction.HEAT, L.Direction.COOL


def demand(direction, setpoint, current, **kw):
    delta = setpoint - current if direction is HEAT else current - setpoint
    return L.Demand(direction, setpoint, delta, **kw)


# --- compute_demand -------------------------------------------------------------------------------


def test_heat_season_positive_delta_when_cold():
    d = L.compute_demand(L.Season.HEAT, 20.0, 23.0, None, None, None)
    assert d == L.Demand(HEAT, 23.0, 3.0)


def test_cool_season_positive_delta_when_hot():
    d = L.compute_demand(L.Season.COOL, 27.0, 24.0, None, None, None)
    assert (d.direction, d.delta) == (COOL, 3.0)


def test_no_sensor_no_vote():
    assert L.compute_demand(L.Season.HEAT, None, 23.0, None, None, None) is None


@pytest.mark.parametrize(
    ("current", "last", "expected_dir", "expected_setpoint"),
    [
        (18.0, None, HEAT, 20.0),
        (27.0, None, COOL, 25.0),
        (22.0, COOL, COOL, 25.0),  # dead band keeps the last direction
        (22.0, None, HEAT, 20.0),
    ],
)
def test_heat_cool_range(current, last, expected_dir, expected_setpoint):
    d = L.compute_demand(L.Season.HEAT_COOL, current, None, 20.0, 25.0, last)
    assert (d.direction, d.setpoint) == (expected_dir, expected_setpoint)


def test_heat_cool_dead_band_delta_is_negative():
    d = L.compute_demand(L.Season.HEAT_COOL, 22.0, None, 20.0, 25.0, HEAT)
    assert d.delta == -2.0


# --- boosting a single thermostat ---------------------------------------------------------------

BREEZER = L.ActuatorConfig(
    heat=L.DirectionConfig(L.Idle.FOLLOW, L.Boost.EXTREME, 2.0, 0.5, boost_fan_mode="7"),
    cool=L.DirectionConfig(L.Idle.FOLLOW, L.Boost.EXTREME, 2.0, 0.5, boost_fan_mode="7"),
    night_preset="night",
)
HEATER = L.ActuatorConfig(heat=L.DirectionConfig(L.Idle.OFF, L.Boost.ON_TARGET, 2.0, 0.5))


def run(cfg, currents, setpoint=23.0, direction=HEAT, **kw):
    state, plans = L.BoostState(), []
    for i, current in enumerate(currents):
        plan, state = L.decide(cfg, [(demand(direction, setpoint, current, **kw), 1.0)], state, float(i))
        plans.append(plan)
    return plans, state


def test_hysteresis_on_and_off():
    plans, _ = run(BREEZER, [22.0, 20.5, 21.0, 22.4, 22.6, 21.0])
    assert [p.status for p in plans] == ["idle", "boost", "boost", "boost", "idle", "boost"]
    assert plans[1].action is L.Action.EXTREME
    assert plans[1].fan_mode == "7"
    assert plans[4].action is L.Action.FOLLOW
    assert plans[4].fan_mode is None  # speed override released


def test_boost_only_device_is_off_while_idle():
    plans, _ = run(HEATER, [22.0, 20.0, 23.0])
    assert [p.action for p in plans] == [L.Action.OFF, L.Action.ON_TARGET, L.Action.OFF]


def test_drift_starts_boost_without_setpoint_change():
    plans, _ = run(HEATER, [23.0, 22.0, 21.5, 20.9])
    assert plans[-1].status is L.Status.BOOST


def test_boost_disallowed_by_thermostat_switch():
    plans, _ = run(HEATER, [18.0], boost_allowed=False)
    assert plans[0].action is L.Action.OFF


def test_min_on_holds_boost():
    cfg = L.ActuatorConfig(heat=L.DirectionConfig(L.Idle.OFF, L.Boost.TURN_ON, 2.0, 0.5, min_on=10))
    plans, _ = run(cfg, [20.0, 23.0, 23.0])  # t=0 on, t=1 closed but held
    assert plans[1].status is L.Status.BOOST


def test_min_off_delays_restart():
    cfg = L.ActuatorConfig(heat=L.DirectionConfig(L.Idle.OFF, L.Boost.TURN_ON, 2.0, 0.5, min_off=10))
    plans, _ = run(cfg, [20.0, 23.0, 20.0])
    # Boost can start at t=0 because nothing ran before; restart at t=2 is held by min_off.
    assert [p.status for p in plans] == ["boost", "idle", "idle"]


# --- night ---------------------------------------------------------------------------------------


def test_night_preset_wins_over_boost_speed():
    plans, _ = run(BREEZER, [20.0], night=True)
    assert plans[0].status is L.Status.BOOST
    assert (plans[0].preset, plans[0].fan_mode) == ("night", None)


def test_night_preset_applies_while_following():
    plans, _ = run(BREEZER, [23.0], night=True)
    assert (plans[0].action, plans[0].preset) == (L.Action.FOLLOW, "night")


def test_night_no_boost():
    cfg = L.ActuatorConfig(heat=HEATER.heat, night_no_boost=True)
    plans, _ = run(cfg, [18.0], night=True)
    assert plans[0].action is L.Action.OFF


# --- shared actuator -----------------------------------------------------------------------------

CORRIDOR_AC = L.ActuatorConfig(cool=L.DirectionConfig(L.Idle.OFF, L.Boost.ON_TARGET, 1.5, 0.5))


def test_shared_average_blocks_boost():
    votes = [(demand(COOL, 24.0, 27.0), 1.0), (demand(COOL, 24.0, 23.0), 1.0)]  # +3 and -1
    plan, _ = L.decide(CORRIDOR_AC, votes, L.BoostState(), 0.0)
    assert (plan.delta, plan.status) == (1.0, L.Status.IDLE)


def test_shared_average_starts_boost_and_averages_setpoint():
    votes = [(demand(COOL, 24.0, 27.0), 1.0), (demand(COOL, 22.0, 23.0), 1.0)]  # +3 and +1
    plan, _ = L.decide(CORRIDOR_AC, votes, L.BoostState(), 0.0)
    assert (plan.status, plan.setpoint) == (L.Status.BOOST, 23.0)


def test_overshoot_veto_stops_shared_boost():
    running = L.BoostState(True, COOL, 0.0)
    votes = [(demand(COOL, 24.0, 29.0), 1.0), (demand(COOL, 24.0, 22.0), 1.0)]  # +5 and -2
    plan, state = L.decide(CORRIDOR_AC, votes, running, 100.0)
    assert plan.delta == 1.5
    assert (plan.status, plan.reason, state.on) == (L.Status.IDLE, "overshoot veto", False)


def test_veto_can_be_disabled():
    cfg = L.ActuatorConfig(cool=CORRIDOR_AC.cool, veto=False)
    votes = [(demand(COOL, 24.0, 29.0), 1.0), (demand(COOL, 24.0, 22.0), 1.0)]
    plan, _ = L.decide(cfg, votes, L.BoostState(), 0.0)
    assert plan.status is L.Status.BOOST


def test_shared_boost_needs_every_room_to_allow_it():
    votes = [(demand(COOL, 24.0, 28.0), 1.0), (demand(COOL, 24.0, 28.0, boost_allowed=False), 1.0)]
    plan, _ = L.decide(CORRIDOR_AC, votes, L.BoostState(), 0.0)
    assert plan.action is L.Action.OFF


def test_shared_night_if_any_room_sleeps():
    cfg = L.ActuatorConfig(cool=CORRIDOR_AC.cool, night_fan_mode="quiet")
    votes = [(demand(COOL, 24.0, 28.0), 1.0), (demand(COOL, 24.0, 28.0, night=True), 1.0)]
    plan, _ = L.decide(cfg, votes, L.BoostState(), 0.0)
    assert (plan.night, plan.fan_mode) == (True, "quiet")


def test_conflict_releases_boost_and_turns_off_boost_only_device():
    running = L.BoostState(True, COOL, 0.0)
    votes = [(demand(COOL, 24.0, 28.0), 1.0), (demand(HEAT, 22.0, 19.0), 1.0)]
    plan, state = L.decide(CORRIDOR_AC, votes, running, 5.0)
    assert (plan.status, plan.action, state.on) == (L.Status.CONFLICT, L.Action.OFF, False)


def test_no_demand_leaves_follower_alone():
    plan, _ = L.decide(BREEZER, [], L.BoostState(), 0.0)
    assert (plan.status, plan.action) == (L.Status.NO_DEMAND, L.Action.NONE)


def test_season_never_sends_opposite_direction():
    # Winter: the AC has no heat role, so whatever happens it is only ever switched off.
    plans, _ = run(CORRIDOR_AC, [15.0, 30.0], direction=HEAT)
    assert {p.action for p in plans} == {L.Action.NONE}


# --- clamp ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "lo", "hi", "step", "expected"),
    [(23.5, 5, 25, 1, 24), (20.0, 22, 28, 1, 22), (23.3, 5, 30, 0.5, 23.5), (31, 5, 30, 0.5, 30)],
)
def test_clamp(value, lo, hi, step, expected):
    assert L.clamp_to_device(value, lo, hi, step) == expected
