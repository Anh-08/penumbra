"""Scripted stub model: Detect -> Diagnose (with a diagnostic test when needed) -> Plan -> Verify -> Schedule ->
Approve, with no LLM.

It decides each turn only from the tool results already in the history, so it is deterministic and works
against any Backend. Used for offline runs, tests and the mock API; Gemini (T17) is the live model.

Diagnostic test (T20): if more than one hypothesis is plausible, the stub runs one diagnostic in the current pass
before planning: switch the suspect AUX load off (a stuck load does not drop), then cycle the payload. It is
simulated, scheduled and sent for approval like any plan. If a flight rule covers it (Tier 1) it runs at once and
the stub scores the hypotheses again; plans are built only on those new scores.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .model import Message, ModelResponse, ToolCall

# Candidate plans for a draining satellite: shed the suspect load, or shed it and cut payload duty.
CANDIDATES: list[list[dict[str, Any]]] = [
    [{"name": "LOAD_OFF", "args": {"load": "AUX"}}],
    [{"name": "SET_PAYLOAD_DUTY", "args": {"duty": 0.2}}, {"name": "LOAD_OFF", "args": {"load": "AUX"}}],
]

# Diagnostic steps, no t_exec: the dispatcher spaces them one dwell apart inside the pass.
DIAGNOSTIC: list[dict[str, Any]] = [
    {"name": "LOAD_OFF", "args": {"load": "AUX"}},
    {"name": "LOAD_OFF", "args": {"load": "PAYLOAD"}},
    {"name": "LOAD_ON", "args": {"load": "PAYLOAD"}},
]


class StubModel:
    def generate(
        self, system: str, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]
    ) -> ModelResponse:
        log = _results(messages)
        turn = 1 + sum(1 for m in messages if m.role == "model")

        def calls(*specs: tuple[str, dict[str, Any]]) -> ModelResponse:
            return ModelResponse(
                tool_calls=tuple(ToolCall(name=n, args=a, id=f"call-{turn}-{i}") for i, (n, a) in enumerate(specs, 1))
            )

        def of(name: str, plan_id: str | None = None) -> list[dict[str, Any]]:
            return [r for n, r in log if n == name and (plan_id is None or r.get("plan_id") == plan_id)]

        # Detect
        if not of("get_fleet_status"):
            return calls(("get_fleet_status", {}))
        triage = of("get_fleet_status")[-1].get("triage") or []
        if not triage:
            return ModelResponse(text="No satellite needs attention. The fleet is nominal.")
        sat = triage[0]["sat_id"]
        if not of("get_telemetry"):
            return calls(("get_telemetry", {"sat_id": sat}))

        # Diagnose
        if not of("score_hypotheses"):
            return calls(("score_hypotheses", {"sat_id": sat}))
        scores = of("score_hypotheses")[-1]
        if "error" in scores:
            return ModelResponse(text=f"Diagnosis for {sat} failed. Handing over to the operator.")

        # Diagnostic test, at most one, only while more than one hypothesis is plausible
        diag_note = ""
        proposed = of("propose_diagnostic_test")
        if not proposed and len(scores.get("plausible") or []) > 1:
            return calls(("propose_diagnostic_test", {"sat_id": sat, "commands": DIAGNOSTIC}))
        diag = proposed[-1].get("plan_id") if proposed else None
        if diag:
            sims = of("simulate", diag)
            if not sims:
                return calls(("simulate", {"plan_id": diag, "hypotheses": "all"}))
            if sims[-1].get("passes_all"):
                if not of("schedule_uplink", diag):
                    return calls(("schedule_uplink", {"plan_id": diag}))
                tickets = of("request_approval", diag)
                if not tickets:
                    return calls(("request_approval", {"plan_id": diag}))
                ticket = tickets[-1]
                if ticket.get("status") == "awaiting_approval":
                    return ModelResponse(
                        text=f"{sat} needs a diagnostic test before a recovery plan. Diagnostic {diag} passes "
                        f"verification and waits for the Flight Director as approval {ticket['approval_id']}."
                    )
                if ticket.get("status") == "auto_executed":
                    if len(of("score_hypotheses")) < 2:
                        return calls(("score_hypotheses", {"sat_id": sat}))
                    rule = ticket.get("flight_rule")
                    diag_note = f" Diagnostic {diag} ran under {rule} and the new telemetry confirms it."

        # Plan
        plan_results = of("propose_plan")
        if not plan_results:
            return calls(*[("propose_plan", {"sat_id": sat, "commands": c}) for c in CANDIDATES])
        plan_ids = [r["plan_id"] for r in plan_results if "plan_id" in r]
        if not plan_ids:
            return ModelResponse(text=f"No valid plan could be proposed for {sat}. Recommend SAFE_MODE.")

        # Verify
        sims = [s for p in plan_ids for s in of("simulate", p)[-1:]]
        if len(sims) < len(plan_ids):
            return calls(*[("simulate", {"plan_id": p, "hypotheses": "all"}) for p in plan_ids if not of("simulate", p)])
        passing = [s for s in sims if s.get("passes_all")]
        failing = [s["plan_id"] for s in sims if not s.get("passes_all")]
        if not passing:
            return ModelResponse(
                text=f"No candidate plan for {sat} passes verification. The situation is infeasible; "
                "recommend SAFE_MODE."
            )
        best = passing[0]  # first passing candidate in proposal order; the verifier made the call

        # Schedule
        if not of("schedule_uplink", best["plan_id"]):
            return calls(("schedule_uplink", {"plan_id": best["plan_id"]}))
        # Approve
        tickets = of("request_approval", best["plan_id"])
        if not tickets:
            return calls(("request_approval", {"plan_id": best["plan_id"]}))
        ticket = tickets[-1]
        if "error" in ticket:
            return ModelResponse(text=f"Approval for {best['plan_id']} was refused: verification must pass first.")

        top = max(scores["scores"], key=scores["scores"].get)
        label = scores.get("labels", {}).get(top, top)
        rejected = f" Rejected by verification: {', '.join(failing)}." if failing else ""
        return ModelResponse(
            text=(
                f"{sat} is losing charge. Most likely cause: {top} ({label}).{diag_note}{rejected} "
                f"{best['plan_id']} passes every plausible hypothesis with worst SOC margin "
                f"{{{best['sim_id']}.worst_margin_soc}}. The uplink is booked and approval {ticket['approval_id']} "
                "is waiting for the Flight Director."
            )
        )


def _results(messages: Sequence[Message]) -> list[tuple[str, dict[str, Any]]]:
    """Every tool result so far as (tool name, result), oldest first."""
    return [(r.name, r.result) for m in messages for r in m.tool_results]
