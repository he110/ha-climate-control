# Climate Control for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Validate](https://github.com/he110/ha-climate-control/actions/workflows/validate.yaml/badge.svg)](https://github.com/he110/ha-climate-control/actions/workflows/validate.yaml)
[![Tests](https://github.com/he110/ha-climate-control/actions/workflows/tests.yaml/badge.svg)](https://github.com/he110/ha-climate-control/actions/workflows/tests.yaml)
[![Release](https://img.shields.io/github/v/release/he110/ha-climate-control)](https://github.com/he110/ha-climate-control/releases)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=he110&repository=ha-climate-control&category=integration)

Set **one target temperature** and let the house decide how to reach it.

A virtual thermostat passes its setpoint to the devices that keep the temperature (radiator
valves, breezers, air conditioners) and switches on **boosters** — a fan heater, an electric
stove, an AC — only while the room lags far behind, then hands control back.

[Русская версия](README.ru.md)

## Concepts

| Object | What it is |
|---|---|
| **Season** | One selector for the whole installation: *heating*, *cooling* or *mid-season* (a range, both directions). Thermostats only offer the season's mode, so nothing can cool in winter or heat in summer. |
| **Thermostat** | A `climate` entity with a temperature sensor and presets (*home*, *away*, *sleep*, *eco*, *comfort*), each with a winter and a summer temperature. Comes with a *Boosters allowed* switch. |
| **Device** | A real device (`climate`, `switch`, `fan`, `input_boolean`, `light`) attached to one or more thermostats from the thermostat's **Configure** menu. Each gets a *status* sensor on the thermostat that tells what it does and why. |

For each season an actuator has two settings:

- **While idle** — *follow the setpoint*, *off*, or *leave alone*.
- **While boosting** — *no boost*, *just turn on* (stove), *turn on with the setpoint* (fan heater, AC),
  or *push to the device limit* (its max temperature when heating, min when cooling — e.g. a breezer's
  air heater), optionally with a fan speed or preset for the boost.

A boost starts when the gap between setpoint and room temperature reaches the actuator's
**start** threshold and stops at its **stop** threshold. Optional minimum on/off times prevent
flapping. It is re-checked on every sensor change, so a boost also starts when the room drifts
(an open window), not only when the setpoint changes.

## Shared actuators

A device that serves several thermostats (say, an AC in a hallway blowing into two rooms) follows
the **average** gap and setpoint of the thermostats that are on. The **overshoot veto** stops a
shared boost as soon as *any* of those rooms overshoots by the start threshold, even if the average
still looks cold/hot. If the rooms want opposite directions (mid-season), the boost is released and
the status shows *conflict*.

## Night

While a linked thermostat is in the **sleep** preset, an actuator can switch to its own night
preset or fan speed (and optionally never boost). When the night ends, the previous value is
restored — unless someone changed it manually in the meantime.

Climate Control never switches presets itself: use your own automations for that
(presence, alarm panel, schedule).

## Behaviour worth knowing

- Commands are sent only when the decision changes. If you turn a valve by hand, it is left
  alone until the thermostat changes again.
- A thermostat that is *off* or has lost its sensor stops voting: boosters turn off, followers are
  left as they are.
- Values are clamped to the device's range and rounded to its step.
- Devices whose HVAC mode means something else (a breezer's ventilation mode) can be set to receive
  only the temperature.

## Installation

1. HACS → ⋮ → *Custom repositories* → `https://github.com/he110/ha-climate-control`, category *Integration*.
2. Install **Climate Control**, restart Home Assistant.
3. *Settings → Devices & services → Add integration → Climate Control*.
4. On the integration page: **Add thermostat**, then **Configure → Add device** on it for each device.

Requires Home Assistant 2026.9 or newer.
