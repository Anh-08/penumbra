"""Tool dispatcher: checks tool-call args, runs the safety layer, calls the Backend interfaces, and keeps the
agent's registry of plans, simulation results, alerts and approvals.

Bad input never raises out of `dispatch`; the model gets `{"error": ...}` back and can try again.
Every result is plain JSON (no NaN, no pydantic objects), ready for the model and the flight recorder.
`approve()` is the check behind POST /api/approve; `execute()` runs an approved plan afterwards.
`operator_ticket()` gives the Flight Director's button an approval_id for any simulated plan, including a red one,
so pressing it shows the refusal with the failing hypotheses (physics has veto). The agent's own
`request_approval` tool still refuses failed plans. Tickets for several plans of one satellite can coexist; once
one recovery plan is approved (or auto-executed), approving another one for that satellite is refused (superseded).

Diagnostic tests (T20): `propose_diagnostic_test` time-tags untagged steps one DIAGNOSTIC_STEP_S apart inside
the pass. A Tier 1 plan (pre-approved flight rule) executes as soon as `request_approval` is called. After a
diagnostic executes, `propose_plan` for that satellite is refused until `score_hypotheses` has been called
again, so every plan is built on the diagnostic's evidence.

Infeasible (T26): after MAX_FAILED_PLANS failed plans for a satellite, only a SAFE_MODE plan may still be
proposed, and simulate results say so; if the backend offers rank(sat_id), they also carry its status.

Collision avoidance (T31): `get_conjunctions` lists the backend's conjunctions; a `propose_plan` with a `CAM_BURN`
becomes a kind "maneuver" plan (an untimed burn defaults to the start of its window), checked by
safety.check_maneuver at every step. A CAM is never Tier 1, and a conjunction gets at most one approved burn.

Station conflicts (T26): `schedule_uplink` re-solves every pending request together. If the plan gets no slot,
its contact is marked blocked for that satellite and the next proposal targets the following contact. Plans
that lost the slot they had are reported as `bumped` (and in `self.bumped`). `request_approval` needs a slot.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import BaseModel, ValidationError

from contracts import (
    AGENT_TOOLS,
    AGENT_TOOLS_CAM,
    HYPOTHESES,
    HYPOTHESIS_LABELS,
    SAT_IDS,
    Alert,
    ApprovalRefused,
    ApprovalRequestMsg,
    Conjunction,
    Assignment,
    Plan,
    ScheduleRequest,
    ScheduleResult,
    SimResult,
)

from backend.verify.diagnostics import DWELL_S

from . import safety
from .interfaces import Backend, NullRecorder, Recorder
from .safety import SafetyError
from .tools import DEFAULT_MONTE_CARLO, MAX_MONTE_CARLO, TOOL_SCHEMAS

TELEMETRY_WINDOW_S = 3 * 3600.0  # default get_telemetry window, ending now
MAX_TELEMETRY_POINTS = 120  # keep tool results small for the model
UPLINK_DURATION_S = 60.0  # station time requested per plan uplink
DIAGNOSTIC_STEP_S = DWELL_S  # dwell per diagnostic step, same as backend.verify.diagnostics
MAX_FAILED_PLANS = 4  # failed non-SAFE_MODE plans per satellite before the search stops (infeasible)


class ToolArgError(ValueError):
    """The model sent something the tool cannot accept."""


@dataclass(frozen=True)
class ToolOutcome:
    result: dict[str, Any]
    plan_hash: str | None = None  # copied to the recorder event when the call concerns one plan
    events: tuple[tuple[str, dict[str, Any]], ...] = ()  # extra recorder events (kind, payload), e.g. execution


class Dispatcher:
    def __init__(self, backend: Backend, created_by: str = "agent") -> None:
        self.backend = backend
        self.created_by = created_by
        self.plans: dict[str, Plan] = {}
        self.sims: dict[str, SimResult] = {}  # latest SimResult per plan_id
        self.sim_results: dict[str, SimResult] = {}  # every SimResult by sim_id
        self.alerts: dict[str, Alert] = {}
        self.approvals: dict[str, ApprovalRequestMsg] = {}
        self.assignments: dict[str, Assignment] = {}  # latest scheduler assignment per plan_id
        self.last_schedule: ScheduleResult | None = None
        self._decided: set[str] = set()
        self._executed: set[str] = set()
        self._flight_rules: dict[str, str | None] = {}  # approval_id -> flight rule id (Tier 1)
        self._needs_rescore: dict[str, str] = {}  # sat_id -> diagnostic plan_id executed since the last score
        self._blocked: dict[str, set[tuple[str, float]]] = {}  # sat_id -> (station_id, aos) with no free slot
        self.bumped: set[str] = set()  # plan_ids that lost their uplink slot to another plan
        self._executed_plans: set[str] = set()
        self._fixed: dict[str, Assignment] = {}  # booked slots of executed plans: never moved, never given away
        self._schedule_requests: dict[str, ScheduleRequest] = {}
        self._schemas = {s["name"]: s["parameters"] for s in TOOL_SCHEMAS}
        self.conjunctions: dict[str, Conjunction] = {}  # CJ-n seen through get_conjunctions (narration)
        self._handlers = {name: getattr(self, f"_tool_{name}") for name in AGENT_TOOLS + AGENT_TOOLS_CAM}

    # ------------------------------------------------------------------ entry point
    def dispatch(self, name: str, args: Mapping[str, Any] | None) -> ToolOutcome:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolOutcome({"error": f"unknown tool {name!r}; tools are {', '.join(self._handlers)}"})
        try:
            outcome = handler(**self._check_args(name, args))
        except ValidationError as exc:
            return ToolOutcome({"error": "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())})
        except (ValueError, LookupError) as exc:  # SafetyError, ToolArgError, or a backend refusing the input
            return ToolOutcome({"error": str(exc) or type(exc).__name__})
        return ToolOutcome(jsonable(outcome.result), outcome.plan_hash,
                           tuple((kind, jsonable(payload)) for kind, payload in outcome.events))

    def _check_args(self, name: str, args: Mapping[str, Any] | None) -> dict[str, Any]:
        if args is None:
            args = {}
        if not isinstance(args, Mapping):
            raise ToolArgError(f"{name}: arguments must be an object")
        schema = self._schemas[name]
        unknown = set(args) - set(schema["properties"])
        if unknown:
            raise ToolArgError(f"{name}: unknown argument(s) {sorted(unknown)}")
        missing = [k for k in schema.get("required", []) if k not in args]
        if missing:
            raise ToolArgError(f"{name}: missing argument(s) {missing}")
        return dict(args)

    def lookup(self, obj_id: str) -> BaseModel | None:
        """Narration registry: plan, SimResult, approval or alert by id."""
        for prefix, table in (("AP-", self.approvals), ("AL-", self.alerts), ("P-", self.plans),
                              ("S-", self.sim_results), ("CJ-", self.conjunctions)):
            if obj_id.startswith(prefix):
                return table.get(obj_id)
        return None

    def record_sim(self, sim: SimResult) -> None:
        """Store a SimResult as the latest one for its plan."""
        self.sims[sim.plan_id] = sim
        self.sim_results[sim.sim_id] = sim

    # ------------------------------------------------------------------ approval (POST /api/approve)
    def approve(self, approval_id: str, plan_hash: str, recorder: Recorder | None = None) -> Plan | ApprovalRefused:
        """Return the Plan to execute, or ApprovalRefused. Executes only if the hash equals the hash of the
        latest SimResult for that plan, the plan passed verification, and the uplink window is still open.
        A plan that failed verification can never be approved. There is no override."""
        rec = recorder if recorder is not None else NullRecorder()
        ticket = self.approvals.get(approval_id)
        failing: list[str] = []
        try:
            if ticket is None:
                raise SafetyError("unknown_approval", f"no approval {approval_id!r}")
            if approval_id in self._decided:
                raise SafetyError("already_decided", f"{approval_id} was already approved")
            plan = self.plans[ticket.plan_id]
            sim = self.sims.get(plan.plan_id)
            if sim is None:
                raise SafetyError("not_verified", f"{plan.plan_id} has no simulation result")
            if not sim.passes_all:
                failing = safety.failing_hypotheses(sim)
                raise SafetyError("failed_verification", f"{plan.plan_id} failed verification ({sim.sim_id})")
            if plan.kind == "plan":  # one recovery plan per satellite: the first approved one wins (after the
                # verification checks, so a red plan always shows why it failed)
                done = next((t.plan_id for a, t in self.approvals.items() if a in self._decided
                             and t.sat_id == plan.sat_id and self.plans[t.plan_id].kind == "plan"), None)
                if done is not None:
                    raise SafetyError("superseded", f"{done} was already approved for {plan.sat_id}")
            if plan.kind == "maneuver":
                self._check_one_burn(plan)
            current = safety.plan_hash(plan)
            if not (plan_hash == current == sim.plan_hash == ticket.plan_hash):
                raise SafetyError("stale_hash", f"hash does not match the latest verified version of {plan.plan_id}")
            safety.check_plan(plan, self.backend.contacts(), now=self.backend.now(), conjunctions=self._conjunctions())
            slot = self.assignments.get(plan.plan_id)
            if slot is None and plan.plan_id in self._schedule_requests:
                raise SafetyError("uplink_closed", f"{plan.plan_id} has no uplink slot (it lost it to another plan)")
            if slot is None:
                raise SafetyError("not_scheduled", f"{plan.plan_id} has no booked uplink; schedule it first")
            if slot is not None and self.backend.now() >= slot.end_t:
                raise SafetyError("uplink_closed", f"the scheduled uplink for {plan.plan_id} has passed")
        except SafetyError as exc:
            refused = ApprovalRefused(reason_key=exc.reason_key, failing_hypotheses=failing)
            rec.record(
                "approval_refused",
                {"approval_id": approval_id, "reason_key": exc.reason_key, "failing_hypotheses": failing,
                 "detail": exc.args[0]},
                plan_hash=ticket.plan_hash if ticket else None,
            )
            return refused
        self._decided.add(approval_id)
        rec.record(
            "approval",
            {"approval_id": approval_id, "plan_id": plan.plan_id, "sat_id": plan.sat_id, "sim_id": sim.sim_id},
            plan_hash=current,
        )
        return plan

    def operator_ticket(self, plan_id: str) -> ApprovalRequestMsg:
        """Approval ticket for the Flight Director's button on any simulated plan (green or red).

        Returns the plan's open ticket if it has one for its latest simulation, else a new Tier 2 ticket (a human
        decides; the tier policy is not consulted). Nothing is executed here: `approve()` still decides, and a plan
        that failed verification is refused there with its failing hypotheses. Raises SafetyError if the plan is
        unknown or was never simulated."""
        plan = self.plans.get(plan_id) if isinstance(plan_id, str) else None
        if plan is None:
            raise SafetyError("unknown_plan", f"no plan {plan_id!r}")
        sim = self.sims.get(plan.plan_id)
        if sim is None:
            raise SafetyError("not_verified", f"{plan.plan_id} has no simulation result")
        for ticket in reversed(list(self.approvals.values())):
            if (ticket.plan_id == plan.plan_id and ticket.sim_id == sim.sim_id
                    and ticket.approval_id not in self._decided):
                return ticket
        approval_id = f"AP-{len(self.approvals) + 1}"
        ticket = ApprovalRequestMsg(approval_id=approval_id, plan_id=plan.plan_id, plan_hash=safety.plan_hash(plan),
                                    sat_id=plan.sat_id, tier=2, sim_id=sim.sim_id)
        self.approvals[approval_id] = ticket
        return ticket

    def execute(self, approval_id: str, recorder: Recorder | None = None) -> dict[str, Any]:
        """Uplink and run a plan the Flight Director approved through `approve()`. Records an `execution` event
        and returns its payload. Raises SafetyError if the approval was not granted, was already executed, or
        the uplink window has closed."""
        ticket = self.approvals.get(approval_id)
        if ticket is None or approval_id not in self._decided:
            raise SafetyError("not_approved", f"{approval_id} has not been approved")
        if approval_id in self._executed:
            raise SafetyError("already_executed", f"{approval_id} was already executed")
        plan = self.plans[ticket.plan_id]
        safety.check_plan(plan, self.backend.contacts(), now=self.backend.now(), conjunctions=self._conjunctions())
        payload = jsonable(self._run(approval_id))
        (recorder if recorder is not None else NullRecorder()).record("execution", payload, plan_hash=ticket.plan_hash)
        return payload

    # ------------------------------------------------------------------ tools
    def _tool_get_fleet_status(self) -> ToolOutcome:
        t = self.backend.now()
        alerts = self.backend.detect(t)
        self.alerts.update((a.alert_id, a) for a in alerts)
        return ToolOutcome({"t": t, "triage": self.backend.triage(), "alerts": alerts})

    def _tool_get_telemetry(self, sat_id: Any, t_from: Any = None, t_to: Any = None) -> ToolOutcome:
        sat_id = _sat_id(sat_id)
        t_to = self.backend.now() if t_to is None else _finite("t_to", t_to)
        t_from = t_to - TELEMETRY_WINDOW_S if t_from is None else _finite("t_from", t_from)
        if t_from >= t_to:
            raise ToolArgError("t_from must be before t_to")
        data = self.backend.telemetry(sat_id, t_from, t_to)
        times = [float(x) for x in data["t"]]
        channels = {k: [float(x) for x in v] for k, v in data.items() if k != "t"}
        # Keep only downlinked samples (any channel finite), then thin evenly to MAX_TELEMETRY_POINTS.
        rows = [i for i in range(len(times)) if any(math.isfinite(v[i]) for v in channels.values())]
        if len(rows) > MAX_TELEMETRY_POINTS:
            step = len(rows) / MAX_TELEMETRY_POINTS
            rows = [rows[int(k * step)] for k in range(MAX_TELEMETRY_POINTS)]
        return ToolOutcome(
            {
                "sat_id": sat_id,
                "t": [times[i] for i in rows],
                "channels": {k: [v[i] for i in rows] for k, v in channels.items()},
            }
        )

    def _tool_score_hypotheses(self, sat_id: Any) -> ToolOutcome:
        sat_id = _sat_id(sat_id)
        scores = self.backend.score_hypotheses(sat_id)
        self._needs_rescore.pop(sat_id, None)
        return ToolOutcome(
            {
                "sat_id": sat_id,
                "scores": scores.scores,
                "plausible": scores.plausible,
                "labels": {h: HYPOTHESIS_LABELS[h] for h in scores.scores},
            }
        )

    def _tool_propose_diagnostic_test(self, sat_id: Any, commands: Any) -> ToolOutcome:
        return self._propose(sat_id, commands, kind="diagnostic")

    def _tool_propose_plan(self, sat_id: Any, commands: Any) -> ToolOutcome:
        return self._propose(sat_id, commands, kind="plan")

    def _tool_simulate(
        self, plan_id: Any, hypotheses: Any = "all", monte_carlo: Any = DEFAULT_MONTE_CARLO
    ) -> ToolOutcome:
        plan = self._plan(plan_id)
        hyps = _hypotheses(hypotheses)
        n = _monte_carlo(monte_carlo)
        sim = self.backend.simulate(plan, hyps, n)
        if sim.plan_id != plan.plan_id or sim.plan_hash != safety.plan_hash(plan):
            raise RuntimeError(f"verifier returned a result for another plan ({sim.plan_id})")
        self.record_sim(sim)
        out = sim.model_dump()
        rank = getattr(self.backend, "rank", None)
        status = None
        if callable(rank):
            try:
                status = getattr(rank(plan.sat_id), "status", None)
            except (ValueError, LookupError):
                status = None
        if status is not None:
            out["rank_status"] = status
        if status == "infeasible" or self._failed_plans(plan.sat_id) >= MAX_FAILED_PLANS:
            out["hint"] = _infeasible_hint(plan.sat_id, self._failed_plans(plan.sat_id))
        return ToolOutcome(out, plan.plan_hash)

    def _tool_schedule_uplink(self, plan_id: Any) -> ToolOutcome:
        plan = self._verified(plan_id)
        self._schedule_requests[plan.plan_id] = ScheduleRequest(
            plan_id=plan.plan_id,
            sat_id=plan.sat_id,
            priority=self._priority(plan.sat_id),
            deadline_t=_uplink_deadline(plan),
            duration_s=UPLINK_DURATION_S,
        )
        # Re-solve with every pending request so station conflicts between satellites are resolved together.
        # Executed plans are not re-solved: their slots stay fixed, and a new slot that overlaps one is refused.
        result = self.backend.schedule(list(self._schedule_requests.values()))
        contacts = self.backend.contacts()
        for a in result.assignments:  # the scheduler is checked too: every slot must be inside a contact
            req = self._schedule_requests.get(a.plan_id)
            if req is None:
                raise RuntimeError(f"scheduler returned unknown plan {a.plan_id}")
            safety.check_assignment(a, req.sat_id, contacts)
        self.last_schedule = result
        now = self.backend.now()
        fixed = {p: a for p, a in self._fixed.items() if a.end_t > now}
        before = set(self.assignments)
        self.assignments = dict(fixed)
        self.assignments.update(
            (a.plan_id, a) for a in result.assignments if not any(_overlap(a, f) for f in fixed.values())
        )
        bumped = sorted(p for p in before - set(self.assignments)
                        if p != plan.plan_id and p not in self._executed_plans)
        self.bumped.update(bumped)
        self.bumped -= set(self.assignments)
        mine = self.assignments.get(plan.plan_id)
        out: dict[str, Any] = {"plan_id": plan.plan_id, "scheduled": mine is not None, "solver": result.solver}
        if mine is not None:
            out.update(station_id=mine.station_id, start_t=mine.start_t, end_t=mine.end_t)
        else:
            self._schedule_requests.pop(plan.plan_id, None)  # stop competing for a contact it cannot get
            self._blocked.setdefault(plan.sat_id, set()).add((plan.uplink.station_id, plan.uplink.aos))
            out["reason"] = (
                f"no station is free for {plan.plan_id} in its {plan.uplink.station_id} contact (another plan has "
                "priority); propose the same commands again to target the next contact, then simulate and schedule"
            )
        if bumped:
            out["bumped"] = bumped
        return ToolOutcome(out, plan.plan_hash)

    def _tool_request_approval(self, plan_id: Any) -> ToolOutcome:
        plan = self._verified(plan_id)
        sim = self.sims[plan.plan_id]
        if plan.kind == "plan" and plan.sat_id in self._needs_rescore:
            raise ToolArgError(_rescore_message(plan.sat_id, self._needs_rescore[plan.sat_id]))
        if plan.plan_id not in self.assignments:
            raise ToolArgError(f"{plan.plan_id} has no uplink slot; call schedule_uplink first")
        tier, flight_rule = self.backend.tier(plan, sim)
        if plan.kind == "maneuver" or any(c.name == "CAM_BURN" for c in plan.commands):
            tier, flight_rule = 2, None  # a collision-avoidance burn is never Tier 1, whatever the backend says
        approval_id = f"AP-{len(self.approvals) + 1}"
        self.approvals[approval_id] = ApprovalRequestMsg(
            approval_id=approval_id,
            plan_id=plan.plan_id,
            plan_hash=safety.plan_hash(plan),
            sat_id=plan.sat_id,
            tier=tier,
            sim_id=sim.sim_id,
        )
        out: dict[str, Any] = {"approval_id": approval_id, "plan_id": plan.plan_id, "tier": tier}
        if tier != 1:
            out["status"] = "awaiting_approval"
            return ToolOutcome(out, plan.plan_hash)
        # Tier 1: a pre-approved flight rule covers this plan, so it executes now and the trace shows the rule.
        self._flight_rules[approval_id] = flight_rule
        payload = self._run(approval_id)
        self._decided.add(approval_id)
        out.update(status="auto_executed", flight_rule=flight_rule, t_done=payload["t_done"])
        return ToolOutcome(out, plan.plan_hash, events=(("execution", payload),))

    # ------------------------------------------------------------------ helpers
    def _propose(self, sat_id: Any, commands: Any, kind: str) -> ToolOutcome:
        sat_id = _sat_id(sat_id)
        if not isinstance(commands, list) or not commands:
            raise ToolArgError("commands must be a non-empty list")
        if kind == "plan" and sat_id in self._needs_rescore:
            raise ToolArgError(_rescore_message(sat_id, self._needs_rescore[sat_id]))
        safe = any(isinstance(c, Mapping) and c.get("name") == "SAFE_MODE" for c in commands)
        if kind == "plan" and not safe and self._failed_plans(sat_id) >= MAX_FAILED_PLANS:
            raise ToolArgError(_infeasible_hint(sat_id, self._failed_plans(sat_id)))
        now = self.backend.now()
        uplink = self._next_uplink(sat_id, now)
        if uplink is None:
            raise ToolArgError(f"{sat_id} has no contact window left in the horizon")
        start = max(uplink.aos, now)  # never time-tag a command in the past, even when the pass is in progress
        burns = [c for c in commands if isinstance(c, Mapping) and c.get("name") == "CAM_BURN"]
        if burns and kind != "plan":
            raise SafetyError("bad_args", "CAM_BURN goes in propose_plan (a maneuver plan), never in a diagnostic")
        if len(burns) > 1:
            raise SafetyError("cam_duplicate", "a maneuver plan has exactly one CAM_BURN (one burn per conjunction)")
        conjunctions = self._conjunctions() if burns else None
        if kind == "diagnostic":
            cmds = self._diagnostic_steps(commands, start, uplink)
        else:
            cmds = [safety.parse_command(c, default_t_exec=self._burn_start(sat_id, c, start, conjunctions, now))
                    for c in commands]
        if any(c.t_exec < now for c in cmds):
            raise SafetyError("bad_time", "t_exec is before the current time")
        plan_id = f"P-{len(self.plans) + 1}"
        plan = Plan(plan_id=plan_id, sat_id=sat_id, kind="maneuver" if burns else kind, uplink=uplink, commands=cmds,
                    created_by=self.created_by)
        safety.check_plan(plan, self.backend.contacts(), now=now, conjunctions=conjunctions)
        if plan.kind == "maneuver":
            self._check_one_burn(plan)
        self.plans[plan_id] = plan
        return ToolOutcome({"plan_id": plan_id}, plan.plan_hash)

    def _conjunctions(self) -> list[Conjunction] | None:
        """The backend's conjunctions (orbit, T31), or None if this backend has none."""
        source = getattr(self.backend, "conjunctions", None)
        return list(source()) if callable(source) else None

    def _burn_start(self, sat_id: str, raw: Any, start: float, conjunctions: list[Conjunction] | None,
                    now: float) -> float:
        """Default t_exec: a CAM_BURN without t_exec executes at the start of its window (never before the
        uplink or now); any other command at the uplink."""
        if not (isinstance(raw, Mapping) and raw.get("name") == "CAM_BURN"):
            return start
        window_id = (raw.get("args") or {}).get("window_id") if isinstance(raw.get("args"), Mapping) else None
        if window_id not in ("W1", "W2", "W3"):
            return start  # parse_command rejects it with a clear message
        cj = safety.resolve_conjunction(sat_id, window_id, conjunctions, now)
        return max(cj.window(window_id).t_start, start)

    def _check_one_burn(self, plan: Plan) -> None:
        """One approved burn per conjunction: a maneuver for a conjunction that already has an approved (or
        executed) burn is refused."""
        cj = safety.check_maneuver(plan, self._conjunctions(), now=None)
        for approval_id in self._decided:
            other = self.plans.get(self.approvals[approval_id].plan_id)
            if other is None or other.plan_id == plan.plan_id or other.kind != "maneuver":
                continue
            if safety.check_maneuver(other, self._conjunctions(), now=None).conj_id == cj.conj_id:
                raise SafetyError("cam_duplicate", f"{cj.conj_id} already has an approved burn ({other.plan_id})")

    def _tool_get_conjunctions(self, sat_id: Any) -> ToolOutcome:
        sat_id = _sat_id(sat_id)
        source = self._conjunctions()
        if source is None:
            return ToolOutcome({"sat_id": sat_id, "conjunctions": [], "note": "no conjunction data on this backend"})
        now = self.backend.now()
        found = sorted((cj for cj in source if cj.sat_id == sat_id and cj.tca > now), key=lambda cj: cj.tca)
        self.conjunctions.update((cj.conj_id, cj) for cj in found)
        return ToolOutcome({"sat_id": sat_id, "conjunctions": [cj.model_dump() for cj in found]})

    def _failed_plans(self, sat_id: str) -> int:
        """Recovery plans (not SAFE_MODE, not diagnostics) for this satellite whose latest simulation failed."""
        return sum(
            1 for pid, sim in self.sims.items()
            if not sim.passes_all and self.plans[pid].sat_id == sat_id and self.plans[pid].kind == "plan"
            and not any(c.name == "SAFE_MODE" for c in self.plans[pid].commands)
        )

    def _next_uplink(self, sat_id: str, now: float) -> Any:
        """Next contact for the uplink, skipping contacts where this satellite could not get a station."""
        blocked = self._blocked.get(sat_id, set())
        t = now
        for _ in range(50):
            c = self.backend.next_contact(sat_id, t)
            if c is None or (c.station_id, c.aos) not in blocked:
                return c
            t = max(t, c.los) + 1.0
        return None

    def _diagnostic_steps(self, commands: list[Any], start: float, uplink: Any) -> list[Any]:
        """Untagged steps run one DIAGNOSTIC_STEP_S apart, after one step of lead time for the uplink; the test,
        including the dwell after its last step, must end before LOS so its telemetry comes down this pass."""
        first = start + DIAGNOSTIC_STEP_S
        cmds = [safety.parse_command(c, default_t_exec=first + k * DIAGNOSTIC_STEP_S) for k, c in enumerate(commands)]
        end = max(c.t_exec for c in cmds) + DIAGNOSTIC_STEP_S
        if end > uplink.los:
            left = max(0.0, uplink.los - start)
            raise ToolArgError(
                f"the diagnostic needs {end - start:.0f} s but the {uplink.station_id} pass has {left:.0f} s left; "
                "use fewer steps"
            )
        return cmds

    def _run(self, approval_id: str) -> dict[str, Any]:
        """Execute an approved (or Tier 1) plan through the backend and return the `execution` payload."""
        ticket = self.approvals[approval_id]
        plan = self.plans[ticket.plan_id]
        t_done = float(self.backend.execute(plan))
        self._executed.add(approval_id)
        self._executed_plans.add(plan.plan_id)
        self._schedule_requests.pop(plan.plan_id, None)
        if plan.plan_id in self.assignments:  # keep the station booked for this uplink: no other plan may take it
            self._fixed[plan.plan_id] = self.assignments[plan.plan_id]
        if plan.kind == "diagnostic":
            self._needs_rescore[plan.sat_id] = plan.plan_id
        return {
            "approval_id": approval_id,
            "plan_id": plan.plan_id,
            "sat_id": plan.sat_id,
            "kind": plan.kind,
            "tier": ticket.tier,
            "flight_rule": self._flight_rules.get(approval_id),
            "t_done": t_done,
        }

    def _plan(self, plan_id: Any) -> Plan:
        plan = self.plans.get(plan_id) if isinstance(plan_id, str) else None
        if plan is None:
            raise ToolArgError(f"unknown plan_id {plan_id!r}")
        return plan

    def _verified(self, plan_id: Any) -> Plan:
        """A registered plan that is still safe and whose latest simulation passed, for this exact hash."""
        plan = self._plan(plan_id)
        safety.check_plan(plan, self.backend.contacts(), now=self.backend.now(), conjunctions=self._conjunctions())
        sim = self.sims.get(plan.plan_id)
        if sim is None:
            raise SafetyError("not_verified", f"{plan.plan_id} has not been simulated; call simulate first")
        if sim.plan_hash != safety.plan_hash(plan):
            raise SafetyError("stale_hash", f"{plan.plan_id} changed since it was simulated; simulate it again")
        if not sim.passes_all:
            raise SafetyError(
                "failed_verification",
                f"{plan.plan_id} failed verification ({sim.sim_id}) under {', '.join(safety.failing_hypotheses(sim))}; "
                "it can never be uplinked or approved",
            )
        return plan

    def _priority(self, sat_id: str) -> float:
        """Scheduler weight (higher = more important, backend/sched) from the triage rank (1 = most urgent)."""
        rank = next((e.priority for e in self.backend.triage() if e.sat_id == sat_id), None)
        if rank is None:
            return 0.01  # not in the triage queue: lowest weight
        return 1.0 / rank if rank >= 1.0 else 1.0


