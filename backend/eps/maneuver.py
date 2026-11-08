"""Collision-avoidance burn as a load profile (T31), shared by the truth simulator and the planning model.

A CAM_BURN command names a window of the satellite's conjunction. From t_exec, the satellite slews to burn attitude,
burns and slews back: the window's power_w is drawn for params.cam_slew_s + burn_s seconds, and the payload is off
over that time. The window map comes from orbit conjunctions (simulated debris); a CAM_BURN whose window id is not in
the map is ignored here (safety rejects it before it gets that far).
"""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from contracts import Command, Conjunction, ManeuverWindow


def windows_by_sat(conjunctions: Iterable[Conjunction]) -> dict[str, dict[str, ManeuverWindow]]:
    """{sat_id: {window_id: window}} for each satellite's next conjunction (one active conjunction per satellite)."""
    out: dict[str, dict[str, ManeuverWindow]] = {}
    for cj in sorted(conjunctions, key=lambda c: c.tca):
        out.setdefault(cj.sat_id, {w.window_id: w for w in cj.windows})
    return out


def burn_profile(t: np.ndarray, commands: Iterable[Command], windows: dict[str, ManeuverWindow] | None,
                 slew_s: float) -> tuple[np.ndarray, np.ndarray]:
    """(extra load W, payload blocked) on grid t for the CAM_BURN commands in `commands`."""
    t = np.asarray(t, float)
    extra = np.zeros(len(t))
    block = np.zeros(len(t), bool)
    for c in commands:
        if c.name != "CAM_BURN" or not windows:
            continue
        w = windows.get(c.args["window_id"])
        if w is None:
            continue
        on = (t >= c.t_exec) & (t < c.t_exec + slew_s + w.burn_s)
        extra[on] += w.power_w
        block |= on
    return extra, block
