"""Fleet mode (T26): work the triage queue one satellite at a time, in triage order.

`run_fleet` reads the queue through `get_fleet_status` (recorded like any tool call, so the trace shows the
order), then runs the agent loop once per queued satellite with a goal that names it. All runs share one
dispatcher (plans, approvals and pending uplinks carry over, so station conflicts are solved across satellites)
and one recorder (one flight record, which replays with ReplayModel because the calls happen in the same order).

Each satellite gets its own step cap. A satellite whose uplink slot was taken by a later, more urgent plan
(`dispatcher.bumped`) gets one more run to move to another contact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .dispatcher import Dispatcher
from .interfaces import NullRecorder, Recorder
from .loop import MAX_STEPS, AgentRun, run_agent
from .model import Model, ToolCall

DEFAULT_MAX_SATS = 3
WORKABLE = ("queued", "working")  # triage statuses that still need the agent


@dataclass
class FleetRun:
    status: Literal["done", "step_cap"]
    runs: list[tuple[str, AgentRun]] = field(default_factory=list)  # (sat_id, run), in the order worked
    queue: list[str] = field(default_factory=list)  # triage order seen at the start

    @property
    def steps(self) -> int:
        return sum(r.steps for _, r in self.runs)

    @property
    def final_text(self) -> str | None:
        texts = [r.final_text for _, r in self.runs if r.final_text]
        return " ".join(texts) if texts else None

    @property
    def rendered_text(self) -> str | None:
        texts = [r.rendered_text for _, r in self.runs if r.rendered_text]
        return " ".join(texts) if texts else None

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [tc for _, r in self.runs for tc in r.tool_calls]


def sat_goal(sat_id: str, severity: str, position: int, total: int, retry: bool = False) -> str:
    why = (f"Its uplink slot went to a more urgent plan: propose again for the next contact, simulate, schedule "
           f"and request approval for {sat_id}." if retry else
           f"Diagnose, plan, verify, schedule the uplink and request approval for {sat_id}.")
    return (f"Fleet mode. Work {sat_id} only ({severity}, position {position} of {total} in the triage queue). "
            f"{why} The other satellites are handled in their own turn.")


def run_fleet(
    model: Model,
    dispatcher: Dispatcher,
    recorder: Recorder | None = None,
    *,
    max_sats: int = DEFAULT_MAX_SATS,
    max_steps_per_sat: int = MAX_STEPS,
) -> FleetRun:
    if getattr(model, "live", False) and (recorder is None or isinstance(recorder, NullRecorder)):
        raise ValueError("a live model run must be recorded: pass a flight recorder (see session.record_run)")
    rec = recorder if recorder is not None else NullRecorder()

    status = _fleet_status(dispatcher, rec)
    entries = [e for e in status.get("triage", []) if e.get("status") in WORKABLE][:max_sats]
    queue = [e["sat_id"] for e in entries]
    out = FleetRun(status="done", queue=queue)

    for i, entry in enumerate(entries, 1):
        run = run_agent(model, dispatcher, rec, goal=sat_goal(entry["sat_id"], entry["severity"], i, len(entries)),
                        max_steps=max_steps_per_sat)
        out.runs.append((entry["sat_id"], run))

    retried: set[str] = set()
    for plan_id in sorted(dispatcher.bumped):
        sat = dispatcher.plans[plan_id].sat_id
        if sat in retried or sat not in queue or _decided_elsewhere(dispatcher, sat, plan_id):
            continue
        retried.add(sat)
        severity = next(e["severity"] for e in entries if e["sat_id"] == sat)
        run = run_agent(model, dispatcher, rec, goal=sat_goal(sat, severity, queue.index(sat) + 1, len(queue), True),
                        max_steps=max_steps_per_sat)
        out.runs.append((sat, run))

    if any(r.status == "step_cap" for _, r in out.runs):
        out.status = "step_cap"
    return out


def _fleet_status(dispatcher: Dispatcher, rec: Recorder) -> dict:
    """get_fleet_status by the orchestrator, recorded as a tool call so the trace shows the queue."""
    call_id = "fleet-1"
    rec.record("tool_call", {"id": call_id, "name": "get_fleet_status", "args": {}})
    outcome = dispatcher.dispatch("get_fleet_status", {})
    rec.record("tool_result", {"id": call_id, "name": "get_fleet_status", "result": outcome.result})
    return outcome.result


def _decided_elsewhere(dispatcher: Dispatcher, sat_id: str, bumped_plan: str) -> bool:
    """True if a later plan for this satellite already holds a slot (nothing left to fix)."""
    return any(p != bumped_plan and dispatcher.plans[p].sat_id == sat_id for p in dispatcher.assignments)