# ---------------------------------------------------------------------- arg parsing
def _overlap(a: Assignment, b: Assignment) -> bool:
    return a.station_id == b.station_id and a.start_t < b.end_t and b.start_t < a.end_t


def _uplink_deadline(plan: Plan) -> float:
    """A diagnostic must be uplinked before its first step, a maneuver before its burn; a plan any time inside its
    contact (commands at AOS run as soon as they are uplinked)."""
    if plan.kind == "diagnostic":
        return min(plan.uplink.los, min(c.t_exec for c in plan.commands))
    if plan.kind == "maneuver":
        return min(plan.uplink.los, next(c.t_exec for c in plan.commands if c.name == "CAM_BURN"))
    return plan.uplink.los


def _infeasible_hint(sat_id: str, n_failed: int) -> str:
    return (f"{n_failed} recovery plans for {sat_id} failed verification: stop searching. The situation is "
            f"infeasible: propose SAFE_MODE for {sat_id} if you have not, simulate, schedule it, request approval, "
            "and say it is infeasible and that you recommend SAFE_MODE.")


def _rescore_message(sat_id: str, plan_id: str) -> str:
    return (f"diagnostic {plan_id} has run on {sat_id}; call score_hypotheses for {sat_id} first, "
            "so the plan uses the new evidence")


