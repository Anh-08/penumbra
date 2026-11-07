"""T16 evaluation: how often does the true fault rank first after one diagnostic test?

Per seed: a small fleet on a synthetic circular orbit, one seeded fault (beta_season hits the whole
fleet), telemetry downlinked only during contacts. The scorer sees telemetry and the commands the
ground sent, never the fault spec. After two passes with the fault, the standard diagnostic runs in
the next contact and the fleet is re-scored.

Run: python -m backend.eps.eval_diagnosis [n_seeds] [out.json]
Lives in backend/eps because it needs the fault specs to know the right answer.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np

from contracts import HYPOTHESES

from . import faults, synthetic
from backend.verify import diagnostics, scoring

FLEET = ("SAT-07", "SAT-12", "SAT-19")
TARGET = "SAT-07"
T0 = 1_794_000_000.0
CONTACT_EVERY_S = 5400.0
CONTACT_LEN_S = 600.0
HOURS = 12.0


def _passes(mask: np.ndarray) -> list[tuple[int, int]]:
    return scoring.passes(mask)


def score_world(world: faults.EpsWorld, env, commands: dict[str, list]) -> dict[str, scoring.Score]:
    evs = {}
    for i, s in enumerate(env.sat_ids):
        evs[s] = scoring.evidence(s, env.t, env.illum[i], env.beta_deg[i], world.telemetry(s),
                                  commands=commands.get(s, []))
    return scoring.score_fleet(evs)


def run_seed(seed: int) -> dict:
    env = synthetic.environment(FLEET, hours=HOURS, beta_deg=20.0, contact_every_s=CONTACT_EVERY_S,
                                contact_len_s=CONTACT_LEN_S)
    fault = faults.seeded_fault(seed, TARGET, T0, 4 * 3600.0)
    truth_h = HYPOTHESES[1 + ["array_degradation", "stuck_on_load", "capacity_fade", "sensor_bias",
                              "beta_season"].index(fault.kind)]
    world = faults.EpsWorld(env, [fault], seed=seed)
    row = FLEET.index(TARGET)
    runs = [(a, b) for a, b in _passes(env.in_contact[row]) if env.t[a] > fault.onset_t]
    # alert after two passes with the fault; the diagnostic runs in the third
    a2, b2 = runs[1]
    world.advance_to(env.t[b2 - 1])
    pre = score_world(world, env, {})[TARGET]
    a3, b3 = runs[2]
    diag = diagnostics.standard_diagnostic(env.t[a3], env.t[b3 - 1])
    world.execute(TARGET, diag)
    world.advance_to(env.t[b3 - 1])
    post = score_world(world, env, {TARGET: diag})[TARGET]
    return {
        "seed": seed, "truth": truth_h, "kind": fault.kind,
        "pre_top": pre.top, "post_top": post.top,
        "post_truth_posterior": round(post.posterior[truth_h], 3),
        "post_plausible": post.plausible,
        "correct_pre": pre.top == truth_h, "correct_post": post.top == truth_h,
    }


def healthy_seed(seed: int, beta_deg: float) -> dict:
    """No fault anywhere: every satellite should come out as H0."""
    env = synthetic.environment(FLEET, hours=8.0, beta_deg=beta_deg, contact_every_s=CONTACT_EVERY_S,
                                contact_len_s=CONTACT_LEN_S)
    world = faults.EpsWorld(env, [], seed=1000 + seed)
    world.advance_to(env.t[-1])
    sc = score_world(world, env, {})
    return {"seed": seed, "beta_deg": beta_deg, "tops": {s: v.top for s, v in sc.items()},
            "p_h0": {s: round(v.posterior["H0"], 3) for s, v in sc.items()}}


def evaluate(n_seeds: int = 20, n_healthy: int = 10) -> dict:
    t = time.perf_counter()
    rows = [run_seed(s) for s in range(n_seeds)]
    healthy = [healthy_seed(s, b) for s in range(n_healthy) for b in (5.0, 35.0)]
    sat_calls = [top for h in healthy for top in h["tops"].values()]
    false_alarm = float(np.mean([top != "H0" for top in sat_calls]))
    acc_pre = float(np.mean([r["correct_pre"] for r in rows]))
    acc_post = float(np.mean([r["correct_post"] for r in rows]))
    in_plausible = float(np.mean([r["truth"] in r["post_plausible"] for r in rows]))
    return {
        "metric": "true fault ranks first after one diagnostic",
        "n_seeds": n_seeds, "top1_after_diagnostic": acc_post, "top1_before_diagnostic": acc_pre,
        "truth_in_plausible_after": in_plausible, "target": 0.8,
        "healthy_false_alarm_rate": false_alarm, "healthy_satellite_scores": len(sat_calls),
        "runtime_s": round(time.perf_counter() - t, 1),
        "data": {
            "real": [],
            "simulated": ["orbit (synthetic circular 500 km, beta 20 deg)", "contacts (10 min every 90 min)",
                          "EPS telemetry (truth simulator, battery parameters fitted from NASA PCoE data)",
                          "faults (seeded)"],
        },
        "rows": rows,
        "healthy": healthy,
    }


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    out = evaluate(n)
    text = json.dumps(out, indent=1)
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    summary = {k: v for k, v in out.items() if k not in ("rows", "healthy")}
    print(json.dumps(summary, indent=1))
    for r in out["rows"]:
        print(r["seed"], r["kind"].ljust(18), "pre", r["pre_top"], "post", r["post_top"],
              "p(truth)", r["post_truth_posterior"], "OK" if r["correct_post"] else "MISS")
