"""Seeded faults (5 kinds) and EpsWorld, the object that keeps them secret.

Fault params by kind (FaultSpec.params):
- array_degradation: {"loss": 0..1, "panel": "PY" | "MY" | "ALL", "ramp_s": >= 0 (optional, default 0)}
    panel output drops by `loss`, linearly over ramp_s after onset.
- stuck_on_load:     {"load": non-essential load}
    the load is physically on from onset and ignores LOAD_OFF, SAFE_MODE and load shedding. Its switch
    telemetry still shows the commanded state. A stuck HEATER also bypasses its thermostat.
- capacity_fade:     {"remaining": 0..1}
    capacity drops to `remaining` at onset (at constant SOC) and R0 rises with the fade (PCoE fit).
- sensor_bias:       {"channel": telemetry channel, "bias": float, "drift_per_s": float (optional)}
    only the reading is wrong; the system is fine.
- beta_season:       {"extra_beta_deg": float}, usually target "FLEET"
    fleet-wide environment: higher beta lowers array incidence (cos beta). Eclipse geometry stays as the
    orbit inputs give it.

Fault specs and seeds never leave backend/eps (CONTRACTS.md). The only exception is EpsWorld.reveal(),
which POST /api/reveal calls after execution.
"""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from contracts import ESSENTIAL_LOADS, FAULT_KINDS, LOADS, SAT_IDS, Command, FaultSpec

from . import truth
from .params import EpsParams

_SEED_SALT = 0xFA17


def _channels(panels: Iterable[str]) -> tuple[str, ...]:
    return truth.SCALAR_CHANNELS + tuple(f"i_array_{p}" for p in panels)


def validate(fault: FaultSpec, params: EpsParams | None = None) -> None:
    """Raise ValueError when the params do not fit the fault kind."""
    p = params or EpsParams()
    a = fault.params

    def need(*keys):
        missing = [k for k in keys if k not in a]
        if missing:
            raise ValueError(f"{fault.kind} needs params {missing}")

    def number(key, lo=-np.inf, hi=np.inf):
        v = a[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) or not lo <= v <= hi:
            raise ValueError(f"{fault.kind}.{key}={v!r} must be a number in [{lo}, {hi}]")

    if fault.kind == "array_degradation":
        need("loss", "panel")
        number("loss", 0.0, 1.0)
        if a["panel"] not in (*p.panels, "ALL"):
            raise ValueError(f"unknown panel {a['panel']!r}")
        if "ramp_s" in a:
            number("ramp_s", 0.0)
    elif fault.kind == "stuck_on_load":
        need("load")
        if a["load"] not in LOADS or a["load"] in ESSENTIAL_LOADS:
            raise ValueError(f"stuck_on_load needs a non-essential load, got {a['load']!r}")
    elif fault.kind == "capacity_fade":
        need("remaining")
        number("remaining", 0.05, 1.0)
    elif fault.kind == "sensor_bias":
        need("channel", "bias")
        if a["channel"] not in _channels(p.panels):
            raise ValueError(f"unknown telemetry channel {a['channel']!r}")
        number("bias")
        if "drift_per_s" in a:
            number("drift_per_s")
    elif fault.kind == "beta_season":
        need("extra_beta_deg")
        number("extra_beta_deg", -90.0, 90.0)


