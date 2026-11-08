"""Safety layer: the checks a command, plan, uplink or approval must pass. AI proposes, physics verifies,
humans approve, and nothing reaches a satellite without passing here.

The checks are written out explicitly instead of relying only on the contracts validators, so they also hold
for objects built without validation (`model_construct`) or changed after the fact. Every failure raises
`SafetyError` with a stable `reason_key` for the UI and the flight recorder.

T31 collision-avoidance burns: `CAM_BURN {"window_id": "W<n>"}` is allowlisted (contracts.ALL_COMMAND_ARGS) and only
goes in a kind "maneuver" plan. `check_maneuver` ties the burn to its conjunction (from the backend's
`conjunctions()`, never from the model): its `t_exec` must lie inside the chosen window and before
`tca - CAM_LEAD_S`. A conjunction gets at most one approved burn (the dispatcher enforces that with
`resolve_conjunction`).
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from pydantic import ValidationError

from contracts import (
    ALL_COMMAND_ARGS,
    CAM_LEAD_S,
    ESSENTIAL_LOADS,
    HEATER_SETPOINT_RANGE_C,
    LOADS,
    PAYLOAD_DUTY_RANGE,
    Assignment,
    Command,
    Conjunction,
    Contact,
    Plan,
    SimResult,
    plan_hash,
)

__all__ = [
    "SafetyError",
    "parse_command",
    "check_command",
    "check_plan",
    "check_maneuver",
    "resolve_conjunction",
    "check_uplink",
    "check_assignment",
    "failing_hypotheses",
    "plan_hash",
]

COMMAND_FIELDS = frozenset({"name", "args", "t_exec"})


class SafetyError(ValueError):
    """reason_key values: not_allowlisted, essential_load, bad_args, bad_time, uplink_outside_contact,
    uplink_closed, cam_no_conjunction, cam_outside_window, cam_too_late, cam_duplicate (plus the approval reasons
    used by Dispatcher.approve)."""

    def __init__(self, reason_key: str, message: str) -> None:
        super().__init__(message)
        self.reason_key = reason_key

    def __str__(self) -> str:
        return f"{self.reason_key}: {self.args[0]}"


# ---------------------------------------------------------------- commands
def parse_command(raw: Any, default_t_exec: float) -> Command:
    """Turn one model-supplied command into a checked Command. t_exec defaults to the uplink AOS."""
    if not isinstance(raw, Mapping):
        raise SafetyError("bad_args", "each command must be an object with name, args and optional t_exec")
    name = raw.get("name")
    if not isinstance(name, str) or name not in ALL_COMMAND_ARGS:
        raise SafetyError("not_allowlisted", f"{name!r} is not on the allowlist ({', '.join(ALL_COMMAND_ARGS)})")
    extra = set(raw) - COMMAND_FIELDS
    if extra:
        raise SafetyError("bad_args", f"{name}: unknown field(s) {sorted(extra)}")
    args = raw.get("args")
    args = {} if args is None else args
    if not isinstance(args, Mapping):
        raise SafetyError("bad_args", f"{name}: args must be an object")
    if name == "LOAD_OFF" and args.get("load") in ESSENTIAL_LOADS:
        raise SafetyError("essential_load", f"LOAD_OFF on essential load {args['load']} is never allowed")
    t_exec = raw.get("t_exec")
    t_exec = default_t_exec if t_exec is None else _finite_time(t_exec)
    try:
        cmd = Command(t_exec=t_exec, name=name, args=dict(args))
    except ValidationError as exc:
        raise SafetyError("bad_args", "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors()))
    check_command(cmd)
    return cmd


def check_command(cmd: Command) -> None:
    name, args = cmd.name, dict(cmd.args or {})
    if name not in ALL_COMMAND_ARGS:
        raise SafetyError("not_allowlisted", f"{name!r} is not on the allowlist")
    if set(args) != set(ALL_COMMAND_ARGS[name]):
        raise SafetyError("bad_args", f"{name} takes args {sorted(ALL_COMMAND_ARGS[name])}, got {sorted(args)}")
    if name == "CAM_BURN" and args["window_id"] not in ("W1", "W2", "W3"):
        raise SafetyError("bad_args", f"CAM_BURN window_id must be W1..W3, got {args['window_id']!r}")
    if name in ("LOAD_OFF", "LOAD_ON") and args["load"] not in LOADS:
        raise SafetyError("bad_args", f"unknown load {args['load']!r}")
    if name == "LOAD_OFF" and args["load"] in ESSENTIAL_LOADS:
        raise SafetyError("essential_load", f"LOAD_OFF on essential load {args['load']} is never allowed")
    if name == "SET_PAYLOAD_DUTY":
        _in_range("duty", args["duty"], PAYLOAD_DUTY_RANGE)
    if name == "SET_HEATER_SETPOINT":
        _in_range("setpoint_c", args["setpoint_c"], HEATER_SETPOINT_RANGE_C)
    _finite_time(cmd.t_exec)


# ---------------------------------------------------------------- plans and uplinks
def check_plan(plan: Plan, contacts: Iterable[Contact], now: float | None = None,
               conjunctions: Iterable[Conjunction] | None = None) -> None:
    """Commands allowlisted and safe, t_exec not before the uplink, uplink inside a real contact window of
    this satellite (and still open if `now` is given). A maneuver plan is also checked against its conjunction."""
    if not plan.commands:
        raise SafetyError("bad_args", f"{plan.plan_id} has no commands")
    burns = [c for c in plan.commands if c.name == "CAM_BURN"]
    if plan.kind == "maneuver" and len(burns) != 1:
        raise SafetyError("cam_duplicate", f"{plan.plan_id}: a maneuver plan has exactly one CAM_BURN")
    if plan.kind != "maneuver" and burns:
        raise SafetyError("bad_args", f"{plan.plan_id}: CAM_BURN only goes in a maneuver plan (propose_plan)")
    for cmd in plan.commands:
        check_command(cmd)
        if cmd.t_exec < plan.uplink.aos:
            raise SafetyError("bad_time", f"{plan.plan_id}: t_exec before the uplink AOS")
    if plan.uplink.sat_id != plan.sat_id:
        raise SafetyError("uplink_outside_contact", f"{plan.plan_id}: uplink contact belongs to another satellite")
    check_uplink(plan.uplink, contacts, now)
    if plan.kind == "maneuver":
        check_maneuver(plan, conjunctions, now)


def resolve_conjunction(sat_id: str, window_id: str, conjunctions: Iterable[Conjunction] | None,
                        now: float | None = None) -> Conjunction:
    """The satellite's next conjunction that offers this window (window ids repeat across conjunctions)."""
    if conjunctions is None:
        raise SafetyError("cam_no_conjunction", "no conjunction data from the backend; a CAM cannot be planned")
    upcoming = sorted((cj for cj in conjunctions if cj.sat_id == sat_id and (now is None or cj.tca > now)),
                      key=lambda cj: cj.tca)
    for cj in upcoming:
        if any(w.window_id == window_id for w in cj.windows):
            return cj
    raise SafetyError("cam_no_conjunction", f"{sat_id} has no upcoming conjunction with window {window_id}")


