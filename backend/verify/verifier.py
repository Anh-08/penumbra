"""Verifier (T12): simulate a plan under every hypothesis with Monte Carlo, rank, declare infeasible.

AI proposes, physics verifies: this is the physics. It never reads fault specs; it starts from what
the ground believes (last downlinked state, commands already sent, hypothesis scores).

- simulate(plan, hypotheses, monte_carlo) -> SimResult (CONTRACTS.md). Hypotheses simulated are the
  requested ones plus every plausible one; passes_all, worst_margin_soc and payload_uptime_worst are
  taken over the plausible ones only. A hypothesis passes when PASS_RATE_REQUIRED of its samples meet
  every hard constraint: SOC >= SOC_MIN, V_bus >= V_MIN, T_bat in [T_MIN_C, T_MAX_C], essential loads
  on (guaranteed: the planning model never switches them off and Command rejects LOAD_OFF on them).
- Monte Carlo: per sample, array efficiency, capacity and load power are scaled and the starting SOC
  is shifted (initial-SOC error plus SOC-sensor bias), with constants.MC_SIGMA. Seeded by the plan hash,
  so the same plan gives the same SimResult.
- Horizon: kind "plan" to the end of the scenario horizon; kind "diagnostic" over its own window
  (first t_exec to last step + one dwell, plus one orbit). A diagnostic must not drain the battery;
  it is not expected to fix anything.
- rank(sat_id): passing plans first; among them non-SAFE_MODE plans before SAFE_MODE plans (SAFE_MODE is
  the fallback: CONTRACTS says the status is infeasible when it is the only passing plan); then highest
  worst_margin_soc, highest payload_uptime_worst, fewest commands. Status "infeasible" when no
  non-SAFE_MODE plan passes; the agent must then say so and recommend SAFE_MODE.
"""
from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from contracts import HYPOTHESES, Command, HypStats, Plan, RankResult, SimResult, Trajectory
from backend.eps import planning
from backend.eps.truth import Environment

from . import constants as K


@dataclass
class Belief:
    """What the ground believes about one satellite right now."""
    sat_id: str
    t_state: float                   # time of the last downlinked sample (simulation starts here)
    soc: float                       # last downlinked SOC reading
    t_bat_c: float
    commands: list[Command]          # every command already sent
    initial: planning.InitialState   # commanded configuration at the start of the grid (1 satellite)
    plausible: list[str]
    hyp_params: dict[str, dict] = field(default_factory=dict)   # MAP params per hypothesis (FaultSpec format)


def belief_from(sat_id: str, telemetry: dict, commands: list[Command], initial: planning.InitialState,
                score=None) -> Belief:
    """Belief from downlinked telemetry and a scoring.Score (None = nominal only)."""
    seen = np.nonzero(np.isfinite(np.asarray(telemetry["soc"], float)))[0]
    if seen.size == 0:
        raise ValueError(f"no telemetry downlinked yet for {sat_id}")
    j = seen[-1]
    t_bat = float(telemetry["t_bat_c"][j])
    params = {}
    if score is not None:
        params = {h: {k: v for k, v in p.items() if k != "onset_after_t"}
                  for h, p in score.map_params.items() if h != "H0"}
    return Belief(sat_id=sat_id, t_state=float(telemetry["t"][j]), soc=float(telemetry["soc"][j]),
                  t_bat_c=t_bat if np.isfinite(t_bat) else float(initial.t_bat_c[0]),
                  commands=list(commands), initial=initial,
                  plausible=list(score.plausible) if score is not None else ["H0"], hyp_params=params)


def is_safe_mode(plan: Plan) -> bool:
    return any(c.name == "SAFE_MODE" for c in plan.commands)