def build_modifiers(faults: Iterable[FaultSpec], env: truth.Environment,
                    params: EpsParams | None = None) -> truth.Modifiers:
    """Turn fault specs into the time-varying per-satellite modifiers the truth simulator applies."""
    p = params or EpsParams()
    panels = tuple(p.panels)
    s, n = env.illum.shape
    t = env.t
    array_factor = np.ones((s, len(panels), n))
    cap_f = np.ones((s, n))
    r0_f = np.ones((s, n))
    stuck = np.zeros((s, len(LOADS), n), bool)
    beta = np.zeros((s, n))
    bias: dict[str, np.ndarray] = {}

    for f in faults:
        validate(f, p)
        if f.target == "FLEET":
            rows = list(range(s))
        elif f.target in env.sat_ids:
            rows = [env.sat_ids.index(f.target)]
        else:
            continue  # targets a satellite outside this run
        a = f.params
        active = t >= f.onset_t
        if f.kind == "array_degradation":
            ramp_s = float(a.get("ramp_s", 0.0))
            ramp = np.clip((t - f.onset_t) / ramp_s, 0.0, 1.0) if ramp_s > 0 else active.astype(float)
            factor = 1.0 - float(a["loss"]) * ramp
            cols = range(len(panels)) if a["panel"] == "ALL" else [panels.index(a["panel"])]
            for r in rows:
                for c in cols:
                    array_factor[r, c] *= factor
        elif f.kind == "stuck_on_load":
            stuck[rows, LOADS.index(a["load"])] |= active
        elif f.kind == "capacity_fade":
            remaining = float(a["remaining"])
            cap_f[rows] *= np.where(active, remaining, 1.0)
            r0_f[rows] *= np.where(active, 1.0 + p.r0_growth_per_fade * (1.0 - remaining), 1.0)
        elif f.kind == "sensor_bias":
            ch = a["channel"]
            drift = float(a.get("drift_per_s", 0.0))
            value = np.where(active, float(a["bias"]) + drift * (t - f.onset_t), 0.0)
            bias.setdefault(ch, np.zeros((s, n)))[rows] += value
        elif f.kind == "beta_season":
            beta[rows] += np.where(active, float(a["extra_beta_deg"]), 0.0)

    return truth.Modifiers(array_factor=array_factor, capacity_factor=cap_f, r0_factor=r0_f,
                           stuck_on=stuck, extra_beta_deg=beta, sensor_bias=bias)


def sample_fault(kind: str, target: str, rng: np.random.Generator, onset_t: float,
                 params: EpsParams | None = None) -> FaultSpec:
    """Draw one fault of `kind` with params uniform inside params.fault_ranges[kind]."""
    if kind not in FAULT_KINDS:
        raise ValueError(f"unknown fault kind {kind!r}")
    rg = (params or EpsParams()).fault_ranges[kind]
    if kind == "array_degradation":
        fp = {"loss": float(rng.uniform(*rg["loss"])), "panel": str(rng.choice(rg["panel"]))}
    elif kind == "stuck_on_load":
        fp = {"load": str(rng.choice(rg["load"]))}
    elif kind == "capacity_fade":
        fp = {"remaining": float(rng.uniform(*rg["remaining"]))}
    elif kind == "sensor_bias":
        ch = str(rng.choice(rg["channel"]))
        sign = 1.0 if rng.random() < 0.5 else -1.0
        fp = {"channel": ch, "bias": sign * float(rng.uniform(*rg["bias"][ch]))}
    else:
        fp = {"extra_beta_deg": float(rng.uniform(*rg["extra_beta_deg"]))}
    return FaultSpec(kind=kind, target=target, params=fp, onset_t=float(onset_t))


def seeded_fault(seed: int, sat_id: str, t0: float, horizon_s: float, kind: str | None = None,
                 params: EpsParams | None = None) -> FaultSpec:
    """Deterministic random fault for one satellite (kind random too unless given)."""
    p = params or EpsParams()
    rng = np.random.default_rng([seed, SAT_IDS.index(sat_id), _SEED_SALT])
    k = kind or str(rng.choice(FAULT_KINDS))
    onset = t0 + float(rng.uniform(0.0, p.fault_onset_window)) * horizon_s
    return sample_fault(k, "FLEET" if k == "beta_season" else sat_id, rng, onset, p)


