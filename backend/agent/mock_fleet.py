"""SIMULATED fault storm for the fleet-mode agent (T26), used until scenario `fault-storm` (B1, T27) is merged.

Three satellites alert at once. The designed outcomes follow CONTRACTS.md:
- SAT-19: severe capacity fade (H3). Nothing but SAFE_MODE passes: infeasible, SAFE_MODE recommended.
- SAT-07: stuck AUX load (H2). Shedding AUX does not help; cutting payload duty does. Tier 2 approval.
- SAT-12: mild array degradation (H1). A duty cut passes with a wide margin: Tier 1 under FR-3, auto-executed.
SAT-07 and SAT-12 share a SVALBARD contact with room for one uplink only (a station conflict): SAT-07 is more
urgent, so SAT-12 has to move to its next contact.

Toy power budget, not physics; nothing here is shown as real. Deterministic.
"""
from __future__ import annotations

from datetime import datetime, timezone

from contracts import (
    HYPOTHESES,
    Alert,
    Assignment,
    Contact,
    HypStats,
    HypothesisScores,
    Plan,
    ScheduleRequest,
    ScheduleResult,
    SimResult,
    TriageEntry,
)

STORM_T0 = datetime(2026, 11, 7, 22, 0, tzinfo=timezone.utc).timestamp()
STORM_SATS = ("SAT-19", "SAT-07", "SAT-12")  # triage order: most urgent first
TIER1_MARGIN = 0.20  # same idea as backend/fleet flight rules FR-2 / FR-3
_DWELL_S = 60.0

# per satellite: severity, time to critical, scores, plausible set, power deficit (W) per hypothesis
_SATS = {
    "SAT-19": dict(severity="critical", ttc=1.2 * 3600, plausible=["H3"],
                   scores={"H0": 0.01, "H1": 0.02, "H2": 0.02, "H3": 0.81, "H4": 0.13, "H5": 0.01},
                   deficit={"H0": 0.0, "H1": 20.0, "H2": 20.0, "H3": 24.0, "H4": 18.0, "H5": 20.0}),
    "SAT-07": dict(severity="warning", ttc=4.5 * 3600, plausible=["H2"],
                   scores={"H0": 0.01, "H1": 0.03, "H2": 0.91, "H3": 0.03, "H4": 0.01, "H5": 0.01},
                   deficit={"H0": 0.0, "H1": 4.0, "H2": 6.0, "H3": 3.0, "H4": 0.0, "H5": 2.0}),
    "SAT-12": dict(severity="warning", ttc=9.0 * 3600, plausible=["H1"],
                   scores={"H0": 0.04, "H1": 0.88, "H2": 0.03, "H3": 0.03, "H4": 0.01, "H5": 0.01},
                   deficit={"H0": 0.0, "H1": 3.0, "H2": 3.0, "H3": 3.0, "H4": 0.0, "H5": 2.0}),
}
_PAYLOAD_W, _AUX_W, _HEATER_W = 10.0, 6.0, 4.0
_BASE_MARGIN, _SOC_PER_W, _SOC_MIN = 0.02, 0.01, 0.20
_SAFE_SHED_W = 26.0  # SAFE_MODE sheds payload, AUX, heater and part of the bus


