"""Scripted stub model: Detect -> Diagnose -> Plan -> Verify -> Schedule -> Approve, with no LLM.

It decides each turn only from the tool results already in the history, so it is deterministic and works
against any Backend. Used for offline runs, tests and the mock API until Gemini (T17) is wired in.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .model import Message, ModelResponse, ToolCall

# Candidate plans for a draining satellite: shed the suspect load, or shed it and cut payload duty.
CANDIDATES: list[list[dict[str, Any]]] = [
    [{"name": "LOAD_OFF", "args": {"load": "AUX"}}],
    [{"name": "SET_PAYLOAD_DUTY", "args": {"duty": 0.2}}, {"name": "LOAD_OFF", "args": {"load": "AUX"}}],
]


class StubModel:
    def generate(
        self, system: str, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]
    ) -> ModelResponse:
        done = _results(messages)
        turn = 1 + sum(1 for m in messages if m.role == "model")

        def calls(*specs: tuple[str, dict[str, Any]]) -> ModelResponse:
            return ModelResponse(
                tool_calls=tuple(ToolCall(name=n, args=a, id=f"call-{turn}-{i}") for i, (n, a) in enumerate(specs, 1))
            )

        # Detect
        if "get_fleet_status" not in done:
            return calls(("get_fleet_status", {}))
        triage = done["get_fleet_status"][-1].get("triage") or []
        if not triage:
            return ModelResponse(text="No satellite needs attention. The fleet is nominal.")
        sat = triage[0]["sat_id"]
        if "get_telemetry" not in done:
            return calls(("get_telemetry", {"sat_id": sat}))

        # Diagnose
        if "score_hypotheses" not in done:
            return calls(("score_hypotheses", {"sat_id": sat}))
        scores = done["score_hypotheses"][-1]
        if "error" in scores:
            return ModelResponse(text=f"Diagnosis for {sat} failed. Handing over to the operator.")

        # Plan
        if "propose_plan" not in done:
            return calls(*[("propose_plan", {"sat_id": sat, "commands": c}) for c in CANDIDATES])
        plan_ids = [r["plan_id"] for r in done["propose_plan"] if "plan_id" in r]
        if not plan_ids:
            return ModelResponse(text=f"No valid plan could be proposed for {sat}. Recommend SAFE_MODE.")

        # Verify
        if "simulate" not in done:
            return calls(*[("simulate", {"plan_id": p, "hypotheses": "all"}) for p in plan_ids])
        sims = [r for r in done["simulate"] if "plan_id" in r]
        passing = [s for s in sims if s["passes_all"]]
        failing = [s["plan_id"] for s in sims if not s["passes_all"]]
        if not passing:
            return ModelResponse(
                text=f"No candidate plan for {sat} passes verification. The situation is infeasible; "
                "recommend SAFE_MODE."
            )
        best = passing[0]  # first passing candidate in proposal order; the verifier made the call

        # Schedule
        if "schedule_uplink" not in done:
            return calls(("schedule_uplink", {"plan_id": best["plan_id"]}))
        # Approve
        if "request_approval" not in done:
            return calls(("request_approval", {"plan_id": best["plan_id"]}))
        ticket = done["request_approval"][-1]
        if "error" in ticket:
            return ModelResponse(text=f"Approval for {best['plan_id']} was refused: verification must pass first.")

        top = max(scores["scores"], key=scores["scores"].get)
        label = scores.get("labels", {}).get(top, top)
        rejected = f" Rejected by verification: {', '.join(failing)}." if failing else ""
        return ModelResponse(
            text=(
                f"{sat} is losing charge. Most likely cause: {top} ({label}).{rejected} "
                f"{best['plan_id']} passes every plausible hypothesis with worst SOC margin "
                f"{{{best['sim_id']}.worst_margin_soc}}. The uplink is booked and approval {ticket['approval_id']} "
                "is waiting for the Flight Director."
            )
        )


def _results(messages: Sequence[Message]) -> dict[str, list[dict[str, Any]]]:
    """Tool results so far, grouped by tool name, oldest first."""
    out: dict[str, list[dict[str, Any]]] = {}
    for m in messages:
        for r in m.tool_results:
            out.setdefault(r.name, []).append(r.result)
    return out
