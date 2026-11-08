"""Function-calling schemas for the 8 agent tools (CONTRACTS.md "Agent tools": exact names and parameters).

Plain JSON Schema, restricted to keywords Gemini accepts (type, description, properties, required, enum, items,
minimum, maximum, anyOf). The dispatcher re-validates everything, so the schema is guidance for the model,
not the safety check.
"""
from __future__ import annotations

from typing import Any

from contracts import (
    ALL_COMMAND_NAMES,
    CAM_WINDOW_IDS,
    HEATER_SETPOINT_RANGE_C,
    HYPOTHESES,
    LOADS,
    PAYLOAD_DUTY_RANGE,
    SAT_IDS,
)

DEFAULT_MONTE_CARLO = 100
MAX_MONTE_CARLO = 1000

_SAT_ID = {"type": "string", "enum": list(SAT_IDS), "description": "Satellite id."}
_PLAN_ID = {
    "type": "string",
    "description": "Plan id (P-<n>) returned by propose_plan or propose_diagnostic_test.",
}
_TIME = {"type": "number", "description": "POSIX seconds, UTC."}

_COMMAND = {
    "type": "object",
    "description": "One allowlisted command.",
    "properties": {
        "name": {"type": "string", "enum": list(ALL_COMMAND_NAMES)},
        "args": {
            "type": "object",
            "description": (
                "LOAD_OFF/LOAD_ON: {load}. SET_PAYLOAD_DUTY: {duty}. SET_HEATER_SETPOINT: {setpoint_c}. "
                "SAFE_MODE: no args. CAM_BURN: {window_id} (a collision-avoidance burn in a window from "
                "get_conjunctions; only in propose_plan)."
            ),
            "properties": {
                "load": {
                    "type": "string",
                    "enum": list(LOADS),
                    "description": "BUS, COMMS and ADCS are essential and can never be switched off.",
                },
                "duty": {
                    "type": "number",
                    "minimum": PAYLOAD_DUTY_RANGE[0],
                    "maximum": PAYLOAD_DUTY_RANGE[1],
                    "description": "Payload duty cycle as a fraction.",
                },
                "setpoint_c": {
                    "type": "number",
                    "minimum": HEATER_SETPOINT_RANGE_C[0],
                    "maximum": HEATER_SETPOINT_RANGE_C[1],
                    "description": "Battery heater setpoint, degrees C.",
                },
                "window_id": {
                    "type": "string",
                    "enum": list(CAM_WINDOW_IDS),
                    "description": "CAM_BURN only: the maneuver window to burn in. You never give a delta-v; "
                                   "the window carries it.",
                },
            },
        },
        "t_exec": {
            "type": "number",
            "description": (
                "Execution time, POSIX seconds UTC. Optional: omit it to execute at the uplink AOS "
                "(a CAM_BURN at the start of its window). Set it later to time-tag a command (for example "
                "during eclipse); a CAM_BURN must stay inside its window."
            ),
        },
    },
    "required": ["name"],
}

_COMMANDS = {"type": "array", "items": _COMMAND, "description": "Commands, in execution order."}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool(
        "get_fleet_status",
        "Fleet triage queue (most urgent first) and current alerts.",
        {},
        [],
    ),
    _tool(
        "get_telemetry",
        "Downlinked telemetry for one satellite (samples only exist inside ground contacts). "
        "Default window: the last few hours up to now.",
        {"sat_id": _SAT_ID, "t_from": _TIME, "t_to": _TIME},
        ["sat_id"],
    ),
    _tool(
        "score_hypotheses",
        "Posterior probability of each fault hypothesis (H0 nominal, H1..H5 fault kinds) for one satellite, "
        "the plausible set, and human labels.",
        {"sat_id": _SAT_ID},
        ["sat_id"],
    ),
    _tool(
        "propose_diagnostic_test",
        "Register a short diagnostic test for the current or next pass, for example LOAD_OFF of a suspect load "
        "then cycling the payload. Give the steps in order without t_exec: they run one dwell apart and the test "
        "must end inside the pass. Returns {plan_id} or {error}. Simulate, schedule and request_approval it like "
        "a plan; after it has run, call score_hypotheses again before proposing a plan.",
        {"sat_id": _SAT_ID, "commands": _COMMANDS},
        ["sat_id", "commands"],
    ),
    _tool(
        "propose_plan",
        "Register a candidate recovery plan for the next contact. Returns {plan_id} or {error}. "
        "Propose several alternatives and let simulate judge them. A plan with one CAM_BURN (plus optional "
        "power commands) is a collision-avoidance maneuver: one plan per window you want judged.",
        {"sat_id": _SAT_ID, "commands": _COMMANDS},
        ["sat_id", "commands"],
    ),
    _tool(
        "simulate",
        "Physics verification of a plan across fault hypotheses with Monte Carlo sampling. Returns a SimResult "
        "(passes_all, worst_margin_soc, per-hypothesis stats). Only the verifier decides feasibility.",
        {
            "plan_id": _PLAN_ID,
            "hypotheses": {
                "description": 'Hypotheses to test: "all" (default) or a list such as ["H0", "H2"].',
                "anyOf": [
                    {"type": "string", "enum": ["all"]},
                    {"type": "array", "items": {"type": "string", "enum": list(HYPOTHESES)}},
                ],
            },
            "monte_carlo": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_MONTE_CARLO,
                "description": f"Monte Carlo samples per hypothesis (default {DEFAULT_MONTE_CARLO}).",
            },
        },
        ["plan_id"],
    ),
    _tool(
        "schedule_uplink",
        "Book ground-station time to uplink a verified plan. Returns the station and window, or scheduled=false.",
        {"plan_id": _PLAN_ID},
        ["plan_id"],
    ),
    _tool(
        "request_approval",
        "Ask for execution of a verified plan. Returns {approval_id, plan_id, tier, status}: tier 1 runs at once "
        "under a flight rule (status auto_executed, with flight_rule), tier 2 waits for the human Flight Director "
        "(status awaiting_approval). A plan that failed verification is refused.",
        {"plan_id": _PLAN_ID},
        ["plan_id"],
    ),
    # T31 (contracts.AGENT_TOOLS_CAM)
    _tool(
        "get_conjunctions",
        "Predicted close approaches of this satellite with (simulated) debris: {conjunctions: [{conj_id, object_id, "
        "tca, miss_km, windows: [{window_id, t_start, t_end, dv_mps, burn_s, power_w, miss_after_km}]}]}. Each "
        "window is a burn the backend offers; you choose one with CAM_BURN {window_id}. Early windows need less "
        "delta-v but come while the battery may still be low; late windows find it charged but need a larger "
        "burn. Only simulate decides whether the battery survives the burn.",
        {"sat_id": _SAT_ID},
        ["sat_id"],
    ),
]

_BY_NAME = {t["name"]: t for t in TOOL_SCHEMAS}


def tool_schema(name: str) -> dict[str, Any]:
    return _BY_NAME[name]
