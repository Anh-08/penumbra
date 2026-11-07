"""FleetBackend: A1's Backend port (backend/agent/interfaces.py) on the real stack (T22).

orbit (B2: real illumination, contacts) -> truth world (eps, private faults) -> detector (fleet) ->
hypothesis scores and verifier (verify) -> uplink scheduler (B2) -> execution back into the world.

Everything the agent sees comes from downlinked telemetry and the commands the ground sent; the fault
specs stay inside the scenario's EpsWorld.
"""
from __future__ import annotations

from typing import Literal

import numpy as np

from contracts import Alert, Contact, HypothesisScores, Plan, ScheduleRequest, ScheduleResult, SimResult, TriageEntry
from backend.eps.planning import InitialState
from backend.sched import UplinkScheduler
from backend.verify import constants as K
from backend.verify import scoring
from backend.verify.verifier import Belief, Verifier, belief_from

from .detect import SEVERITY_RANK, Detector
from .scenarios import Scenario

DIAGNOSTIC_DWELL_S = K.DIAGNOSTIC_DWELL_S


class FleetBackend:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.world = scenario.world
        self.env = scenario.env
        self.detector = Detector(self.env, scenario.contacts, self.world.telemetry, self.world.executed,
                                 scenario.initial)
        self.verifier = Verifier(self.env, self._belief)
        self._scores: dict[str, scoring.Score] = {}       # fresh diagnosis scores (score_hypotheses)

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
        """Active alerts, most urgent first. T23 adds the full triage policy (contact timing, peers, status)."""
        alerts = self.detector.detect(self.now())
        inf = float("inf")
        alerts.sort(key=lambda a: (-SEVERITY_RANK[a.severity],
                                   a.time_to_critical_s if a.time_to_critical_s is not None else inf, a.sat_id))
        return [TriageEntry(sat_id=a.sat_id, severity=a.severity, time_to_critical_s=a.time_to_critical_s,
                            next_contact_aos=a.next_contact_aos, priority=float(i + 1), status="queued")
                for i, a in enumerate(alerts)]

    def tier(self, plan: Plan, sim: SimResult) -> tuple[Literal[1, 2], str | None]:
        """Every plan needs the Flight Director until T23's flight rules land."""
        return 2, None

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
        return sc.to_contract()

    # ---- orbit
    def contacts(self) -> list[Contact]:
        return list(self.scenario.contacts)

    def next_contact(self, sat_id: str, t: float) -> Contact | None:
        return self.scenario.next_contact(sat_id, t)

    # ---- verify
    def simulate(self, plan: Plan, hypotheses: str | list[str], monte_carlo: int) -> SimResult:
        return self.verifier.simulate(plan, hypotheses, monte_carlo)

    # ---- sched
    def schedule(self, requests: list[ScheduleRequest]) -> ScheduleResult:
        """Uplink slots from now on. The scheduler places slots from each contact's AOS, so contacts already
        in progress are cut to start now and finished ones are dropped (no slot can be in the past)."""
        now = self.now()
        live = [c if c.aos >= now else c.model_copy(update={"aos": now})
                for c in self.scenario.contacts if c.los > now + 1.0]
        return UplinkScheduler(live).schedule(requests)

    # ---- execution
    def execute(self, plan: Plan) -> float:
        """Uplink the plan into the world. A diagnostic returns once its last step has been seen for a dwell."""
        if any(c.t_exec < self.now() for c in plan.commands):
            raise ValueError("t_exec is before the current sim time")
        self.world.execute(plan.sat_id, plan.commands)
        if plan.kind == "diagnostic" and plan.commands:
            self.advance_to(max(c.t_exec for c in plan.commands) + DIAGNOSTIC_DWELL_S)
        return self.now()

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