class Verifier:
    def __init__(self, env: Environment, belief: Callable[[str], Belief], params=None, seed: int = 0,
                 conjunctions: Callable[[str], list] | None = None) -> None:
        """conjunctions(sat_id) -> that satellite's Conjunctions (T31): the burn load of a CAM_BURN and the
        horizon of a kind "maneuver" plan (first command to tca plus one orbit) come from it."""
        self._conjunctions = conjunctions or (lambda sat_id: [])
        self.env = env
        self._belief = belief
        self.params = params or planning.planning_params()
        self.seed = int(seed)
        self._ids = itertools.count(1)
        self._plans: dict[str, Plan] = {}
        self._results: dict[str, SimResult] = {}
        self._context: dict[str, dict] = {}       # plan_id -> what the last simulate used (for trajectory)

    # ------------------------------------------------------------------ public
    def simulate(self, plan: Plan, hypotheses: str | list[str] = "all", monte_carlo: int = K.MONTE_CARLO_N) -> SimResult:
        if monte_carlo < 1:
            raise ValueError("monte_carlo must be >= 1")
        b = self._belief(plan.sat_id)
        requested = list(HYPOTHESES) if hypotheses == "all" else list(hypotheses)
        unknown = set(requested) - set(HYPOTHESES)
        if unknown:
            raise ValueError(f"unknown hypotheses {sorted(unknown)}")
        plausible = [h for h in HYPOTHESES if h in b.plausible] or ["H0"]
        names = [h for h in HYPOTHESES if h in requested or h in plausible]
        stats, _ = self._run(plan, b, names, monte_carlo, record=False)
        per = {}
        for hi, h in enumerate(names):
            ok = ((stats.min_soc[0, hi, 0] >= K.SOC_MIN) & (stats.min_v_bus[0, hi, 0] >= K.V_MIN)
                  & (stats.t_bat_min_c[0, hi, 0] >= K.T_MIN_C) & (stats.t_bat_max_c[0, hi, 0] <= K.T_MAX_C))
            per[h] = HypStats(
                pass_rate=float(ok.mean()),
                min_soc_p5=float(np.percentile(stats.min_soc[0, hi, 0], 5)),
                min_v_bus=float(np.percentile(stats.min_v_bus[0, hi, 0], 5)),
                t_bat_min_c=float(np.percentile(stats.t_bat_min_c[0, hi, 0], 5)),
                t_bat_max_c=float(np.percentile(stats.t_bat_max_c[0, hi, 0], 95)),
                payload_uptime=float(np.mean(stats.payload_uptime[0, hi, 0])),
            )
        passes_all = all(per[h].pass_rate >= K.PASS_RATE_REQUIRED for h in plausible)
        result = SimResult(
            sim_id=f"S-{next(self._ids)}", plan_id=plan.plan_id, plan_hash=plan.plan_hash, per_hyp=per,
            plausible=plausible, passes_all=passes_all,
            worst_margin_soc=min(per[h].min_soc_p5 for h in plausible) - K.SOC_MIN,
            payload_uptime_worst=min(per[h].payload_uptime for h in plausible),
            n_commands=len(plan.commands),
        )
        self._plans[plan.plan_id] = plan
        self._results[plan.plan_id] = result
        self._context[plan.plan_id] = {"belief": b, "names": names, "mc": monte_carlo}
        return result

    def latest(self, plan_id: str) -> SimResult | None:
        """Latest SimResult of a plan (the approval hash check compares against its plan_hash)."""
        return self._results.get(plan_id)

    def rank(self, sat_id: str) -> RankResult:
        """Rank every plan simulated for this satellite whose latest result matches its hash."""
        cands = [(self._plans[pid], r) for pid, r in self._results.items()
                 if self._plans[pid].sat_id == sat_id and r.plan_hash == self._plans[pid].plan_hash]

        def key(item):
            plan, r = item
            return (not r.passes_all, is_safe_mode(plan), -r.worst_margin_soc, -r.payload_uptime_worst,
                    r.n_commands, plan.plan_id)

        cands.sort(key=key)
        feasible = any(r.passes_all and not is_safe_mode(p) for p, r in cands)
        return RankResult(ordered=[p.plan_id for p, _ in cands], status="feasible" if feasible else "infeasible")

    def trajectory(self, plan_id: str, hypothesis: str) -> Trajectory:
        """SOC percentiles over time for one simulated plan and hypothesis, for drawing only."""
        if plan_id not in self._plans:
            raise KeyError(f"{plan_id} has not been simulated")
        ctx = self._context[plan_id]
        stats, t = self._run(self._plans[plan_id], ctx["belief"], [hypothesis], ctx["mc"], record=True)
        hist = stats.soc_history[0, 0, 0]                       # (M, N)
        step = max(1, int(round(K.TRAJECTORY_STEP_S / self.env.dt)))
        sel = slice(0, hist.shape[1], step)
        p5, p50, p95 = np.percentile(hist[:, sel], [5, 50, 95], axis=0)
        return Trajectory(t=t[sel].tolist(), soc_p5=p5.tolist(), soc_p50=p50.tolist(), soc_p95=p95.tolist())

    # ------------------------------------------------------------------ internals
    def horizon_end(self, plan: Plan, t_state: float) -> float:
        end = float(self.env.t[-1])
        if plan.kind == "maneuver" and plan.commands:
            cjs = sorted(self._conjunctions(plan.sat_id), key=lambda c: c.tca)
            if cjs:
                end = min(end, cjs[0].tca + K.ORBIT_S)
        if plan.kind == "diagnostic" and plan.commands:
            end = min(end, max(c.t_exec for c in plan.commands) + K.DIAGNOSTIC_DWELL_S + K.ORBIT_S)
        return max(end, t_state + self.env.dt)

    def _perturbation(self, plan: Plan, n_hyp: int, mc: int) -> planning.Perturbation:
        rng = np.random.default_rng([self.seed, int(plan.plan_hash[:15], 16)])
        sig = K.MC_SIGMA
        shape = (1, n_hyp, 1, mc)
        return planning.Perturbation(
            array_factor=1.0 + sig["array_efficiency"] * rng.standard_normal(shape),
            capacity_factor=np.clip(1.0 + sig["capacity"] * rng.standard_normal(shape), 0.5, 1.5),
            load_factor=1.0 + sig["load_power"] * rng.standard_normal(shape),
            soc0_offset=(sig["initial_soc"] * rng.standard_normal(shape)
                         + sig["soc_sensor_bias"] * rng.standard_normal(shape)),
            n_mc=mc,
        )

    def _run(self, plan: Plan, b: Belief, names: list[str], mc: int, record: bool):
        env = self.env
        i = env.sat_ids.index(plan.sat_id)
        k0 = int(np.searchsorted(env.t, b.t_state))
        k1 = int(np.searchsorted(env.t, self.horizon_end(plan, b.t_state), side="right"))
        k1 = max(k1, k0 + 2)
        cmds = list(b.commands) + list(plan.commands)
        from backend.eps.maneuver import windows_by_sat

        windows = windows_by_sat(self._conjunctions(plan.sat_id)).get(plan.sat_id)
        full = planning.build_controls(env.t, [[cmds]], b.initial, self.params, maneuvers=[windows])
        ctl = full.window(k0, k1)
        hyps = planning.Hypotheses.build([{h: b.hyp_params[h] for h in names if h in b.hyp_params}],
                                         names=tuple(names), params=self.params)
        init = planning.InitialState(soc=np.array([b.soc]), t_bat_c=np.array([b.t_bat_c]),
                                     switch=ctl.switch[:, 0, :, 0], payload_duty=ctl.duty[:, 0, 0],
                                     heater_setpoint_c=ctl.setpoint[:, 0, 0])
        stats = planning.simulate_batch(env.t[k0:k1], env.illum[i:i + 1, k0:k1], env.beta_deg[i:i + 1, k0:k1], ctl,
                                        init, hyps, self._perturbation(plan, len(names), mc), params=self.params,
                                        record_soc=record)
        return stats, env.t[k0:k1]
