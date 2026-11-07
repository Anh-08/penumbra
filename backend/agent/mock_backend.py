"""SIMULATED stand-in for orbit, eps, verify, fleet and sched, used until those modules merge.

Scenario (like `single-stuck-load`): SAT-07 drains because its AUX load is stuck on. Everything here is made-up
mock data with a toy power budget; none of it is physics and none of it is shown as real.
Deterministic: the same calls give the same results.
"""
from __future__ import annotations

from datetime import datetime, timezone

from contracts import (
    DT,
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

MOCK_T0 = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc).timestamp()
MOCK_SAT = "SAT-07"

# Toy power budget (watts) behind the mock verifier.
_DEFICIT_W = {"H0": 0.0, "H1": 4.0, "H2": 6.0, "H3": 3.0, "H4": 0.0, "H5": 2.0}
_PAYLOAD_W, _AUX_W, _HEATER_W = 10.0, 6.0, 4.0
_BASE_MARGIN = 0.02  # SOC margin with no deficit and no commands
_SOC_PER_W = 0.01  # SOC margin per watt of surplus
_SOC_MIN = 0.20

_SCORES = {"H0": 0.04, "H1": 0.16, "H2": 0.58, "H3": 0.17, "H4": 0.03, "H5": 0.02}
_PLAUSIBLE = ["H1", "H2", "H3"]


