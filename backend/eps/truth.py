"""Truth EPS simulator: the "real" satellites that the agent can only observe through telemetry.

Inputs come from orbit (illumination, contact windows, beta angle) as arrays on the DT grid, so this
module does not depend on backend/orbit. One run simulates many satellites at once, vectorized over sats.

Conventions:
- Battery current inside this module: positive = discharge. Telemetry `i_bat`: positive = charging
  (current into the battery), as housekeeping telemetry usually reports it.
- `load_<LOAD>` telemetry is the commanded switch state (1 = on). A load that is physically on while
  commanded off (stuck relay) shows up in `p_load`, not in the switch bit.
- Telemetry is NaN outside contact windows (CONTRACTS.md).
- Determinism: everything random comes from numpy Generators seeded with (seed, global sat index), so a
  satellite gives the same telemetry whether it runs alone or with the fleet, and for any horizon prefix.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from contracts import DT, ESSENTIAL_LOADS, LOADS, SAT_IDS, Command, Contact

from . import physics
from .params import EpsParams

L_IDX = {name: i for i, name in enumerate(LOADS)}
ESSENTIAL = np.array([name in ESSENTIAL_LOADS for name in LOADS])
I_PAYLOAD, I_HEATER, I_AUX = L_IDX["PAYLOAD"], L_IDX["HEATER"], L_IDX["AUX"]
SCALAR_CHANNELS = ("soc", "v_bus", "i_bat", "p_load", "t_bat_c")


@dataclass
class Environment:
    """Orbit-derived inputs on the sim grid."""
    t: np.ndarray            # (N,) POSIX seconds, uniform step
    sat_ids: tuple[str, ...]  # (S,)
    illum: np.ndarray        # (S, N) in [0, 1]
    in_contact: np.ndarray   # (S, N) bool
    beta_deg: np.ndarray     # (S,) or (S, N)

    def __post_init__(self) -> None:
        self.t = np.asarray(self.t, float)
        self.sat_ids = tuple(self.sat_ids)
        s, n = len(self.sat_ids), len(self.t)
        self.illum = np.broadcast_to(np.asarray(self.illum, float), (s, n))
        self.in_contact = np.broadcast_to(np.asarray(self.in_contact, bool), (s, n))
        beta = np.asarray(self.beta_deg, float)
        self.beta_deg = np.broadcast_to(beta[:, None] if beta.ndim == 1 else beta, (s, n))
        unknown = set(self.sat_ids) - set(SAT_IDS)
        if unknown:
            raise ValueError(f"unknown sat ids {sorted(unknown)}")

    @property
    def dt(self) -> float:
        return float(self.t[1] - self.t[0]) if len(self.t) > 1 else DT


def contact_mask(t: np.ndarray, contacts: list[Contact], sat_ids: tuple[str, ...]) -> np.ndarray:
    """(S, N) bool: True where t lies inside one of that satellite's contact windows."""
    t = np.asarray(t, float)
    mask = np.zeros((len(sat_ids), len(t)), bool)
    row = {s: i for i, s in enumerate(sat_ids)}
    for c in contacts:
        if c.sat_id in row:
            mask[row[c.sat_id]] |= (t >= c.aos) & (t <= c.los)
    return mask


@dataclass
class Modifiers:
    """Per-satellite deviations from nominal hardware or environment. T08 maps FaultSpecs onto these.

    Every field is optional and broadcast to the shape given (S sats, P panels, L loads, N steps).
    """
    array_factor: np.ndarray | None = None      # (S, P) or (S, P, N), multiplies panel output
    capacity_factor: np.ndarray | None = None   # (S,) or (S, N), multiplies battery capacity
    r0_factor: np.ndarray | None = None         # (S,) or (S, N), multiplies internal resistance
    stuck_on: np.ndarray | None = None          # (S, L) or (S, L, N) bool, load stays on whatever is commanded
    extra_beta_deg: np.ndarray | None = None    # (S,) or (S, N)
    sensor_bias: dict[str, np.ndarray] = field(default_factory=dict)  # channel -> (S,) or (S, N), additive

    def resolve(self, s: int, p: int, n_loads: int, n: int) -> dict[str, np.ndarray]:
        def full(x, default, shape):
            if x is None:
                return np.full(shape, default)
            x = np.asarray(x)
            if x.ndim == len(shape) - 1:
                x = x[..., None]
            return np.broadcast_to(x, shape)

        return {
            "array_factor": full(self.array_factor, 1.0, (s, p, n)).astype(float),
            "capacity_factor": full(self.capacity_factor, 1.0, (s, n)).astype(float),
            "r0_factor": full(self.r0_factor, 1.0, (s, n)).astype(float),
            "stuck_on": full(self.stuck_on, False, (s, n_loads, n)).astype(bool),
            "extra_beta_deg": full(self.extra_beta_deg, 0.0, (s, n)).astype(float),
            "sensor_bias": {k: full(v, 0.0, (s, n)).astype(float) for k, v in self.sensor_bias.items()},
        }