def _sat_id(value: Any) -> str:
    if value not in SAT_IDS:
        raise ToolArgError(f"unknown sat_id {value!r}; use SAT-01..SAT-24")
    return value


def _finite(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolArgError(f"{name} must be a finite number")
    return float(value)


def _hypotheses(value: Any) -> str | list[str]:
    if value is None or value == "all":
        return "all"
    if not isinstance(value, list) or not value:
        raise ToolArgError('hypotheses must be "all" or a non-empty list of H0..H5')
    bad = [h for h in value if h not in HYPOTHESES]
    if bad:
        raise ToolArgError(f"unknown hypotheses {bad}; use H0..H5")
    return list(dict.fromkeys(value))


def _monte_carlo(value: Any) -> int:
    # Function-calling APIs often send integers as floats (100.0).
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or value != int(value)):
        raise ToolArgError("monte_carlo must be an integer")
    n = int(value)
    if not 1 <= n <= MAX_MONTE_CARLO:
        raise ToolArgError(f"monte_carlo must be between 1 and {MAX_MONTE_CARLO}")
    return n


# ---------------------------------------------------------------------- JSON
def jsonable(obj: Any) -> Any:
    """Plain-JSON copy: pydantic models dumped, tuples to lists, numpy to Python, NaN/inf to None."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="python")
    if isinstance(obj, Mapping):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return obj
    if hasattr(obj, "tolist"):  # numpy scalar or array
        return jsonable(obj.tolist())
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj
