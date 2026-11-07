"""Tool dispatcher: checks tool-call args, calls the Backend interfaces, and keeps the agent's plan registry.

Bad input never raises out of `dispatch`; the model gets `{"error": ...}` back and can try again.
Every result is plain JSON (no NaN, no pydantic objects), ready for the model and the flight recorder.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import BaseModel, ValidationError

from contracts import (
    AGENT_TOOLS,
    HYPOTHESES,
    HYPOTHESIS_LABELS,
    SAT_IDS,
    ApprovalRequestMsg,
    Assignment,
    Command,
    Plan,
    ScheduleRequest,
    ScheduleResult,
    SimResult,
)

from .interfaces import Backend
from .tools import DEFAULT_MONTE_CARLO, MAX_MONTE_CARLO, TOOL_SCHEMAS

TELEMETRY_WINDOW_S = 3 * 3600.0  # default get_telemetry window, ending now
MAX_TELEMETRY_POINTS = 120  # keep tool results small for the model
UPLINK_DURATION_S = 60.0  # station time requested per plan uplink
_COMMAND_FIELDS = {"name", "args", "t_exec"}


class ToolArgError(ValueError):
    """The model sent something the tool cannot accept."""


@dataclass(frozen=True)
class ToolOutcome:
    result: dict[str, Any]
    plan_hash: str | None = None  # copied to the recorder event when the call concerns one plan


class Dispatcher:
    def __init__(self, backend: Backend, created_by: str = "agent") -> None:
        self.backend = backend
        self.created_by = created_by
        self.plans: dict[str, Plan] = {}
        self.sims: dict[str, SimResult] = {}  # latest SimResult per plan_id
        self.approvals: dict[str, ApprovalRequestMsg] = {}
        self.last_schedule: ScheduleResult | None = None
        self._schedule_requests: dict[str, ScheduleRequest] = {}
        self._schemas = {s["name"]: s["parameters"] for s in TOOL_SCHEMAS}
        self._handlers = {name: getattr(self, f"_tool_{name}") for name in AGENT_TOOLS}

    # ------------------------------------------------------------------ entry point
    def dispatch(self, name: str, args: Mapping[str, Any] | None) -> ToolOutcome:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolOutcome({"error": f"unknown tool {name!r}; tools are {', '.join(AGENT_TOOLS)}"})
        try:
            outcome = handler(**self._check_args(name, args))
        except ValidationError as exc:
            return ToolOutcome({"error": "; ".join(_short(e["msg"]) for e in exc.errors())})
        except (ValueError, LookupError) as exc:  # ToolArgError, or a backend refusing the input
            return ToolOutcome({"error": str(exc) or type(exc).__name__})
        return ToolOutcome(jsonable(outcome.result), outcome.plan_hash)

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

    # ------------------------------------------------------------------ tools
    def _tool_get_fleet_status(self) -> ToolOutcome:
        t = self.backend.now()
        return ToolOutcome({"t": t, "triage": self.backend.triage(), "alerts": self.backend.detect(t)})

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
        scores = self.backend.score_hypotheses(_sat_id(sat_id))
        return ToolOutcome(
            {
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
        if sim.plan_id != plan.plan_id or sim.plan_hash != plan.plan_hash:
            raise RuntimeError(f"verifier returned a result for another plan ({sim.plan_id})")
        self.sims[plan.plan_id] = sim
        return ToolOutcome(sim.model_dump(), sim.plan_hash)

    def _tool_schedule_uplink(self, plan_id: Any) -> ToolOutcome:
        plan = self._verified(plan_id)
        self._schedule_requests[plan.plan_id] = ScheduleRequest(
            plan_id=plan.plan_id,
            sat_id=plan.sat_id,
            priority=self._priority(plan.sat_id),
            deadline_t=plan.uplink.los,
            duration_s=UPLINK_DURATION_S,
        )
        # Re-solve with every pending request so station conflicts between satellites are resolved together.
        result = self.backend.schedule(list(self._schedule_requests.values()))
        self.last_schedule = result
        mine: Assignment | None = next((a for a in result.assignments if a.plan_id == plan.plan_id), None)
        out: dict[str, Any] = {"plan_id": plan.plan_id, "scheduled": mine is not None, "solver": result.solver}
        if mine is not None:
            out.update(station_id=mine.station_id, start_t=mine.start_t, end_t=mine.end_t)
        return ToolOutcome(out, plan.plan_hash)

    def _tool_request_approval(self, plan_id: Any) -> ToolOutcome:
        plan = self._verified(plan_id)
        sim = self.sims[plan.plan_id]
        tier, _flight_rule = self.backend.tier(plan, sim)
        approval_id = f"AP-{len(self.approvals) + 1}"
        self.approvals[approval_id] = ApprovalRequestMsg(
            approval_id=approval_id,
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            sat_id=plan.sat_id,
            tier=tier,
            sim_id=sim.sim_id,
        )
        return ToolOutcome({"approval_id": approval_id, "tier": tier}, plan.plan_hash)

    # ------------------------------------------------------------------ helpers
    def _propose(self, sat_id: Any, commands: Any, kind: str) -> ToolOutcome:
        sat_id = _sat_id(sat_id)
        if not isinstance(commands, list) or not commands:
            raise ToolArgError("commands must be a non-empty list")
        uplink = self.backend.next_contact(sat_id, self.backend.now())
        if uplink is None:
            raise ToolArgError(f"{sat_id} has no contact window left in the horizon")
        cmds = []
        for i, c in enumerate(commands):
            if not isinstance(c, Mapping):
                raise ToolArgError(f"commands[{i}] must be an object")
            extra = set(c) - _COMMAND_FIELDS
            if extra:
                raise ToolArgError(f"commands[{i}]: unknown field(s) {sorted(extra)}")
            t_exec = uplink.aos if c.get("t_exec") is None else _finite(f"commands[{i}].t_exec", c["t_exec"])
            cmds.append(Command(t_exec=t_exec, name=c.get("name"), args=c.get("args") or {}))
        plan_id = f"P-{len(self.plans) + 1}"
        plan = Plan(plan_id=plan_id, sat_id=sat_id, kind=kind, uplink=uplink, commands=cmds,
                    created_by=self.created_by)
        self.plans[plan_id] = plan
        return ToolOutcome({"plan_id": plan_id}, plan.plan_hash)

    def _plan(self, plan_id: Any) -> Plan:
        plan = self.plans.get(plan_id) if isinstance(plan_id, str) else None
        if plan is None:
            raise ToolArgError(f"unknown plan_id {plan_id!r}")
        return plan

    def _verified(self, plan_id: Any) -> Plan:
        plan = self._plan(plan_id)
        sim = self.sims.get(plan.plan_id)
        if sim is None:
            raise ToolArgError(f"{plan.plan_id} has not been simulated; call simulate first")
        if sim.plan_hash != plan.plan_hash:
            raise ToolArgError(f"{plan.plan_id} changed since it was simulated; simulate it again")
        if not sim.passes_all:
            raise ToolArgError(f"{plan.plan_id} failed verification ({sim.sim_id}); it can never be uplinked")
        return plan

    def _priority(self, sat_id: str) -> float:
        return next((e.priority for e in self.backend.triage() if e.sat_id == sat_id), 0.0)


# ---------------------------------------------------------------------- arg parsing
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


def _short(msg: str) -> str:
    return msg.removeprefix("Value error, ")


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