@dataclass
class TruthResult:
    t: np.ndarray
    sat_ids: tuple[str, ...]
    panels: tuple[str, ...]
    # true state, (S, N) unless noted; recorded at the start of each step
    soc: np.ndarray
    v_bus: np.ndarray
    i_bat: np.ndarray            # + = discharge (internal convention)
    p_load: np.ndarray           # power actually served to loads
    p_array_avail: np.ndarray    # array power available at the bus
    p_array_used: np.ndarray
    i_array: np.ndarray          # (S, P, N) current delivered to the bus per panel
    t_bat_c: np.ndarray
    load_cmd: np.ndarray         # (S, L, N) commanded switch state
    load_on: np.ndarray          # (S, L, N) physically drawing power
    payload_duty: np.ndarray
    heater_setpoint_c: np.ndarray
    shed: np.ndarray             # undervoltage shed latched
    brownout: np.ndarray         # load demand could not be served
    soc_final: np.ndarray        # (S,) after the last step
    energy_wh: dict[str, np.ndarray]   # (S,) totals over the run
    telemetry: dict[str, np.ndarray]   # channel -> (S, N), NaN outside contacts

    def index(self, sat_id: str) -> int:
        return self.sat_ids.index(sat_id)

    def sat_telemetry(self, sat_id: str) -> dict[str, np.ndarray]:
        i = self.index(sat_id)
        return {"t": self.t, **{k: v[i] for k, v in self.telemetry.items()}}

    @property
    def payload_uptime(self) -> np.ndarray:
        """(S,) mean payload duty actually flown (0 when the payload is off)."""
        return np.mean(self.load_on[:, I_PAYLOAD] * self.payload_duty, axis=1)


def _schedule(t: np.ndarray, sat_ids: tuple[str, ...], commands: dict[str, list[Command]] | None):
    """Map step index -> [(sat row, Command)], applied at the first step with t >= t_exec."""
    plan: dict[int, list[tuple[int, Command]]] = defaultdict(list)
    for sat_id, cmds in (commands or {}).items():
        if sat_id not in sat_ids:
            raise ValueError(f"commands for {sat_id}, which is not in this run")
        row = sat_ids.index(sat_id)
        for c in sorted(cmds, key=lambda c: c.t_exec):
            k = int(np.searchsorted(t, c.t_exec, side="left"))
            if k < len(t):
                plan[k].append((row, c))
    return plan


