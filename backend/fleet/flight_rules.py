"""Flight rules: pre-approved low-risk command classes (CONTRACTS.md, Tier 1).

A plan is Tier 1 when it passed verification and one rule covers every command in it; it then
auto-executes and the trace shows the rule id. Everything else is Tier 2 and waits for the Flight
Director. SAFE_MODE, LOAD_ON, heater changes and duty increases are never Tier 1, and neither is a plan
that commands a load the scorer suspects is stuck (H2 plausible): commanding a suspected-faulty unit
is not routine. Diagnostics (FR-1) are exempt: testing that unit is their job.

- FR-1 verified diagnostic: kind "diagnostic", uplinked in the pass in progress, every step inside that
  pass, commands on non-essential loads only, no state held longer than one dwell.
- FR-2 payload duty reduction: kind "plan", only SET_PAYLOAD_DUTY to a lower duty than commanded now,
  worst_margin_soc >= TIER1_MARGIN.
- FR-3 non-essential load shed: kind "plan", only LOAD_OFF(AUX) and payload duty reductions,
  worst_margin_soc >= TIER1_MARGIN.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from contracts import NON_ESSENTIAL_LOADS, Plan, SimResult
from backend.verify import constants as K


@dataclass(frozen=True)
class RuleContext:
    now: float
    commanded_duty: float      # payload duty commanded on this satellite right now
    suspect_loads: frozenset = frozenset()   # loads a plausible H2 says may be stuck on


@dataclass(frozen=True)
class FlightRule:
    rule_id: str
    title: str
    check: Callable[[Plan, SimResult, RuleContext], bool]


def _verified(sim: SimResult, margin: float) -> bool:
    return sim.passes_all and sim.worst_margin_soc >= margin


def _diagnostic(plan: Plan, sim: SimResult, ctx: RuleContext) -> bool:
    if plan.kind != "diagnostic" or not plan.commands or not _verified(sim, K.TIER1_MARGIN_DIAGNOSTIC):
        return False
    up = plan.uplink
    if not up.aos <= ctx.now <= up.los:
        return False                                   # only inside the pass in progress
    for c in plan.commands:
        if c.name not in ("LOAD_OFF", "LOAD_ON", "SET_PAYLOAD_DUTY"):
            return False
        if c.name != "SET_PAYLOAD_DUTY" and c.args["load"] not in NON_ESSENTIAL_LOADS:
            return False
        if not up.aos <= c.t_exec <= up.los:
            return False
    times = sorted(c.t_exec for c in plan.commands)
    dwell = K.DIAGNOSTIC_DWELL_S + 1e-6
    if any(b - a > dwell for a, b in zip(times, times[1:])):
        return False                                   # no state held longer than one dwell
    return times[-1] + K.DIAGNOSTIC_DWELL_S <= up.los + 1e-6


def _duty_cut(c, ctx: RuleContext) -> bool:
    return (c.name == "SET_PAYLOAD_DUTY" and float(c.args["duty"]) < ctx.commanded_duty
            and "PAYLOAD" not in ctx.suspect_loads)


def _duty_reduction(plan: Plan, sim: SimResult, ctx: RuleContext) -> bool:
    return (plan.kind == "plan" and bool(plan.commands) and _verified(sim, K.TIER1_MARGIN)
            and all(_duty_cut(c, ctx) for c in plan.commands))


def _load_shed(plan: Plan, sim: SimResult, ctx: RuleContext) -> bool:
    return (plan.kind == "plan" and bool(plan.commands) and _verified(sim, K.TIER1_MARGIN)
            and all(_duty_cut(c, ctx) or (c.name == "LOAD_OFF" and c.args["load"] == "AUX"
                                          and "AUX" not in ctx.suspect_loads) for c in plan.commands))


FLIGHT_RULES: tuple[FlightRule, ...] = (
    FlightRule("FR-1", "Verified diagnostic inside the current pass", _diagnostic),
    FlightRule("FR-2", "Payload duty reduction", _duty_reduction),
    FlightRule("FR-3", "Non-essential load shed (AUX off, payload duty down)", _load_shed),
)


def tier(plan: Plan, sim: SimResult, ctx: RuleContext) -> tuple[int, str | None]:
    """(1, "FR-n") when a flight rule covers the verified plan, else (2, None)."""
    if sim.plan_hash != plan.plan_hash:
        return 2, None                                 # stale result: the Flight Director decides
    if plan.kind == "maneuver" or any(c.name == "CAM_BURN" for c in plan.commands):
        return 2, None                                 # T31: a collision-avoidance burn is never Tier 1
    for rule in FLIGHT_RULES:
        if rule.check(plan, sim, ctx):
            return 1, rule.rule_id
    return 2, None
