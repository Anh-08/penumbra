"""Vectorized planning model: what the ground believes the EPS will do under a plan.

It runs every combination at once, in arrays of shape (sat, hyp, plan, mc), and keeps only running
minima/maxima (no time history), so the verifier can afford hundreds of Monte Carlo samples per plan.

Differences from the truth model (truth.py), on purpose:
- Parameters come from params.PLANNING: datasheet capacity 2.0 Ah/cell (truth 1.91, PCoE), a coarse
  7-point OCV table (truth: 21-point PCoE fit), R0 0.14 ohm/cell with a 2500 K Arrhenius factor
  (truth: 0.1286 ohm at 25 degC, 3000 K), coulombic efficiency 0.99 (truth 0.982), cell efficiency
  0.29 (truth 0.295), regulator 0.94 (truth 0.95), thermal conductance 0.5 W/K (truth 0.4). See
  params.PLANNING_DIFFERENCES. Capacity and efficiency are optimistic, array output is pessimistic.
- No unit-to-unit spread, no sensor model and no undervoltage load shedding: a plan that only
  survives thanks to shedding already breaks the voltage constraint.
- Faults enter only as hypotheses (H0..H5) with assumed parameters, never from the fault specs.
The Monte Carlo perturbations (Perturbation) are where the verifier covers this mismatch.

Battery current sign: positive = discharge, as in physics.py.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np

from contracts import ESSENTIAL_LOADS, FAULT_KINDS, HYPOTHESES, LOADS, Command

from . import physics
from .params import PLANNING, EpsParams

log = logging.getLogger("penumbra.eps.planning")

L_IDX = {name: i for i, name in enumerate(LOADS)}
ESSENTIAL = np.array([name in ESSENTIAL_LOADS for name in LOADS])
I_PAYLOAD, I_HEATER, I_AUX = L_IDX["PAYLOAD"], L_IDX["HEATER"], L_IDX["AUX"]
KIND_OF = {f"H{i + 1}": k for i, k in enumerate(FAULT_KINDS)}


def planning_params(**overrides) -> EpsParams:
    return EpsParams(PLANNING, **overrides)


# ----------------------------------------------------------------------------- hypotheses
@dataclass
class Hypotheses:
    """Physical meaning of each hypothesis per satellite. Arrays (S, H[, ...]); defaults = nominal."""
    names: tuple[str, ...]
    array_factor: np.ndarray      # (S, H, P) panel output factor
    capacity_factor: np.ndarray   # (S, H)
    r0_factor: np.ndarray         # (S, H)
    stuck_on: np.ndarray          # (S, H, L) bool
    extra_beta_deg: np.ndarray    # (S, H)
    soc_offset: np.ndarray        # (S, H) true SOC = estimated SOC + offset (biased SOC sensor)

    @classmethod
    def build(cls, per_sat: list[dict[str, dict]], names=HYPOTHESES, params: EpsParams | None = None) -> "Hypotheses":
        """per_sat[s][H] = fault params in the FaultSpec.params format of that kind (see faults.py).

        Missing entries use representative values: the middle of params.fault_ranges.
        """
        p = params or planning_params()
        panels = tuple(p.panels)
        s, h = len(per_sat), len(names)
        hy = cls(
            names=tuple(names),
            array_factor=np.ones((s, h, len(panels))),
            capacity_factor=np.ones((s, h)),
            r0_factor=np.ones((s, h)),
            stuck_on=np.zeros((s, h, len(LOADS)), bool),
            extra_beta_deg=np.zeros((s, h)),
            soc_offset=np.zeros((s, h)),
        )
        for si, given in enumerate(per_sat):
            for hi, name in enumerate(names):
                if name == "H0":
                    continue
                kind = KIND_OF[name]
                fp = given.get(name) or default_params(kind, p)
                hy._apply(si, hi, kind, fp, p, panels)
        return hy

    def _apply(self, si, hi, kind, fp, p, panels):
        if kind == "array_degradation":
            cols = range(len(panels)) if fp["panel"] == "ALL" else [panels.index(fp["panel"])]
            for c in cols:
                self.array_factor[si, hi, c] *= 1.0 - float(fp["loss"])
        elif kind == "stuck_on_load":
            self.stuck_on[si, hi, L_IDX[fp["load"]]] = True
        elif kind == "capacity_fade":
            rem = float(fp["remaining"])
            self.capacity_factor[si, hi] *= rem
            self.r0_factor[si, hi] *= 1.0 + p.r0_growth_per_fade * (1.0 - rem)
        elif kind == "sensor_bias":
            # only a biased SOC reading changes what the battery really holds; other channels are cosmetic
            if fp["channel"] == "soc":
                self.soc_offset[si, hi] -= float(fp["bias"])
        elif kind == "beta_season":
            self.extra_beta_deg[si, hi] += float(fp["extra_beta_deg"])


def default_params(kind: str, params: EpsParams | None = None) -> dict:
    """Representative parameters for a hypothesis: middle of the range, the usual suspect for discrete ones."""
    rg = (params or planning_params()).fault_ranges[kind]
    mid = lambda lohi: 0.5 * (lohi[0] + lohi[1])  # noqa: E731
    if kind == "array_degradation":
        return {"loss": mid(rg["loss"]), "panel": "ALL"}
    if kind == "stuck_on_load":
        return {"load": "AUX"}
    if kind == "capacity_fade":
        return {"remaining": mid(rg["remaining"])}
    if kind == "sensor_bias":
        return {"channel": "soc", "bias": mid(rg["bias"]["soc"])}
    return {"extra_beta_deg": mid(rg["extra_beta_deg"])}


# ----------------------------------------------------------------------------- inputs
@dataclass
class InitialState:
    """What the ground believes at the start of the horizon, per satellite (S,)."""
    soc: np.ndarray
    t_bat_c: np.ndarray
    switch: np.ndarray          # (S, L) commanded switch state
    payload_duty: np.ndarray
    heater_setpoint_c: np.ndarray

    @classmethod
    def nominal(cls, n_sats: int, soc=0.9, t_bat_c=15.0, params: EpsParams | None = None) -> "InitialState":
        p = params or planning_params()
        sw = np.array([name in ESSENTIAL_LOADS or bool(p.initial_on.get(name, False)) for name in LOADS])
        return cls(
            soc=np.broadcast_to(np.asarray(soc, float), (n_sats,)).copy(),
            t_bat_c=np.broadcast_to(np.asarray(t_bat_c, float), (n_sats,)).copy(),
            switch=np.tile(sw, (n_sats, 1)),
            payload_duty=np.full(n_sats, float(p.initial_payload_duty)),
            heater_setpoint_c=np.full(n_sats, float(p.initial_heater_setpoint_c)),
        )


@dataclass
class Perturbation:
    """Monte Carlo multipliers/offsets, broadcastable to (S, H, P, M). Sigma values belong to the verifier."""
    array_factor: np.ndarray | float = 1.0
    capacity_factor: np.ndarray | float = 1.0
    load_factor: np.ndarray | float = 1.0
    soc0_offset: np.ndarray | float = 0.0
    n_mc: int = 1


@dataclass
class Controls:
    """Commanded state per (sat, plan) over the grid, built from command lists."""
    switch: np.ndarray       # (S, P, L, N) bool
    duty: np.ndarray         # (S, P, N)
    setpoint: np.ndarray     # (S, P, N)
    n_commands: np.ndarray   # (S, P)


def build_controls(t: np.ndarray, plans: list[list[list[Command]]], init: InitialState,
                   params: EpsParams | None = None) -> Controls:
    """plans[s][p] = command list of plan p for satellite s. Satellites with fewer plans get no-op plans."""
    p = params or planning_params()
    s_n, n = len(plans), len(t)
    n_plans = max(1, max((len(x) for x in plans), default=1))
    sw = np.empty((s_n, n_plans, len(LOADS), n), bool)
    duty = np.empty((s_n, n_plans, n))
    sp = np.empty((s_n, n_plans, n))
    n_cmd = np.zeros((s_n, n_plans), int)
    for si in range(s_n):
        for pi in range(n_plans):
            cmds = sorted(plans[si][pi], key=lambda c: c.t_exec) if pi < len(plans[si]) else []
            n_cmd[si, pi] = len(cmds)
            cur_sw = init.switch[si].copy()
            cur_duty, cur_sp = float(init.payload_duty[si]), float(init.heater_setpoint_c[si])
            k0 = 0
            for c in cmds + [None]:
                k1 = n if c is None else int(np.searchsorted(t, c.t_exec, side="left"))
                sw[si, pi, :, k0:k1] = cur_sw[:, None]
                duty[si, pi, k0:k1] = cur_duty
                sp[si, pi, k0:k1] = cur_sp
                k0 = max(k0, k1)
                if c is None:
                    break
                if c.name == "LOAD_ON":
                    cur_sw[L_IDX[c.args["load"]]] = True
                elif c.name == "LOAD_OFF" and not ESSENTIAL[L_IDX[c.args["load"]]]:
                    cur_sw[L_IDX[c.args["load"]]] = False
                elif c.name == "SET_PAYLOAD_DUTY":
                    cur_duty = float(c.args["duty"])
                elif c.name == "SET_HEATER_SETPOINT":
                    cur_sp = float(c.args["setpoint_c"])
                elif c.name == "SAFE_MODE":
                    cur_sw[I_PAYLOAD] = False
                    cur_sw[I_AUX] = False
                    cur_duty, cur_sp = 0.0, float(p.safe_heater_setpoint_c)
                cur_sw[ESSENTIAL] = True
    return Controls(switch=sw, duty=duty, setpoint=sp, n_commands=n_cmd)


# ----------------------------------------------------------------------------- result
@dataclass
class BatchResult:
    """Running statistics, each (S, H, P, M)."""
    min_soc: np.ndarray
    min_v_bus: np.ndarray
    t_bat_min_c: np.ndarray
    t_bat_max_c: np.ndarray
    payload_uptime: np.ndarray     # mean payload duty actually flown over the horizon
    soc_end: np.ndarray
    t_soc_below: np.ndarray        # first time SOC < soc_threshold (POSIX s), NaN if never
    hyp_names: tuple[str, ...]
    n_commands: np.ndarray         # (S, P)
    evals_per_s: float             # (S*H*P*M*N) step evaluations per second
    soc_history: np.ndarray | None = field(default=None, repr=False)   # (S, H, P, M, N) only if requested
    # predicted telemetry channels (truth-free readings), each (S, H, P, M, N), only if record_channels;
    # i_bat here is + = charging, as in telemetry
    channels: dict[str, np.ndarray] | None = field(default=None, repr=False)


# ----------------------------------------------------------------------------- the model
def simulate_batch(
    t: np.ndarray,
    illum: np.ndarray,              # (S, N)
    beta_deg: np.ndarray,           # (S,) or (S, N)
    plans: list[list[list[Command]]] | Controls,
    init: InitialState,
    hyps: Hypotheses | None = None,
    perturb: Perturbation | None = None,
    params: EpsParams | None = None,
    soc_threshold: float = 0.0,
    dtype=np.float64,
    record_soc: bool = False,
    record_channels: bool = False,
) -> BatchResult:
    p = params or planning_params()
    t = np.asarray(t, float)
    s_n, n = np.shape(illum)
    dt = float(t[1] - t[0]) if n > 1 else 10.0
    ctl = plans if isinstance(plans, Controls) else build_controls(t, plans, init, p)
    hyps = hyps or Hypotheses.build([{} for _ in range(s_n)], names=("H0",), params=p)
    pert = perturb or Perturbation()
    h_n, p_n, m_n = len(hyps.names), ctl.switch.shape[1], pert.n_mc
    shape = (s_n, h_n, p_n, m_n)
    f = lambda x: np.broadcast_to(np.asarray(x, dtype), shape).copy()  # noqa: E731

    # constants per (S, H, ., .) broadcast to the full batch
    cap = f(p.capacity_ah * hyps.capacity_factor[:, :, None, None] * pert.capacity_factor)
    r0 = f(p.r0_ohm * hyps.r0_factor[:, :, None, None])
    r = r0
    load_factor = f(pert.load_factor)
    w = np.asarray([p.load_power[name] for name in LOADS], dtype)
    panel_coef = (p.solar_flux * p.panel_area * p.cell_efficiency * p.packing_factor * p.regulator_eff
                  * hyps.array_factor)                                 # (S, H, Pn) at normal incidence, sunlit
    # beta_deg is accepted for the record; power depends on beta only through illum (eclipse geometry) and
    # on a beta drift from the design attitude (H5), as in truth.py
    cos_inc = np.broadcast_to(np.cos(np.radians(hyps.extra_beta_deg))[:, :, None], (s_n, h_n, n))   # (S, H, N)
    sun = np.clip(cos_inc, 0, 1) * np.clip(np.asarray(illum)[:, None, :], 0, 1)
    p_sun_pan = (panel_coef[:, :, :, None] * sun[:, :, None, :]).astype(dtype)   # (S, H, Pn, N)
    p_sun = p_sun_pan.sum(axis=2)                                      # (S, H, N)
    panels = tuple(p.panels)
    array_factor = f(pert.array_factor)
    stuck = hyps.stuck_on[:, :, None, None, :]                         # (S, H, 1, 1, L)
    other = np.array([i not in (I_PAYLOAD, I_HEATER) for i in range(len(LOADS))])
    t_sink = (p.sink_eclipse_c + (p.sink_sun_c - p.sink_eclipse_c) * np.asarray(illum)).astype(dtype)
    soc_grid = np.asarray(p.soc_grid, dtype)
    ocv_grid = np.asarray(p.ocv_cell_v, dtype)
    eta = dtype(p.coulombic_eff)
    half = 0.5 * p.heater_hysteresis_c

    soc = np.clip(f(init.soc[:, None, None, None] + hyps.soc_offset[:, :, None, None] + pert.soc0_offset), 0, 1)
    temp = f(init.t_bat_c[:, None, None, None])
    heater_active = temp < f(init.heater_setpoint_c[:, None, None, None])

    min_soc, min_v = soc.copy(), np.full(shape, np.inf, dtype)
    t_min, t_max = temp.copy(), temp.copy()
    uptime = np.zeros(shape, dtype)
    t_below = np.full(shape, np.nan)
    hist = np.empty(shape + (n,), dtype) if record_soc else None
    chans = None
    if record_channels:
        names = ("soc", "v_bus", "i_bat", "p_load", "t_bat_c") + tuple(f"i_array_{pn}" for pn in panels)
        chans = {name: np.empty(shape + (n,), dtype) for name in names}

    clock = time.perf_counter()
    for k in range(n):
        sw = ctl.switch[:, None, :, None, :, k]                         # (S, 1, P, 1, L)
        on = sw | stuck                                                 # (S, H, P, 1, L)
        duty = ctl.duty[:, None, :, None, k].astype(dtype)
        setpoint = ctl.setpoint[:, None, :, None, k].astype(dtype)
        heater_active = np.where(temp < setpoint - half, True, np.where(temp > setpoint + half, False, heater_active))
        heater_active = heater_active | on[..., I_HEATER] & stuck[..., I_HEATER]
        heater_on = on[..., I_HEATER] & heater_active
        payload = on[..., I_PAYLOAD] * duty
        base = (on[..., other] * w[other]).sum(axis=-1)
        p_demand = load_factor * (base + payload * w[I_PAYLOAD] + heater_on * w[I_HEATER])
        p_avail = p_sun[:, :, None, None, k] * array_factor

        voc = (p.n_series * physics.ocv_cell(soc, soc_grid, ocv_grid)).astype(dtype, copy=False)
        if p.r_temp_b:
            r = (r0 * physics.resistance(1.0, temp, p.r_temp_b)).astype(dtype, copy=False)
        p_b = p_demand - p_avail
        discharging = p_b > 0
        i_dis, _ = physics.current_for_power(np.maximum(p_b, 0), voc, r)
        i_dis = np.minimum(i_dis, np.minimum(p.discharge_current_max_a, soc * cap * 3600.0 / dt))
        i_chg_w, _ = physics.current_for_power(-np.maximum(-p_b, 0), voc, r)
        i_chg = np.minimum(-i_chg_w, np.minimum(
            physics.charge_limit(soc, temp, p.charge_current_max_a, p.taper_soc, p.charge_min_temp_c),
            (1.0 - soc) * cap * 3600.0 / (eta * dt)))
        current = np.where(discharging, i_dis, -i_chg)
        v_t = voc - current * r

        np.minimum(min_soc, soc, out=min_soc)
        np.minimum(min_v, v_t, out=min_v)
        np.minimum(t_min, temp, out=t_min)
        np.maximum(t_max, temp, out=t_max)
        uptime += payload
        if soc_threshold > 0:
            newly = np.isnan(t_below) & (soc < soc_threshold)
            t_below[newly] = t[k]
        if hist is not None:
            hist[..., k] = soc
        if chans is not None:
            p_term = v_t * current
            p_used = np.where(discharging, p_avail, p_demand - p_term)
            used = np.divide(p_used, p_avail, out=np.zeros_like(p_used), where=p_avail > 0)
            chans["soc"][..., k] = soc
            chans["v_bus"][..., k] = v_t
            chans["i_bat"][..., k] = -current
            chans["p_load"][..., k] = p_demand
            chans["t_bat_c"][..., k] = temp
            for j, pn in enumerate(panels):
                chans[f"i_array_{pn}"][..., k] = p_sun_pan[:, :, j, None, None, k] * array_factor * used / v_t

        soc = np.clip(soc + np.where(current > 0, -current, -current * eta) * dt / 3600.0 / cap, 0.0, 1.0)
        q = current * current * r + heater_on * w[I_HEATER] * load_factor
        temp = temp + (q - p.thermal_conductance * (temp - t_sink[:, None, None, None, k])) * dt / p.thermal_capacity

    np.minimum(min_soc, soc, out=min_soc)
    elapsed = max(time.perf_counter() - clock, 1e-9)
    evals = float(np.prod(shape)) * n / elapsed
    log.info("planning batch %s x %d steps: %.3g evals/s (%.2f s)", shape, n, evals, elapsed)
    return BatchResult(
        min_soc=min_soc, min_v_bus=min_v, t_bat_min_c=t_min, t_bat_max_c=t_max,
        payload_uptime=uptime / n, soc_end=soc, t_soc_below=t_below, hyp_names=hyps.names,
        n_commands=ctl.n_commands, evals_per_s=evals, soc_history=hist, channels=chans,
    )