def run(
    env: Environment,
    commands: dict[str, list[Command]] | None = None,
    seed: int = 0,
    params: EpsParams | None = None,
    mods: Modifiers | None = None,
    soc0: np.ndarray | float | None = None,
    t_bat0_c: np.ndarray | float | None = None,
) -> TruthResult:
    p = params or EpsParams()
    panels = tuple(p.panels)
    s_n, n = env.illum.shape
    n_p, n_l = len(panels), len(LOADS)
    m = (mods or Modifiers()).resolve(s_n, n_p, n_l, n)
    dt = env.dt

    # --- seeded per-satellite draws, fixed order: spreads, calibration biases, then noise rows ---
    sat_index = [SAT_IDS.index(s) for s in env.sat_ids]
    rngs = [np.random.default_rng([seed, gi]) for gi in sat_index]
    channels = SCALAR_CHANNELS + tuple(f"i_array_{pn}" for pn in panels)
    cap_sp = np.empty(s_n)
    arr_sp = np.empty((s_n, n_p))
    load_sp = np.empty((s_n, n_l))
    cal = np.empty((s_n, len(channels)))
    noise = np.empty((s_n, n, len(channels)))
    sens = p.sensors
    sig_cal = np.array([sens["i_array" if c.startswith("i_array_") else c][2] for c in channels])
    for row, rng in enumerate(rngs):
        cap_sp[row] = 1.0 + p.spread_capacity * rng.standard_normal()
        arr_sp[row] = 1.0 + p.spread_array * rng.standard_normal(n_p)
        load_sp[row] = 1.0 + p.spread_load * rng.standard_normal(n_l)
        cal[row] = sig_cal * rng.standard_normal(len(channels))
        noise[row] = rng.standard_normal((n, len(channels)))

    # --- per-satellite constants ---
    cap_base = p.capacity_ah * cap_sp
    cap_f, r0_f = m["capacity_factor"], m["r0_factor"]
    cap = cap_base * cap_f[:, 0]
    base_w = np.array([p.load_power[name] for name in LOADS])
    load_w = base_w * load_sp
    panel_coef = p.solar_flux * p.panel_area * p.cell_efficiency * p.packing_factor * arr_sp  # (S, P)
    cos_inc = np.cos(np.radians(env.beta_deg + m["extra_beta_deg"]))
    soc_grid = np.asarray(p.soc_grid, float)
    ocv_grid = np.asarray(p.ocv_cell_v, float)
    t_sink = p.sink_eclipse_c + (p.sink_sun_c - p.sink_eclipse_c) * env.illum
    half_band = 0.5 * p.heater_hysteresis_c
    eta = p.coulombic_eff
    non_essential = ~ESSENTIAL

    # --- state ---
    soc = np.broadcast_to(np.asarray(p.initial_soc if soc0 is None else soc0, float), (s_n,)).copy()
    temp = np.broadcast_to(np.asarray(p.initial_t_bat_c if t_bat0_c is None else t_bat0_c, float), (s_n,)).copy()
    sw = np.tile(np.array([True if name in ESSENTIAL_LOADS else bool(p.initial_on.get(name, False))
                           for name in LOADS]), (s_n, 1))
    duty = np.full(s_n, float(p.initial_payload_duty))
    setpoint = np.full(s_n, float(p.initial_heater_setpoint_c))
    heater_active = temp < setpoint
    shed = np.zeros(s_n, bool)
    soc_start = soc.copy()
    cap_start = cap.copy()

    rec = {k: np.empty((s_n, n)) for k in ("soc", "v_bus", "i_bat", "p_load", "p_avail", "p_used", "t_bat_c",
                                           "duty", "setpoint")}
    rec_shed = np.empty((s_n, n), bool)
    rec_brown = np.empty((s_n, n), bool)
    rec_cmd = np.empty((s_n, n_l, n), bool)
    rec_on = np.empty((s_n, n_l, n), bool)
    rec_iarr = np.empty((s_n, n_p, n))
    e = {k: np.zeros(s_n) for k in ("array_used", "array_avail", "load", "ohmic", "coulombic", "shunt", "chemical",
                                    "capacity_loss")}

    sched = _schedule(env.t, env.sat_ids, commands)

    for k in range(n):
        for row, c in sched.get(k, ()):
            if c.name in ("LOAD_OFF", "LOAD_ON"):
                li = L_IDX[c.args["load"]]
                if c.name == "LOAD_ON":
                    sw[row, li] = True
                    shed[row] = False
                elif not ESSENTIAL[li]:
                    sw[row, li] = False
            elif c.name == "SET_PAYLOAD_DUTY":
                duty[row] = float(c.args["duty"])
            elif c.name == "SET_HEATER_SETPOINT":
                setpoint[row] = float(c.args["setpoint_c"])
            elif c.name == "SAFE_MODE":
                sw[row, I_PAYLOAD] = False
                sw[row, I_AUX] = False
                duty[row] = 0.0
                setpoint[row] = float(p.safe_heater_setpoint_c)
        sw[:, ESSENTIAL] = True
        stuck = m["stuck_on"][:, :, k]
        on = sw | stuck
        if k > 0 and np.any(cap_f[:, k] != cap_f[:, k - 1]):
            # capacity lost at constant SOC (e.g. a cell string drops out): its stored energy goes with it
            new_cap = cap_base * cap_f[:, k]
            e["capacity_loss"] += 3600.0 * (
                physics.stored_energy_wh(soc, cap, p.n_series, soc_grid, ocv_grid)
                - physics.stored_energy_wh(soc, new_cap, p.n_series, soc_grid, ocv_grid))
            cap = new_cap
        r25 = p.r0_ohm * r0_f[:, k]

        heater_active = np.where(temp < setpoint - half_band, True,
                                 np.where(temp > setpoint + half_band, False, heater_active))
        heater_active = heater_active | stuck[:, I_HEATER]   # a stuck heater relay also bypasses the thermostat
        frac = on.astype(float)
        frac[:, I_PAYLOAD] *= duty
        frac[:, I_HEATER] *= heater_active
        p_demand = (load_w * frac).sum(axis=1)

        p_pan = (panel_coef * (cos_inc[:, k] * env.illum[:, k])[:, None]
                 * m["array_factor"][:, :, k] * p.regulator_eff)            # (S, P) at the bus
        p_avail = p_pan.sum(axis=1)

        voc = p.n_series * physics.ocv_cell(soc, soc_grid, ocv_grid)
        r = physics.resistance(r25, temp, p.r_temp_b)
        p_b = p_demand - p_avail                                           # + = battery must discharge
        discharging = p_b > 0

        i_dis, served = physics.current_for_power(np.maximum(p_b, 0.0), voc, r)
        i_dis_max = np.minimum(p.discharge_current_max_a, soc * cap * 3600.0 / dt)
        i_chg_wanted, _ = physics.current_for_power(-np.maximum(-p_b, 0.0), voc, r)
        i_chg_max = np.minimum(
            physics.charge_limit(soc, temp, p.charge_current_max_a, p.taper_soc, p.charge_min_temp_c),
            (1.0 - soc) * cap * 3600.0 / (eta * dt))
        i_chg = np.minimum(-i_chg_wanted, i_chg_max)
        current = np.where(discharging, np.minimum(i_dis, i_dis_max), -i_chg)
        brown = discharging & (~served | (i_dis > i_dis_max))

        v_t = voc - current * r
        p_term = v_t * current                                             # + = delivered by battery
        p_used = np.where(discharging, p_avail, p_demand - p_term)
        p_served = np.where(discharging, p_avail + p_term, p_demand)
        used_frac = np.divide(p_used, p_avail, out=np.zeros(s_n), where=p_avail > 0)

        rec["soc"][:, k] = soc
        rec["v_bus"][:, k] = v_t
        rec["i_bat"][:, k] = current
        rec["p_load"][:, k] = p_served
        rec["p_avail"][:, k] = p_avail
        rec["p_used"][:, k] = p_used
        rec["t_bat_c"][:, k] = temp
        rec["duty"][:, k] = duty
        rec["setpoint"][:, k] = setpoint
        rec_shed[:, k] = shed
        rec_brown[:, k] = brown
        rec_cmd[:, :, k] = sw
        rec_on[:, :, k] = on
        rec_iarr[:, :, k] = p_pan * used_frac[:, None] / v_t[:, None]

        e["array_used"] += p_used * dt
        e["array_avail"] += p_avail * dt
        e["shunt"] += (p_avail - p_used) * dt
        e["load"] += p_served * dt
        e["ohmic"] += current * current * r * dt
        e["coulombic"] += np.where(current < 0, -current * voc * (1.0 - eta), 0.0) * dt
        e["chemical"] += np.where(current > 0, -current * voc, -current * voc * eta) * dt

        dsoc = np.where(current > 0, -current, -current * eta) * dt / 3600.0 / cap
        soc = np.clip(soc + dsoc, 0.0, 1.0)

        q = current * current * r + np.where(on[:, I_HEATER] & heater_active, load_w[:, I_HEATER], 0.0)
        temp = temp + (q - p.thermal_conductance * (temp - t_sink[:, k])) * dt / p.thermal_capacity

        newly = (v_t < p.v_shed) & ~shed
        if newly.any():
            sw[np.ix_(newly, non_essential)] = False
            shed |= newly

    energy = {k: v / 3600.0 for k, v in e.items()}
    energy["stored_start"] = physics.stored_energy_wh(soc_start, cap_start, p.n_series, soc_grid, ocv_grid)
    energy["stored_end"] = physics.stored_energy_wh(soc, cap, p.n_series, soc_grid, ocv_grid)

    truth_vals = {
        "soc": rec["soc"], "v_bus": rec["v_bus"], "i_bat": -rec["i_bat"], "p_load": rec["p_load"],
        "t_bat_c": rec["t_bat_c"], **{f"i_array_{pn}": rec_iarr[:, j] for j, pn in enumerate(panels)},
    }
    telemetry: dict[str, np.ndarray] = {}
    hidden = ~env.in_contact
    for j, ch in enumerate(channels):
        sigma, lsb, _ = sens["i_array" if ch.startswith("i_array_") else ch]
        x = truth_vals[ch] + cal[:, j, None] + sigma * noise[:, :, j]
        if ch in m["sensor_bias"]:
            x = x + m["sensor_bias"][ch]
        x = np.round(x / lsb) * lsb
        if ch == "soc":
            x = np.clip(x, 0.0, 1.0)
        x[hidden] = np.nan
        telemetry[ch] = x
    for li, name in enumerate(LOADS):
        x = rec_cmd[:, li].astype(float)
        x[hidden] = np.nan
        telemetry[f"load_{name}"] = x

    return TruthResult(
        t=env.t, sat_ids=env.sat_ids, panels=panels,
        soc=rec["soc"], v_bus=rec["v_bus"], i_bat=rec["i_bat"], p_load=rec["p_load"],
        p_array_avail=rec["p_avail"], p_array_used=rec["p_used"], i_array=rec_iarr, t_bat_c=rec["t_bat_c"],
        load_cmd=rec_cmd, load_on=rec_on, payload_duty=rec["duty"], heater_setpoint_c=rec["setpoint"],
        shed=rec_shed, brownout=rec_brown, soc_final=soc, energy_wh=energy, telemetry=telemetry,
    )
