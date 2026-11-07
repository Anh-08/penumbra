"""Standard diagnostic test: a short command sequence inside one contact whose response separates the
hypotheses (T16). Each step lasts DWELL_S so the downlink holds several samples per level.

Steps: AUX off, HEATER off, PAYLOAD off (a stuck load does not drop), PAYLOAD on at full duty
(a large current step: battery resistance), then restore the previous duty and heater.
"""
from __future__ import annotations

from contracts import Command

DWELL_S = 60.0
LENGTH_S = 6 * DWELL_S


def standard_diagnostic(aos: float, los: float, payload_duty: float = 0.5, heater_on: bool = True,
                        aux_on: bool = False) -> list[Command]:
    """Commands for one contact [aos, los]; needs los - aos >= LENGTH_S + DWELL_S."""
    if los - aos < LENGTH_S + DWELL_S:
        raise ValueError("contact too short for the standard diagnostic")
    t = aos + DWELL_S
    c = [
        Command(t_exec=t, name="LOAD_OFF", args={"load": "AUX"}),
        Command(t_exec=t + DWELL_S, name="LOAD_OFF", args={"load": "HEATER"}),
        Command(t_exec=t + 2 * DWELL_S, name="LOAD_OFF", args={"load": "PAYLOAD"}),
        Command(t_exec=t + 3 * DWELL_S, name="LOAD_ON", args={"load": "PAYLOAD"}),
        Command(t_exec=t + 3 * DWELL_S, name="SET_PAYLOAD_DUTY", args={"duty": 1.0}),
        Command(t_exec=t + 5 * DWELL_S, name="SET_PAYLOAD_DUTY", args={"duty": payload_duty}),
    ]
    if heater_on:
        c.append(Command(t_exec=t + 5 * DWELL_S, name="LOAD_ON", args={"load": "HEATER"}))
    if aux_on:
        c.append(Command(t_exec=t + 5 * DWELL_S, name="LOAD_ON", args={"load": "AUX"}))
    return c
