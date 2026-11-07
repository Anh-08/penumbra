"""Vectorized EPS physics shared by the truth simulator and the planning model (T11).

All functions take numpy arrays of any matching shape. Battery current sign: positive = discharge.
"""
from __future__ import annotations

import numpy as np

T_REF_K = 298.15
KELVIN = 273.15


def ocv_cell(soc, soc_grid, ocv_grid):
    """Open-circuit voltage per cell, piecewise linear in SOC."""
    return np.interp(np.clip(soc, 0.0, 1.0), soc_grid, ocv_grid)


def stored_energy_wh(soc, capacity_ah, n_series, soc_grid, ocv_grid):
    """Chemical energy above SOC 0: capacity * n_series * integral_0^soc OCV(s) ds."""
    grid = np.asarray(soc_grid, float)
    ocv = np.asarray(ocv_grid, float)
    seg = 0.5 * (ocv[1:] + ocv[:-1]) * np.diff(grid)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    soc = np.clip(np.asarray(soc, float), 0.0, 1.0)
    idx = np.clip(np.searchsorted(grid, soc, side="right") - 1, 0, len(grid) - 2)
    v0 = ocv[idx]
    v1 = np.interp(soc, grid, ocv)
    partial = 0.5 * (v0 + v1) * (soc - grid[idx])
    return np.asarray(capacity_ah) * n_series * (cum[idx] + partial)


def resistance(r25, t_c, b_k):
    """Internal resistance with Arrhenius temperature dependence."""
    t_k = np.asarray(t_c) + KELVIN
    return r25 * np.exp(b_k * (1.0 / t_k - 1.0 / T_REF_K))


def array_power(illum, cos_inc, area, efficiency, packing, flux, factor=1.0):
    """Electrical power from one panel at its output (W)."""
    return flux * area * efficiency * packing * np.clip(cos_inc, 0.0, 1.0) * np.clip(illum, 0.0, 1.0) * factor


def current_for_power(p_batt, v_oc, r):
    """Battery current that delivers terminal power p_batt (W, + = discharge) given V_oc and R.

    Solves p = (V_oc - I R) I. Returns (current, served) where served is False when the request
    exceeds the maximum deliverable power V_oc^2 / 4R (current is then capped at V_oc / 2R).
    """
    disc = v_oc * v_oc - 4.0 * r * p_batt
    served = disc >= 0.0
    i = (v_oc - np.sqrt(np.maximum(disc, 0.0))) / (2.0 * r)
    return i, served


def charge_limit(soc, t_c, capacity_ah, c_rate, taper_soc, min_temp_c):
    """Max charge current (A, positive number): C-rate limit, linear taper near full, none when cold."""
    taper = np.clip((1.0 - soc) / (1.0 - taper_soc), 0.0, 1.0)
    warm = np.asarray(t_c) >= min_temp_c
    return capacity_ah * c_rate * taper * warm
