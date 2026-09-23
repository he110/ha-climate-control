# Changelog

## 0.3.0

- **Automation** master switch on the hub: off hands every device back to you and commands nothing
  until it is on again. Devices are left exactly as they are; the status sensors read *manual mode*.

## 0.2.1

- Removing a device from a thermostat also removes its status sensor instead of leaving it unavailable.

## 0.2.0

- Devices are configured inside their thermostat (**Configure → Add / Edit / Remove device**) instead of
  being separate entries: the integration page shows one card per thermostat.
- A device added to several thermostats is one shared actuator with one set of settings.
- The status sensor of each device lives on its thermostat's device; no extra devices are created.
- Existing actuators are migrated automatically.
- Fix: renaming a thermostat's entity_id no longer detaches it from its devices.

## 0.1.0

- Virtual thermostats (heat / cool / heat_cool) with presets per season.
- One season selector for the whole installation.
- Actuators: follow the setpoint while idle; boost by turning on, turning on with the setpoint, or pushing to the device limit.
- Boost hysteresis per actuator, minimum on/off time, overshoot veto.
- One actuator can serve several thermostats: weighted average of their gaps and setpoints.
- Night mode: preset / fan speed while a linked thermostat is in «sleep», restored afterwards.