class MockStormBackend:
    def __init__(self, t0: float = STORM_T0) -> None:
        self.t0 = t0
        self._t = t0
        self._n_sims = 0
        self.executed: list[Plan] = []
        self._status = {s: "queued" for s in STORM_SATS}
        self._tiers: dict[str, tuple[int, str | None]] = {}
        m = 60.0
        self._contacts = sorted(
            [
                Contact(sat_id="SAT-19", station_id="FAIRBANKS", aos=t0 + 5 * m, los=t0 + 13 * m, max_el_deg=40.0),
                # the conflict: one SVALBARD window, room for a single 60 s uplink
                Contact(sat_id="SAT-07", station_id="SVALBARD", aos=t0 + 8 * m, los=t0 + 9 * m, max_el_deg=33.0),
                Contact(sat_id="SAT-12", station_id="SVALBARD", aos=t0 + 8 * m, los=t0 + 9 * m, max_el_deg=29.0),
                Contact(sat_id="SAT-12", station_id="TROLL", aos=t0 + 50 * m, los=t0 + 58 * m, max_el_deg=36.0),
                Contact(sat_id="SAT-07", station_id="FAIRBANKS", aos=t0 + 100 * m, los=t0 + 108 * m, max_el_deg=31.0),
                Contact(sat_id="SAT-19", station_id="SVALBARD", aos=t0 + 95 * m, los=t0 + 103 * m, max_el_deg=45.0),
            ],
            key=lambda c: (c.aos, c.sat_id),
        )

    # ---- clock
    def now(self) -> float:
        return self._t

    # ---- fleet
    def detect(self, t: float) -> list[Alert]:
        return [
            Alert(alert_id=f"AL-{i}", sat_id=s, severity=_SATS[s]["severity"], time_to_critical_s=_SATS[s]["ttc"],
                  next_contact_aos=self._first_aos(s), reason_key="soc_below_model")
            for i, s in enumerate(STORM_SATS, 1)
        ]

    def triage(self) -> list[TriageEntry]:
        return [
            TriageEntry(sat_id=s, severity=_SATS[s]["severity"], time_to_critical_s=_SATS[s]["ttc"],
                        next_contact_aos=self._first_aos(s), priority=float(i), status=self._status[s])
            for i, s in enumerate(STORM_SATS, 1)
        ]

    def tier(self, plan: Plan, sim: SimResult):
        decision: tuple[int, str | None] = (2, None)
        cuts_duty = [c for c in plan.commands if c.name == "SET_PAYLOAD_DUTY" and c.args["duty"] < 0.8]
        shed_only = all(c in cuts_duty or (c.name == "LOAD_OFF" and c.args["load"] == "AUX") for c in plan.commands)
        if plan.kind == "diagnostic" and sim.passes_all:
            decision = (1, "FR-1")
        elif sim.passes_all and sim.worst_margin_soc >= TIER1_MARGIN and cuts_duty and shed_only:
            decision = (1, "FR-2" if len(cuts_duty) == len(plan.commands) else "FR-3")
        self._tiers[plan.plan_id] = decision
        if decision[0] == 2:
            safe = any(c.name == "SAFE_MODE" for c in plan.commands)
            self._status[plan.sat_id] = "safe_mode_recommended" if safe else "awaiting_approval"
        return decision

    def execute(self, plan: Plan) -> float:
        if any(c.t_exec < self._t for c in plan.commands):
            raise ValueError("t_exec is before the current sim time")
        self.executed.append(plan)
        if plan.kind == "diagnostic":
            self._t = max(self._t, max(c.t_exec for c in plan.commands) + _DWELL_S)
        else:
            tier1 = self._tiers.get(plan.plan_id, (2, None))[0] == 1
            self._status[plan.sat_id] = "auto_executed" if tier1 else "executed"
        return self._t

    # ---- eps
    def telemetry(self, sat_id: str, t_from: float, t_to: float) -> dict[str, list[float]]:
        t = [t_from + k * 600.0 for k in range(int((t_to - t_from) // 600.0) + 1)]
        drop = {"SAT-19": 0.08, "SAT-07": 0.035, "SAT-12": 0.015}.get(sat_id, 0.0)
        soc = [0.6 - drop * (x - t_from) / 3600.0 for x in t]
        return {"t": t, "soc": soc, "v_bus": [7.2 + s for s in soc], "t_bat_c": [12.0] * len(t)}

    def score_hypotheses(self, sat_id: str) -> HypothesisScores:
        if sat_id not in _SATS:
            return HypothesisScores(scores={"H0": 0.95, "H1": 0.01, "H2": 0.01, "H3": 0.01, "H4": 0.01, "H5": 0.01},
                                    plausible=["H0"])
        if self._status.get(sat_id) == "queued":
            self._status[sat_id] = "working"
        return HypothesisScores(scores=dict(_SATS[sat_id]["scores"]), plausible=list(_SATS[sat_id]["plausible"]))

    # ---- orbit
    def contacts(self) -> list[Contact]:
        return list(self._contacts)

    def next_contact(self, sat_id: str, t: float) -> Contact | None:
        """The contact in progress at t, else the next one (like the real scenarios)."""
        return next((c for c in self._contacts if c.sat_id == sat_id and c.los > t), None)

    # ---- verify (toy budget, not physics)
    def simulate(self, plan: Plan, hypotheses, monte_carlo: int) -> SimResult:
        info = _SATS.get(plan.sat_id, {"deficit": {h: 0.0 for h in HYPOTHESES}, "plausible": ["H0"]})
        hyps = list(HYPOTHESES) if hypotheses == "all" else list(hypotheses)
        duty, aux_off, heater_off, safe = 0.8, False, False, False
        for c in plan.commands:
            if c.name == "SAFE_MODE":
                safe = True
            elif c.name == "SET_PAYLOAD_DUTY":
                duty = c.args["duty"]
            elif c.args.get("load") == "AUX":
                aux_off = c.name == "LOAD_OFF"
            elif c.args.get("load") == "HEATER":
                heater_off = c.name == "LOAD_OFF"
            elif c.args.get("load") == "PAYLOAD" and c.name == "LOAD_OFF":
                duty = 0.0
        uptime = 0.0 if safe else duty
        per_hyp, margins = {}, {}
        for h in hyps:
            if plan.kind == "diagnostic":
                shed = info["deficit"][h]  # a short test: no lasting change
            elif safe:
                shed = _SAFE_SHED_W
            else:
                aux = _AUX_W if aux_off and h != "H2" else 0.0  # under H2 the AUX relay is stuck on
                shed = (0.8 - duty) * _PAYLOAD_W * 2.5 + aux + (_HEATER_W if heater_off else 0.0)
            margin = round(_BASE_MARGIN + _SOC_PER_W * (shed - info["deficit"][h]), 4)
            margins[h] = margin
            per_hyp[h] = HypStats(pass_rate=min(1.0, max(0.0, 0.5 + 25.0 * margin)),
                                  min_soc_p5=round(_SOC_MIN + margin, 4), min_v_bus=round(7.2 + _SOC_MIN + margin, 4),
                                  t_bat_min_c=6.0, t_bat_max_c=24.0, payload_uptime=uptime)
        plausible = [h for h in info["plausible"] if h in per_hyp] or hyps
        self._n_sims += 1
        return SimResult(
            sim_id=f"S-{self._n_sims}", plan_id=plan.plan_id, plan_hash=plan.plan_hash, per_hyp=per_hyp,
            plausible=plausible, passes_all=all(margins[h] >= 0 for h in plausible),
            worst_margin_soc=min(margins[h] for h in plausible), payload_uptime_worst=uptime,
            n_commands=len(plan.commands),
        )

    # ---- sched (greedy by priority, one uplink per station at a time)
    def schedule(self, requests: list[ScheduleRequest]) -> ScheduleResult:
        assignments, unscheduled, busy = [], [], []
        for r in sorted(requests, key=lambda r: (-r.priority, r.deadline_t, r.plan_id)):  # higher = more important
            slot = None
            for c in self._contacts:
                start = max(c.aos, self._t)
                if c.sat_id != r.sat_id or start + r.duration_s > min(c.los, r.deadline_t):
                    continue
                for st, s0, s1 in sorted(busy, key=lambda b: b[1]):
                    if st == c.station_id and start < s1 and s0 < start + r.duration_s:
                        start = s1
                if start + r.duration_s <= min(c.los, r.deadline_t):
                    slot = (c.station_id, start, start + r.duration_s)
                    break
            if slot is None:
                unscheduled.append(r.plan_id)
            else:
                busy.append(slot)
                assignments.append(Assignment(plan_id=r.plan_id, station_id=slot[0], start_t=slot[1], end_t=slot[2]))
        return ScheduleResult(assignments=assignments, unscheduled=unscheduled, solver="greedy", optimal=False,
                              solve_ms=0.0)

    def _first_aos(self, sat_id: str) -> float | None:
        c = self.next_contact(sat_id, self._t)
        return c.aos if c else None
