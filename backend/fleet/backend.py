"""FleetBackend: A1's Backend port (backend/agent/interfaces.py) on the real stack (T22).

orbit (B2: real illumination, contacts) -> truth world (eps, private faults) -> detector (fleet) ->
hypothesis scores and verifier (verify) -> uplink scheduler (B2) -> execution back into the world.

Everything the agent sees comes from downlinked telemetry and the commands the ground sent; the fault
specs stay inside the scenario's EpsWorld.
"""
from __future__ import annotations

from typing import Literal

import numpy as np

from contracts import (Alert, Contact, HypothesisScores, MatrixMsg, PeerCompare, Plan, RankResult, ScheduleRequest,
                       ScheduleResult, SimResult, TriageEntry)
from backend.eps.planning import InitialState
from backend.sched import UplinkScheduler
from backend.verify import constants as K
from backend.verify import scoring
from backend.verify.verifier import Belief, Verifier, belief_from

from . import flight_rules
from .detect import Detector
from .peers import peer_compare
from .scenarios import Scenario
from .triage import triage

# status a satellite cannot leave by agent activity alone
_FINAL = {"auto_executed", "executed", "safe_mode_recommended"}

DIAGNOSTIC_DWELL_S = K.DIAGNOSTIC_DWELL_S


class FleetBackend:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.world = scenario.world
        self.env = scenario.env
        self.detector = Detector(self.env, scenario.contacts, self.world.telemetry, self.world.executed,
                                 scenario.initial)
        self.verifier = Verifier(self.env, self._belief)
        self.scheduler = UplinkScheduler(scenario.contacts)
        self._scores: dict[str, scoring.Score] = {}       # fresh diagnosis scores (score_hypotheses)
        self._status: dict[str, str] = {}                  # triage status per satellite
        self._last_alert: dict[str, Alert] = {}            # keeps a handled satellite visible in triage
        self._tiers: dict[str, tuple[int, str | None]] = {}   # plan_id -> tier decision

    # ---- clock
    def now(self) -> float:
        return self.world.t_now

    def advance_to(self, t: float) -> list[Alert]:
        """Move the sim clock forward and run the detector; returns the active alerts."""
        self.world.advance_to(t)
        return self.detector.detect(self.world.t_now)

    # ---- fleet
    def detect(self, t: float) -> list[Alert]:
        return self.detector.detect(t)

    def triage(self) -> list[TriageEntry]:
        """Satellites with an active alert, plus the ones already handled, most urgent first (triage.py)."""
        active = {a.sat_id: a for a in self.detector.detect(self.now())}
        self._last_alert.update(active)
        shown = dict(active)
        for s, st in self._status.items():
            if s not in shown and st != "queued" and s in self._last_alert:
                shown[s] = self._last_alert[s]
        return triage(list(shown.values()), self._status, self.now())

    def peer_compare(self, sat_id: str) -> PeerCompare:
        evs = dict(self.detector._evidence)
        evs[sat_id] = self.detector._evidence_for(sat_id, self.world.telemetry(sat_id))
        return peer_compare(evs, sat_id)

    def tier(self, plan: Plan, sim: SimResult) -> tuple[Literal[1, 2], str | None]:
        """Tier 1 under a flight rule (flight_rules.py), else Tier 2 (the Flight Director decides)."""
        latest = self.verifier.latest(plan.plan_id)
        if latest is None or latest.sim_id != sim.sim_id:
            decision: tuple[int, str | None] = (2, None)   # only the verifier's own latest result counts
        else:
            ctx = flight_rules.RuleContext(now=self.now(), commanded_duty=self._commanded_duty(plan.sat_id))
            decision = flight_rules.tier(plan, sim, ctx)
        self._tiers[plan.plan_id] = decision
        if decision[0] == 2:
            infeasible = self.verifier.rank(plan.sat_id).status == "infeasible"
            safe = any(c.name == "SAFE_MODE" for c in plan.commands)
            self._set_status(plan.sat_id, "safe_mode_recommended" if safe and infeasible else "awaiting_approval")
        return decision

    def mark(self, sat_id: str, status: str) -> None:
        """Set a triage status from outside (agent orchestration, UI)."""
        TriageEntry(sat_id=sat_id, severity="watch", time_to_critical_s=None, next_contact_aos=None,
                    priority=1.0, status=status)       # validates the status value
        self._status[sat_id] = status

    # ---- eps
    def telemetry(self, sat_id: str, t_from: float, t_to: float) -> dict[str, list[float]]:
        tm = self.world.telemetry(sat_id, t_from, t_to)
        return {k: np.asarray(v, float).tolist() for k, v in tm.items()}

    def score_hypotheses(self, sat_id: str) -> HypothesisScores:
        """Fresh diagnosis on all downlinked telemetry (diagnosis prior), fleet-coupled for beta season."""
        evs = dict(self.detector._evidence)
        evs[sat_id] = self.detector._evidence_for(sat_id, self.world.telemetry(sat_id))
        sc = scoring.score_fleet(evs, prior=K.PRIOR)[sat_id]
        self._scores[sat_id] = sc
        self._set_status(sat_id, "working")
        return sc.to_contract()

    # ---- orbit
    def contacts(self) -> list[Contact]:
        return list(self.scenario.contacts)

    def next_contact(self, sat_id: str, t: float) -> Contact | None:
        return self.scenario.next_contact(sat_id, t)

    # ---- verify
    def simulate(self, plan: Plan, hypotheses: str | list[str], monte_carlo: int) -> SimResult:
        self._set_status(plan.sat_id, "working")
        return self.verifier.simulate(plan, hypotheses, monte_carlo)

    # ---- sched
    def schedule(self, requests: list[ScheduleRequest]) -> ScheduleResult:
        """Uplink slots from now on (B2's scheduler never books a slot before `now`)."""
        return self.scheduler.schedule(requests, now=self.now())

    def rank(self, sat_id: str) -> RankResult:
        return self.verifier.rank(sat_id)

    def matrix(self, sat_id: str, complete: bool = True) -> MatrixMsg:
        """The plan x hypothesis matrix for the UI. Results are in the backend's rank order (results[0] is
        the recommended plan) so the frontend never ranks; feasibility shows as the sat status in triage."""
        order = self.verifier.rank(sat_id).ordered
        results = [self.verifier.latest(pid) for pid in order]
        return MatrixMsg(sat_id=sat_id, results=[r for r in results if r is not None], complete=complete)

    # ---- execution
    def execute(self, plan: Plan) -> float:
        """Uplink the plan into the world. A diagnostic returns once its last step has been seen for a dwell."""
        if any(c.t_exec < self.now() for c in plan.commands):
            raise ValueError("t_exec is before the current sim time")
        self.world.execute(plan.sat_id, plan.commands)
        if plan.kind == "diagnostic" and plan.commands:
            self.advance_to(max(c.t_exec for c in plan.commands) + DIAGNOSTIC_DWELL_S)
            self._status[plan.sat_id] = "working"           # a diagnostic informs; it does not resolve
        else:
            tier1 = self._tiers.get(plan.plan_id, (2, None))[0] == 1
            self._status[plan.sat_id] = "auto_executed" if tier1 else "executed"
        return self.now()

    def _set_status(self, sat_id: str, status: str) -> None:
        if self._status.get(sat_id) not in _FINAL or status in _FINAL:
            self._status[sat_id] = status

    def _commanded_duty(self, sat_id: str) -> float:
        duty = float(self.scenario.initial.payload_duty[self.env.sat_ids.index(sat_id)])
        for c in sorted(self.world.executed(sat_id), key=lambda c: c.t_exec):
            if c.t_exec <= self.now() + 1e-6:
                if c.name == "SET_PAYLOAD_DUTY":
                    duty = float(c.args["duty"])
                elif c.name == "SAFE_MODE":
                    duty = 0.0
        return duty

    # ---- internals
    def _initial(self, sat_id: str) -> InitialState:
        i = self.env.sat_ids.index(sat_id)
        ini = self.scenario.initial
        return InitialState(soc=ini.soc[i:i + 1], t_bat_c=ini.t_bat_c[i:i + 1], switch=ini.switch[i:i + 1],
                            payload_duty=ini.payload_duty[i:i + 1], heater_setpoint_c=ini.heater_setpoint_c[i:i + 1])

    def _belief(self, sat_id: str) -> Belief:
        score = self._scores.get(sat_id) or self.detector.scores(sat_id)
        return belief_from(sat_id, self.world.telemetry(sat_id), self.world.executed(sat_id),
                           self._initial(sat_id), score)