class EpsWorld:
    """The truth of one scenario: environment, faults, seed, executed commands and the sim clock.

    Public methods give what a ground segment could see (downlinked telemetry up to now) and accept
    commands. Fault specs and the seed are private; reveal() is the only way out, for POST /api/reveal.
    """

    def __init__(self, env: truth.Environment, faults: Iterable[FaultSpec] = (), seed: int = 0,
                 params: EpsParams | None = None, soc0=None, t_bat0_c=None) -> None:
        self.__params = params or EpsParams()
        self.__faults = tuple(faults)
        for f in self.__faults:
            validate(f, self.__params)
        self.__env = env
        self.__seed = int(seed)
        self.__init = (soc0, t_bat0_c)
        self.__mods = build_modifiers(self.__faults, env, self.__params)
        self.__commands: dict[str, list[Command]] = {}
        self.__t_now = float(env.t[0])
        self.__cache: truth.TruthResult | None = None

    def __repr__(self) -> str:
        return f"EpsWorld(sats={len(self.__env.sat_ids)}, t_now={self.__t_now:.0f})"

    # ----- clock -----
    @property
    def sat_ids(self) -> tuple[str, ...]:
        return self.__env.sat_ids

    @property
    def t_grid(self) -> np.ndarray:
        return self.__env.t.copy()

    @property
    def t_now(self) -> float:
        return self.__t_now

    def advance_to(self, t: float) -> None:
        if t < self.__t_now:
            raise ValueError("the sim clock only moves forward")
        self.__t_now = float(min(t, self.__env.t[-1]))

    # ----- commanding -----
    def execute(self, sat_id: str, commands: Iterable[Command]) -> None:
        """Add time-tagged commands. They cannot change the past (t_exec >= t_now)."""
        if sat_id not in self.__env.sat_ids:
            raise ValueError(f"{sat_id} is not in this world")
        cmds = list(commands)
        if any(c.t_exec < self.__t_now for c in cmds):
            raise ValueError("t_exec is before the current sim time")
        self.__commands.setdefault(sat_id, []).extend(cmds)
        self.__cache = None

    def executed(self, sat_id: str) -> list[Command]:
        return list(self.__commands.get(sat_id, ()))

    # ----- what the ground can see -----
    def telemetry(self, sat_id: str, t_from: float | None = None, t_to: float | None = None) -> dict[str, np.ndarray]:
        """Telemetry in [t_from, min(t_to, t_now)]; NaN where not downlinked."""
        r = self.__run()
        t = r.t
        lo = t[0] if t_from is None else t_from
        hi = self.__t_now if t_to is None else min(t_to, self.__t_now)
        sel = (t >= lo) & (t <= hi)
        i = r.index(sat_id)
        return {"t": t[sel].copy(), **{k: v[i, sel].copy() for k, v in r.telemetry.items()}}

    def latest(self, sat_id: str) -> dict[str, float]:
        """Most recent downlinked value of each channel up to now (NaN if none yet), and its time."""
        tm = self.telemetry(sat_id)
        seen = ~np.isnan(tm["soc"])
        if not seen.any():
            return {"t": float("nan"), **{k: float("nan") for k in tm if k != "t"}}
        j = int(np.nonzero(seen)[0][-1])
        return {k: float(v[j]) for k, v in tm.items()}

    # ----- the one exception -----
    def reveal(self, sat_id: str) -> dict:
        """Ground truth for one satellite, for POST /api/reveal only (after execution).

        `truth` is the first fault (by onset) that hits this satellite, or None for a healthy one;
        `faults` lists all of them. `truth_soc` is the true SOC up to now.
        """
        hits = sorted((f for f in self.__faults if f.target in (sat_id, "FLEET")), key=lambda f: f.onset_t)
        r = self.__run()
        sel = r.t <= self.__t_now
        return {
            "sat_id": sat_id,
            "truth": hits[0] if hits else None,
            "faults": hits,
            "truth_soc": {"t": r.t[sel].tolist(), "soc": r.soc[r.index(sat_id), sel].tolist()},
        }

    def __run(self) -> truth.TruthResult:
        if self.__cache is None:
            soc0, t0c = self.__init
            self.__cache = truth.run(self.__env, self.__commands, seed=self.__seed, params=self.__params,
                                     mods=self.__mods, soc0=soc0, t_bat0_c=t0c)
        return self.__cache