class MockBackend:
    def __init__(self, t0: float = MOCK_T0) -> None:
        self.t0 = t0
        self._n_sims = 0
        h = 3600.0
        self._contacts = sorted(
            [
                Contact(sat_id=MOCK_SAT, station_id="FAIRBANKS", aos=t0 - 2.6 * h, los=t0 - 2.6 * h + 480, max_el_deg=35.0),
                Contact(sat_id=MOCK_SAT, station_id="SVALBARD", aos=t0 - 1.0 * h, los=t0 - 1.0 * h + 540, max_el_deg=51.0),
                Contact(sat_id=MOCK_SAT, station_id="SVALBARD", aos=t0 + 600, los=t0 + 1080, max_el_deg=42.0),
                Contact(sat_id=MOCK_SAT, station_id="FAIRBANKS", aos=t0 + 6300, los=t0 + 6780, max_el_deg=31.0),
                Contact(sat_id="SAT-12", station_id="TROLL", aos=t0 + 900, los=t0 + 1400, max_el_deg=25.0),
                Contact(sat_id="SAT-19", station_id="SVALBARD", aos=t0 + 1500, los=t0 + 1900, max_el_deg=38.0),
            ],
            key=lambda c: c.aos,
        )

    # ---- clock
    def now(self) -> float:
        return self.t0

    # ---- fleet
    def detect(self, t: float) -> list[Alert]:
        return [
            Alert(
                alert_id="AL-1",
                sat_id=MOCK_SAT,
                severity="warning",
                time_to_critical_s=4.5 * 3600,
                next_contact_aos=self.t0 + 600,
                reason_key="soc_below_model",
            )
        ]

    def triage(self) -> list[TriageEntry]:
        return [
            TriageEntry(
                sat_id=MOCK_SAT,
                severity="warning",
                time_to_critical_s=4.5 * 3600,
                next_contact_aos=self.t0 + 600,
                priority=1.0,
                status="queued",
            )
        ]

    def tier(self, plan: Plan, sim: SimResult):
        return 2, None

    # ---- eps
    def telemetry(self, sat_id: str, t_from: float, t_to: float) -> dict[str, list[float]]:
        nan = float("nan")
        n = int((t_to - t_from) // DT) + 1
        out: dict[str, list[float]] = {k: [] for k in ("t", "soc", "v_bus", "i_bat", "p_load", "t_bat_c", "load_AUX")}
        windows = [(c.aos, c.los) for c in self._contacts if c.sat_id == sat_id]
        for i in range(n):
            t = t_from + i * DT
            seen = any(a <= t < b for a, b in windows)
            hours = (t - (self.t0 - 3 * 3600.0)) / 3600.0
            soc = 0.62 - (0.035 if sat_id == MOCK_SAT else 0.0) * hours
            row = {
                "soc": soc,
                "v_bus": 7.2 + 1.0 * soc,
                "i_bat": -0.9 if sat_id == MOCK_SAT else 0.1,
                "p_load": 21.0 if sat_id == MOCK_SAT else 15.0,
                "t_bat_c": 12.0,
                "load_AUX": 1.0 if sat_id == MOCK_SAT else 0.0,
            }
            out["t"].append(t)
            for k, v in row.items():
                out[k].append(v if seen else nan)
        return out

    def score_hypotheses(self, sat_id: str) -> HypothesisScores:
        if sat_id != MOCK_SAT:
            return HypothesisScores(scores={"H0": 0.95, "H1": 0.01, "H2": 0.01, "H3": 0.01, "H4": 0.01, "H5": 0.01},
                                    plausible=["H0"])
        return HypothesisScores(scores=dict(_SCORES), plausible=list(_PLAUSIBLE))

    # ---- orbit
    def contacts(self) -> list[Contact]:
        return list(self._contacts)

    def next_contact(self, sat_id: str, t: float) -> Contact | None:
        return next((c for c in self._contacts if c.sat_id == sat_id and c.aos >= t), None)

    # ---- verify (toy budget, not physics)
    def simulate(self, plan: Plan, hypotheses, monte_carlo: int) -> SimResult:
        hyps = list(HYPOTHESES) if hypotheses == "all" else list(hypotheses)
        duty, payload_on, aux_off, heater_shed, safe = 1.0, True, False, 0.0, False
        for c in plan.commands:
            load = c.args.get("load")
            if c.name == "SAFE_MODE":
                safe = True
            elif c.name == "SET_PAYLOAD_DUTY":
                duty = c.args["duty"]
            elif c.name == "SET_HEATER_SETPOINT":
                heater_shed = _HEATER_W * (20.0 - c.args["setpoint_c"]) / 25.0
            elif load == "PAYLOAD":
                payload_on = c.name == "LOAD_ON"
            elif load == "AUX":
                aux_off = c.name == "LOAD_OFF"
            elif load == "HEATER":
                heater_shed = _HEATER_W if c.name == "LOAD_OFF" else 0.0
        if safe:
            duty, payload_on, aux_off, heater_shed = 0.0, False, True, _HEATER_W
        uptime = duty if payload_on else 0.0

        per_hyp, margins = {}, {}
        for h in hyps:
            aux_shed = _AUX_W if aux_off and h != "H2" else 0.0  # under H2 the AUX switch is stuck on
            shed = (1.0 - uptime) * _PAYLOAD_W + aux_shed + heater_shed
            margin = round(_BASE_MARGIN + _SOC_PER_W * (shed - _DEFICIT_W[h]), 4)
            margins[h] = margin
            per_hyp[h] = HypStats(
                pass_rate=min(1.0, max(0.0, 0.5 + 25.0 * margin)),
                min_soc_p5=round(_SOC_MIN + margin, 4),
                min_v_bus=round(7.2 + _SOC_MIN + margin, 4),
                t_bat_min_c=6.0,
                t_bat_max_c=24.0,
                payload_uptime=uptime,
            )
        plausible = [h for h in _PLAUSIBLE if h in per_hyp] if plan.sat_id == MOCK_SAT else ["H0"]
        plausible = [h for h in plausible if h in per_hyp] or hyps
        self._n_sims += 1
        return SimResult(
            sim_id=f"S-{self._n_sims}",
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            per_hyp=per_hyp,
            plausible=plausible,
            passes_all=all(margins[h] >= 0 for h in plausible),
            worst_margin_soc=min(margins[h] for h in plausible),
            payload_uptime_worst=uptime,
            n_commands=len(plan.commands),
        )

    # ---- sched (greedy, one uplink per station at a time)
    def schedule(self, requests: list[ScheduleRequest]) -> ScheduleResult:
        assignments, unscheduled, busy = [], [], []
        for r in sorted(requests, key=lambda r: (-r.priority, r.deadline_t, r.plan_id)):
            slot = None
            for c in self._contacts:
                if c.sat_id != r.sat_id or c.aos < self.t0 or c.aos + r.duration_s > min(c.los, r.deadline_t):
                    continue
                start = c.aos
                for st, s0, s1 in busy:
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
