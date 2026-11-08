"""Scripted stub model: Detect -> Diagnose (with a diagnostic test when needed) -> Plan -> Verify -> Schedule ->
Approve, with no LLM.

It decides each turn only from the tool calls and results already in the history, so it is deterministic and
works against any Backend. Used for offline runs, tests and the mock API; Gemini (T17) is the live model.

- Target: the satellite named in a fleet-mode goal ("Work SAT-07 only ..."), else the top of the triage queue.
- Diagnostic test (T20): if more than one hypothesis is plausible, one diagnostic in the current pass before
  planning (AUX off, then cycle the payload). If a flight rule covers it (Tier 1) it runs at once and the stub
  scores again; plans are built only on those new scores.
- Plans (T26): the candidates include SAFE_MODE. The stub picks the passing non-SAFE_MODE plan with the largest
  worst-case SOC margin reported by the verifier. If only SAFE_MODE passes, the situation is infeasible and it
  asks approval for SAFE_MODE. If the scheduler has no station for the plan, it proposes the same commands
  again (the backend moves it to the next contact), simulates and schedules that.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .model import Message, ModelResponse, ToolCall

# Candidate plans for a draining satellite: shed the suspect load; shed it and cut payload duty; SAFE_MODE.
CANDIDATES: list[list[dict[str, Any]]] = [
    [{"name": "LOAD_OFF", "args": {"load": "AUX"}}],
    [{"name": "SET_PAYLOAD_DUTY", "args": {"duty": 0.2}}, {"name": "LOAD_OFF", "args": {"load": "AUX"}}],
    [{"name": "SAFE_MODE", "args": {}}],
]

# Diagnostic steps, no t_exec: the dispatcher spaces them one dwell apart inside the pass.
DIAGNOSTIC: list[dict[str, Any]] = [
    {"name": "LOAD_OFF", "args": {"load": "AUX"}},
    {"name": "LOAD_OFF", "args": {"load": "PAYLOAD"}},
    {"name": "LOAD_ON", "args": {"load": "PAYLOAD"}},
]

MAX_RETARGETS = 2  # re-proposals after "no station free"
_GOAL_SAT = re.compile(r"\bWork (SAT-\d{2}) only\b")


class StubModel:
    def generate(
        self, system: str, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]
    ) -> ModelResponse:
        log = _log(messages)
        turn = 1 + sum(1 for m in messages if m.role == "model")

        def calls(*specs: tuple[str, dict[str, Any]]) -> ModelResponse:
            return ModelResponse(
                tool_calls=tuple(ToolCall(name=n, args=a, id=f"call-{turn}-{i}") for i, (n, a) in enumerate(specs, 1))
            )

        def say(text: str) -> ModelResponse:
            return ModelResponse(text=text)

        def of(name: str, plan_id: str | None = None) -> list[dict[str, Any]]:
            return [r for n, a, r in log if n == name
                    and (plan_id is None or r.get("plan_id", a.get("plan_id")) == plan_id)]

        # Detect
        if not of("get_fleet_status"):
            return calls(("get_fleet_status", {}))
        triage = of("get_fleet_status")[-1].get("triage") or []
        goal = next((m.text for m in messages if m.role == "user" and m.text), "") or ""
        named = _GOAL_SAT.search(goal)
        sat = named.group(1) if named else (triage[0]["sat_id"] if triage else None)
        if sat is None:
            return say("No satellite needs attention. The fleet is nominal.")
        if not of("score_hypotheses"):
            return calls(("get_telemetry", {"sat_id": sat}), ("score_hypotheses", {"sat_id": sat}))

        # Diagnose
        scores = of("score_hypotheses")[-1]
        if "error" in scores:
            return say(f"Diagnosis for {sat} failed. Handing over to the operator.")

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
            slots = of("schedule_uplink", diag)
            if sims[-1].get("passes_all") and not slots:
                return calls(("schedule_uplink", {"plan_id": diag}))
            if sims[-1].get("passes_all") and slots[-1].get("scheduled"):
                tickets = of("request_approval", diag)
                if not tickets:
                    return calls(("request_approval", {"plan_id": diag}))
                ticket = tickets[-1]
                if ticket.get("status") == "awaiting_approval":
                    return say(
                        f"{sat} needs a diagnostic test before a recovery plan. Diagnostic {diag} passes "
                        f"verification and waits for the Flight Director as approval {ticket['approval_id']}."
                    )
                if ticket.get("status") == "auto_executed":
                    if len(of("score_hypotheses")) < 2:
                        return calls(("score_hypotheses", {"sat_id": sat}))
                    diag_note = (f" Diagnostic {diag} ran under {ticket.get('flight_rule')} and the new "
                                 "telemetry confirms it.")
            scores = of("score_hypotheses")[-1]

        # Plan: one batch of candidates
        proposals = [(a, r) for n, a, r in log if n == "propose_plan"]
        if not proposals:
            return calls(*[("propose_plan", {"sat_id": sat, "commands": c}) for c in CANDIDATES])
        batch = proposals[: len(CANDIDATES)]
        commands_of = {r["plan_id"]: a.get("commands") for a, r in proposals if "plan_id" in r}
        plan_ids = [r["plan_id"] for _, r in batch if "plan_id" in r]
        if not plan_ids:
            return say(f"No valid plan could be proposed for {sat}. Recommend SAFE_MODE.")

        # Verify
        missing = [p for p in plan_ids if not of("simulate", p)]
        if missing:
            return calls(*[("simulate", {"plan_id": p, "hypotheses": "all"}) for p in missing])
        sims = [of("simulate", p)[-1] for p in plan_ids]
        passing = [s for s in sims if s.get("passes_all") and not _is_safe(commands_of[s["plan_id"]])]
        safe_ok = [s for s in sims if s.get("passes_all") and _is_safe(commands_of[s["plan_id"]])]
        failing = [s["plan_id"] for s in sims if not s.get("passes_all")]
        if passing:  # the verifier's numbers decide: largest worst-case margin, then uptime, then fewer commands
            best = max(passing, key=lambda s: (s["worst_margin_soc"], s["payload_uptime_worst"], -s["n_commands"]))
            infeasible = False
        elif safe_ok:
            best, infeasible = safe_ok[0], True
        else:
            return say(f"No plan for {sat} passes verification, not even SAFE_MODE. The situation is infeasible; "
                       "recommend SAFE_MODE and hand over to the operator.")

        # Schedule, moving to the next contact when no station is free
        chain = [best["plan_id"]] + [r["plan_id"] for _, r in proposals[len(CANDIDATES):] if "plan_id" in r]
        current = chain[-1]
        if not of("simulate", current):
            return calls(("simulate", {"plan_id": current, "hypotheses": "all"}))
        sim = of("simulate", current)[-1]
        if not sim.get("passes_all"):
            return say(f"{sat}: {current} no longer passes verification at the next contact. Handing over to "
                       "the operator.")
        slots = of("schedule_uplink", current)
        if not slots:
            return calls(("schedule_uplink", {"plan_id": current}))
        if not slots[-1].get("scheduled"):
            if len(chain) > MAX_RETARGETS:
                return say(f"{sat}: no ground station is free for {current} in the next contacts. Handing over "
                           "to the operator.")
            return calls(("propose_plan", {"sat_id": sat, "commands": commands_of[best["plan_id"]]}))

        # Approve
        tickets = of("request_approval", current)
        if not tickets:
            return calls(("request_approval", {"plan_id": current}))
        ticket = tickets[-1]
        if "error" in ticket:
            return say(f"Approval for {current} was refused: verification must pass first.")

        top = max(scores["scores"], key=scores["scores"].get)
        label = scores.get("labels", {}).get(top, top)
        cause = f"{sat} is losing charge. Most likely cause: {top} ({label}).{diag_note}"
        moved = (f" {chain[0]} lost its ground station to a more urgent satellite, so {current} uses the next "
                 "contact." if len(chain) > 1 else "")
        margin = f"{{{sim['sim_id']}.worst_margin_soc}}"
        if infeasible:
            return say(f"{cause} No recovery plan passes verification: the situation is infeasible. Recommend "
                       f"SAFE_MODE: {current} is booked and approval {ticket['approval_id']} waits for the Flight "
                       "Director.")
        rejected = f" Rejected by verification: {', '.join(failing)}." if failing else ""
        passes = f"{current} passes every plausible hypothesis with worst SOC margin {margin}."
        if ticket.get("status") == "auto_executed":
            return say(f"{cause}{rejected}{moved} {passes} It ran at once under flight rule "
                       f"{ticket.get('flight_rule')}.")
        return say(f"{cause}{rejected}{moved} {passes} The uplink is booked and approval {ticket['approval_id']} "
                   "is waiting for the Flight Director.")


def _log(messages: Sequence[Message]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Every tool call so far as (name, args, result), oldest first."""
    args = {tc.id: tc.args for m in messages if m.role == "model" for tc in m.tool_calls}
    return [(r.name, args.get(r.id, {}), r.result) for m in messages for r in m.tool_results]


def _is_safe(commands: Any) -> bool:
    return any(isinstance(c, Mapping) and c.get("name") == "SAFE_MODE" for c in commands or [])
