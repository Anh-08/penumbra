"""T24 / B4 scale benchmark: planning-model throughput and memory, and uplink-scheduler solve time.

Planning cases run the vectorized planning model (backend/eps/planning.py) on batches of shape
(satellites, hypotheses, plans, Monte Carlo). Every case runs in its own subprocess, so its peak RAM
(ru_maxrss) belongs to that case alone. Illumination comes from the 24 real orbits (cached OMM + DE421,
backend/orbit) at the scenario epoch; fleets larger than 24 repeat those 24 illumination rows.

Scheduler cases solve uplink requests on the real 10 deg contact windows of the 24 satellites at the
3 ground stations. The contract allows only SAT-01..SAT-24, so "200" means 200 uplink requests spread
over those 24 satellites (not 200 satellites); see the results file.

    python -m bench.b4                 # all cases, writes bench/results/b4.json
    python -m bench.b4 --case planning --satellites 1000   # one case, prints JSON (used internally)
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from contracts import HYPOTHESES, SAT_IDS, Command, ScheduleRequest

DEFAULT_OUTPUT = Path(__file__).parent / "results" / "b4.json"
SEED = 24

# the settings T24 prescribes for the 10k run, used for every scale case so they compare
SCALE = {"dtype": "float32", "step_s": 60, "hours": 3.0, "monte_carlo": 20}
# what the verifier really runs for one satellite (constants.MONTE_CARLO_N, the 10 s grid, a day)
OPERATIONAL = {"dtype": "float64", "step_s": 10, "hours": 24.0, "monte_carlo": 100}
PLANS = ("no-op", "SET_PAYLOAD_DUTY 0.2", "SAFE_MODE")


def _orbit(hours: float):
    from backend.fleet.scenarios import T0, orbit_environment

    env, contacts, _ = orbit_environment(SAT_IDS, hours=hours, t0=T0)
    return env, contacts


def planning_case(satellites: int, *, step_s: int, hours: float, monte_carlo: int, dtype: str) -> dict:
    """One planning batch: satellites x 6 hypotheses x 3 plans x monte_carlo, no time history."""
    from backend.eps import planning

    dt = np.dtype(dtype).type
    env, _ = _orbit(24.0)
    stride = max(1, int(round(step_s / env.dt)))
    n = int(round(hours * 3600 / step_s))
    rows = np.arange(satellites) % len(SAT_IDS)
    illum = env.illum[rows][:, : n * stride : stride]
    t = env.t[: n * stride : stride]
    t0 = float(t[0])
    params = planning.planning_params()
    init = planning.InitialState.nominal(satellites, soc=0.75, params=params)
    cmds = [[], [Command(t_exec=t0, name="SET_PAYLOAD_DUTY", args={"duty": 0.2})], [Command(t_exec=t0, name="SAFE_MODE")]]
    clock = time.perf_counter()
    controls = planning.build_controls(t, [cmds] * satellites, init, params)
    hyps = planning.Hypotheses.build([{}] * satellites, names=HYPOTHESES, params=params)
    rng = np.random.default_rng(SEED)
    shape = (satellites, len(HYPOTHESES), len(PLANS), monte_carlo)
    perturb = planning.Perturbation(
        array_factor=(1.0 + 0.03 * rng.standard_normal(shape)).astype(dt),
        capacity_factor=(1.0 + 0.05 * rng.standard_normal(shape)).astype(dt),
        load_factor=(1.0 + 0.03 * rng.standard_normal(shape)).astype(dt),
        soc0_offset=(0.02 * rng.standard_normal(shape)).astype(dt),
        n_mc=monte_carlo,
    )
    setup_s = time.perf_counter() - clock
    res = planning.simulate_batch(t, illum, env.beta_deg[rows, 0], controls, init, hyps, perturb, params=params,
                                  soc_threshold=0.30, dtype=dt)
    total_s = time.perf_counter() - clock
    batch = int(np.prod(shape))
    return {
        "satellites": satellites, "hypotheses": len(HYPOTHESES), "plans": len(PLANS), "monte_carlo": monte_carlo,
        "batch_elements": batch, "steps": int(len(t)), "step_s": step_s, "hours": hours, "dtype": np.dtype(dt).name,
        "evals": batch * int(len(t)), "evals_per_s": round(res.evals_per_s, 1),
        "setup_s": round(setup_s, 3), "wall_s": round(total_s, 3),
        "peak_ram_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1),
        "check_min_soc_range": [round(float(res.min_soc.min()), 3), round(float(res.min_soc.max()), 3)],
    }


def scheduler_case(requests: int) -> dict:
    """`requests` uplink requests over the 24 satellites, real contacts, CP-SAT with greedy fallback."""
    from backend.fleet.scenarios import T0
    from backend.sched import UplinkScheduler

    env, contacts = _orbit(24.0)
    rng = np.random.default_rng(SEED)
    reqs = []
    for i in range(requests):
        sat = SAT_IDS[i % len(SAT_IDS)]
        reqs.append(ScheduleRequest(plan_id=f"P-{i + 1}", sat_id=sat, priority=float(rng.uniform(0.1, 1.0)),
                                    deadline_t=float(T0 + rng.uniform(2, 24) * 3600), duration_s=60.0))
    sched = UplinkScheduler(contacts)
    clock = time.perf_counter()
    res = sched.schedule(reqs, now=T0)
    wall_ms = (time.perf_counter() - clock) * 1000.0
    return {
        "requests": requests, "satellites": len(SAT_IDS), "stations": len({c.station_id for c in contacts}),
        "contacts": len(contacts), "solver": res.solver, "optimal": res.optimal,
        "solve_ms": round(res.solve_ms, 1), "wall_ms": round(wall_ms, 1),
        "scheduled": len(res.assignments), "unscheduled": len(res.unscheduled),
        "time_limit_s": sched.time_limit_s,
        "peak_ram_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1),
    }


def _subprocess(args: list[str]) -> dict:
    env = {**os.environ, "PENUMBRA_OFFLINE": "1"}
    out = subprocess.run([sys.executable, "-m", "bench.b4", *args], check=True, capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[1], env=env)
    return json.loads(out.stdout.strip().splitlines()[-1])


def run_all(satellite_counts=(24, 1000, 10000), request_counts=(24, 200)) -> dict:
    planning_rows = []
    for s in satellite_counts:
        planning_rows.append(_subprocess(["--case", "planning", "--satellites", str(s), "--settings", "scale"]))
    operational = _subprocess(["--case", "planning", "--satellites", "24", "--settings", "operational"])
    scheduler_rows = [_subprocess(["--case", "scheduler", "--requests", str(r)]) for r in request_counts]
    return {
        "benchmark": "B4 scale (T24)",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": {"python": platform.python_version(), "numpy": np.__version__, "cpus": os.cpu_count(),
                    "platform": platform.platform()},
        "n": {"planning_cases": len(planning_rows) + 1, "scheduler_cases": len(scheduler_rows),
              "runs_per_case": 1},
        "seeds": {"monte_carlo_and_requests": SEED},
        "settings": {"scale": SCALE, "operational": OPERATIONAL, "plans": list(PLANS),
                     "hypotheses": list(HYPOTHESES)},
        "planning_scale": planning_rows,
        "planning_operational_24": operational,
        "scheduler": scheduler_rows,
        "notes": [
            "Planning: batch shape (satellites, 6 hypotheses, 3 plans, Monte Carlo); running min/max only, no time "
            "history. evals = batch elements x time steps. Each case is a fresh subprocess; peak_ram_mib is its "
            "ru_maxrss and includes the Python, numpy and orbit-data baseline.",
            "The 1,000 and 10,000 satellite fleets repeat the 24 real illumination rows; they are a load test, "
            "not real satellites.",
            "Scheduler: the contract caps the fleet at SAT-01..SAT-24, so 200 means 200 uplink requests over the "
            "24 satellites and 3 stations (B1 decision 2026-11-08T00:25Z, confirmed by B). solve_ms is reported by "
            "the scheduler; CP-SAT time limit 7 s.",
        ],
        "data": {
            "real": ["24 satellite orbits (cached public OMM elements, renamed SAT-01..SAT-24)",
                     "Sun/Earth geometry (JPL DE421) and the illumination derived from it",
                     "ground station sites and 10 deg contact windows"],
            "simulated": ["EPS state and Monte Carlo perturbations (seeded)", "uplink requests (seeded priorities "
                          "and deadlines)", "satellites 25..10,000 (copies of the 24 real illumination rows)"],
        },
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.b4")
    ap.add_argument("--case", choices=["all", "planning", "scheduler"], default="all")
    ap.add_argument("--satellites", type=int, default=24)
    ap.add_argument("--settings", choices=["scale", "operational"], default="scale")
    ap.add_argument("--requests", type=int, default=24)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--machine-label", help="Name of the machine that produced the measurement")
    ap.add_argument("--provider", help="Cloud or machine provider")
    ap.add_argument("--plan", help="Instance plan name")
    ap.add_argument("--vcpu", type=int, help="Instance vCPU count")
    ap.add_argument("--vcpus", type=int, help="Instance vCPU count for the scale slider")
    ap.add_argument("--ram-gb", type=float, help="Instance advertised physical RAM in GB")
    ap.add_argument("--ram-gib", type=float, help="Instance RAM value for the scale slider")
    ap.add_argument("--limit-ram-gib", type=float, help="Container RAM limit in GiB")
    args = ap.parse_args(argv)
    if args.vcpu is not None and args.vcpu < 1:
        ap.error("--vcpu must be positive")
    if args.vcpus is not None and args.vcpus < 1:
        ap.error("--vcpus must be positive")
    if args.ram_gb is not None and args.ram_gb <= 0:
        ap.error("--ram-gb must be positive")
    if args.ram_gib is not None and args.ram_gib <= 0:
        ap.error("--ram-gib must be positive")
    if args.limit_ram_gib is not None and args.limit_ram_gib <= 0:
        ap.error("--limit-ram-gib must be positive")
    if args.case == "planning":
        cfg = SCALE if args.settings == "scale" else OPERATIONAL
        print(json.dumps(planning_case(args.satellites, **cfg)))
    elif args.case == "scheduler":
        print(json.dumps(scheduler_case(args.requests)))
    else:
        result = run_all()
        if args.machine_label:
            result["machine"]["label"] = args.machine_label
        if args.provider:
            result["machine"]["provider"] = args.provider
        if args.plan:
            result["machine"]["plan"] = args.plan
        if args.vcpu is not None:
            result["machine"]["vcpu"] = args.vcpu
        if args.vcpus is not None:
            result["machine"]["vcpus"] = args.vcpus
        if args.ram_gb is not None:
            result["machine"]["ram_gb"] = args.ram_gb
        if args.ram_gib is not None:
            result["machine"]["ram_gib"] = args.ram_gib
        if args.limit_ram_gib is not None:
            result["machine"]["container_ram_limit_gib"] = args.limit_ram_gib
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({k: result[k] for k in ("planning_scale", "planning_operational_24", "scheduler")}, indent=1))


if __name__ == "__main__":
    main()
