# Changelog

## 0.1.0

- Virtual thermostats (heat / cool / heat_cool) with presets per season.
- One season selector for the whole installation.
- Actuators: follow the setpoint while idle; boost by turning on, turning on with the setpoint, or pushing to the device limit.
- Boost hysteresis per actuator, minimum on/off time, overshoot veto.
- One actuator can serve several thermostats: weighted average of their gaps and setpoints.
- Night mode: preset / fan speed while a linked thermostat is in «sleep», restored afterwards.
