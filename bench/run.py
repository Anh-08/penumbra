"""Seeded B1 safety ablation with one cached live Gemini proposal.

The oracle searches a finite, stated grid of one-command allowlisted plans.
It is an upper bound for that grid, not for every continuous command value.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import random
import statistics
import sys
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.fleet.backend import FleetBackend
from backend.fleet.scenarios import build
from backend.verify import constants as K
from contracts import Command, Plan


CACHE = Path(__file__).with_name("gemini_cache.json")
DEFAULT_OUTPUT = Path(__file__).parent / "results" / "b1.json"
SAT_ID = "SAT-17"


def load_cached_proposal(path: Path = CACHE) -> Command:
    """Load the proposal tool call captured in the live flight recorder."""
    cached = json.loads(path.read_text())
    calls = cached["response"]["tool_calls"]
    proposals = [call for call in calls if call["name"] == "propose_plan"]
    if len(proposals) != 1 or proposals[0]["args"]["sat_id"] != SAT_ID:
        raise ValueError("cache must contain one proposal for the focus satellite")
    commands = proposals[0]["args"]["commands"]
    if len(commands) != 1:
        raise ValueError("benchmark expects one cached command")
    return Command(t_exec=0.0, **commands[0])


def _candidate_commands(t_exec: float) -> list[Command]:
    commands = [
        Command(t_exec=t_exec, name=name, args={"load": load})
        for name in ("LOAD_OFF", "LOAD_ON")
        for load in ("PAYLOAD", "HEATER", "AUX")
    ]
    commands.extend(
        Command(t_exec=t_exec, name="SET_PAYLOAD_DUTY", args={"duty": duty})
        for duty in (0.0, 0.5, 1.0)
    )
    commands.extend(
        Command(t_exec=t_exec, name="SET_HEATER_SETPOINT", args={"setpoint_c": value})
        for value in (-5.0, 5.0, 20.0)
    )
    commands.append(Command(t_exec=t_exec, name="SAFE_MODE", args={}))
    return commands


def _truth(world, command: Command, end_t: float) -> dict[str, float | bool]:
    trial = copy.deepcopy(world)
    trial.execute(SAT_ID, [command])
    trial.advance_to(end_t)
    soc = trial.reveal(SAT_ID)["truth_soc"]["soc"]
    telemetry = trial.telemetry(SAT_ID)
    with np.errstate(all="ignore"):
        v_bus = float(np.nanmin(telemetry["v_bus"]))
        t_min = float(np.nanmin(telemetry["t_bat_c"]))
        t_max = float(np.nanmax(telemetry["t_bat_c"]))
        essential = min(
            float(np.nanmin(telemetry[f"load_{load}"]))
            for load in ("BUS", "COMMS", "ADCS")
        )
    margin = float(min(soc) - K.SOC_MIN)
    violated = (
        margin < 0
        or v_bus < K.V_MIN
        or t_min < K.T_MIN_C
        or t_max > K.T_MAX_C
        or essential < 1.0
    )
    return {
        "margin_soc": margin,
        "min_v_bus_observed": v_bus,
        "min_t_bat_c_observed": t_min,
        "max_t_bat_c_observed": t_max,
        "violation": bool(violated),
    }


def _summary(trials: list[dict]) -> dict:
    margins = [round(trial["margin_soc"], 6) for trial in trials]
    return {
        "n": len(trials),
        "violations": sum(trial["violation"] for trial in trials),
        "margins_soc": margins,
        "min_margin_soc": min(margins),
        "mean_margin_soc": round(statistics.mean(margins), 6),
        "selected_commands": [trial["command"] for trial in trials],
    }


def run_benchmark(
    seeds: int = 100,
    hours: float = 6.0,
    output: Path = DEFAULT_OUTPUT,
    monte_carlo: int = 8,
) -> dict:
    if seeds < 1 or hours < 6 or monte_carlo < 1:
        raise ValueError("seeds and monte_carlo must be positive; hours must be at least 6")
    cached = load_cached_proposal()
    arms: dict[str, list[dict]] = {
        name: [] for name in (
            "brute_force", "random_valid", "gemini_unverified", "gemini_verified"
        )
    }
    rejected = 0
    rng = random.Random(1900)
    for seed in range(seeds):
        scenario = build("single-stuck-load", seed=seed, sat_ids=(SAT_ID,), hours=hours)
        contact = next(
            c for c in scenario.contacts
            if c.sat_id == SAT_ID and c.aos > float(scenario.env.t[0]) + 7200
        )
        t_exec = math.ceil(contact.aos / 10) * 10.0
        if t_exec >= contact.los:
            raise ValueError("selected contact is too short for an uplink")
        backend = FleetBackend(scenario)
        backend.advance_to(t_exec)
        baseline = scenario.world
        commands = _candidate_commands(t_exec)
        measured = {command.name + json.dumps(command.args, sort_keys=True): _truth(
            baseline, command, float(scenario.env.t[-1])
        ) for command in commands}

        def record(arm: str, command: Command) -> None:
            key = command.name + json.dumps(command.args, sort_keys=True)
            arms[arm].append({**measured[key], "command": key})

        oracle = max(
            commands,
            key=lambda command: (
                not measured[command.name + json.dumps(command.args, sort_keys=True)]["violation"],
                measured[command.name + json.dumps(command.args, sort_keys=True)]["margin_soc"],
            ),
        )
        record("brute_force", oracle)
        record("random_valid", rng.choice(commands))
        proposed = Command(t_exec=t_exec, name=cached.name, args=cached.args)
        record("gemini_unverified", proposed)
        plan = Plan(
            plan_id="P-1", sat_id=SAT_ID, kind="plan", uplink=contact,
            commands=[proposed], created_by="gemini-cache",
        )
        verification = backend.simulate(plan, "all", monte_carlo)
        if verification.passes_all:
            verified_command = proposed
        else:
            rejected += 1
            verified_command = next(command for command in commands if command.name == "SAFE_MODE")
            fallback = plan.model_copy(update={"plan_id": "P-2", "commands": [verified_command]})
            if not backend.simulate(fallback, "all", monte_carlo).passes_all:
                raise RuntimeError("no verified SAFE_MODE fallback")
        record("gemini_verified", verified_command)

    result = {
        "n": seeds,
        "scenario": "single-stuck-load",
        "hours": hours,
        "monte_carlo": monte_carlo,
        "candidate_grid": {
            "plan_length": 1,
            "load": ["PAYLOAD", "HEATER", "AUX"],
            "payload_duty": [0.0, 0.5, 1.0],
            "heater_setpoint_c": [-5.0, 5.0, 20.0],
            "includes_safe_mode": True,
            "size": 13,
        },
        "inputs": {
            "real": ["one recorded Gemini proposal from runs/demo/live-single-stuck-load.jsonl"],
            "simulated": ["seeded EPS fault scenarios", "orbit/contact geometry", "telemetry and truth SOC"],
        },
        "measurement": (
            "SOC minimum uses the full truth trajectory; bus voltage, battery temperature, "
            "and essential loads use contact-only telemetry. A violation is any measured "
            "hard-constraint breach. The brute-force upper bound applies to the stated grid."
        ),
        "gemini_rejections": rejected,
        "arms": {name: _summary(trials) for name, trials in arms.items()},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument("--monte-carlo", type=int, default=8)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run_benchmark(args.seeds, args.hours, args.output, args.monte_carlo)
    print(json.dumps({"n": result["n"], "arms": {
        name: {"violations": arm["violations"], "mean_margin_soc": arm["mean_margin_soc"]}
        for name, arm in result["arms"].items()
    }}, sort_keys=True))


if __name__ == "__main__":
    main()