def check_maneuver(plan: Plan, conjunctions: Iterable[Conjunction] | None, now: float | None = None) -> Conjunction:
    """The burn must lie inside its window and before tca - CAM_LEAD_S. Returns the conjunction it answers."""
    burn = next(c for c in plan.commands if c.name == "CAM_BURN")
    window_id = burn.args["window_id"]
    cj = resolve_conjunction(plan.sat_id, window_id, conjunctions, now)
    window = cj.window(window_id)
    if not window.t_start <= burn.t_exec <= window.t_end:
        raise SafetyError("cam_outside_window",
                          f"{plan.plan_id}: CAM_BURN at t_exec outside window {window_id} of {cj.conj_id}")
    if burn.t_exec >= cj.tca - CAM_LEAD_S:
        raise SafetyError("cam_too_late", f"{plan.plan_id}: CAM_BURN must execute before tca - CAM_LEAD_S of {cj.conj_id}")
    if now is not None and burn.t_exec < now:
        raise SafetyError("bad_time", f"{plan.plan_id}: CAM_BURN t_exec is in the past")
    return cj


def check_uplink(uplink: Contact, contacts: Iterable[Contact], now: float | None = None) -> None:
    if not (math.isfinite(uplink.aos) and math.isfinite(uplink.los) and uplink.aos < uplink.los):
        raise SafetyError("uplink_outside_contact", "uplink window is not a valid interval")
    if not any(_inside(uplink.sat_id, uplink.station_id, uplink.aos, uplink.los, c) for c in contacts):
        raise SafetyError(
            "uplink_outside_contact",
            f"uplink {uplink.station_id} for {uplink.sat_id} is not inside any contact window",
        )
    if now is not None and now >= uplink.los:
        raise SafetyError("uplink_closed", f"the {uplink.station_id} uplink window for {uplink.sat_id} has closed")


def check_assignment(a: Assignment, sat_id: str, contacts: Iterable[Contact]) -> None:
    """A scheduler assignment must lie inside a contact window of that satellite at that station."""
    if not any(_inside(sat_id, a.station_id, a.start_t, a.end_t, c) for c in contacts):
        raise SafetyError(
            "uplink_outside_contact", f"scheduled uplink for {a.plan_id} at {a.station_id} is outside any contact"
        )


def _inside(sat_id: str, station_id: str, start: float, end: float, c: Contact) -> bool:
    return c.sat_id == sat_id and c.station_id == station_id and c.aos <= start and end <= c.los and start < end


# ---------------------------------------------------------------- verification
def failing_hypotheses(sim: SimResult) -> list[str]:
    """Plausible hypotheses under which the plan did not pass every Monte Carlo sample."""
    bad = [h for h in sim.plausible if h in sim.per_hyp and sim.per_hyp[h].pass_rate < 1.0]
    if not bad and not sim.passes_all:
        bad = list(sim.plausible)
    return bad


# ---------------------------------------------------------------- helpers
def _finite_time(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SafetyError("bad_time", "t_exec must be a finite POSIX time in seconds")
    return float(value)


def _in_range(name: str, value: Any, bounds: tuple[float, float]) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SafetyError("bad_args", f"{name} must be a finite number")
    if not bounds[0] <= value <= bounds[1]:
        raise SafetyError("bad_args", f"{name}={value} outside [{bounds[0]}, {bounds[1]}]")
