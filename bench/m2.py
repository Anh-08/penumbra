"""M2 (T31): collision-avoidance burn coupled with power. Which burn window keeps the battery above the floor?

Over seeded `conjunction` scenarios (SIMULATED debris DEB-01 passing SAT-17; real orbits, Sun and contacts), three
arms pick the burn window:
  (a) w1_always   - the earliest, cheapest-delta-v window W1, whatever the battery says;
  (b) verified    - the verifier's power-coupled choice: each window simulated (Monte Carlo) against every
                    plausible hypothesis, the top-ranked passing window burned; none passing -> the CAM is deferred
                    (T31's "infeasible: defer CAM, SAFE_MODE power posture");
  (c) w3_always   - the latest window W3 (largest delta-v, battery charged).
Each case is judged by the truth simulator from the alert to one orbit after closest approach: true SOC below the
floor (0.30), chosen window, achieved miss distance (the window's miss_after_km; the raw miss if no burn) and
payload uptime (the burn blocks the payload during slew + burn).

Sanity check, not validation: the debris and its pass are simulated, and so are the EPS truth, its noise and the
burn power and slew time.

    python -m bench.m2 [--seeds 50]       # writes bench/results/m2.json and m2.png
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from contracts import Command, Plan
from backend.eps.maneuver import burn_profile
from backend.eps.params import EpsParams
from backend.fleet import demo, scenarios
from backend.fleet.backend import FleetBackend
from backend.verify import constants as K

RESULTS = Path(__file__).parent / "results"
SATS = ("SAT-03", "SAT-12", "SAT-17")   # the focus satellite plus two nominal peers (fleet-coupled scoring)
FOCUS = "SAT-17"
ARMS = ("w1_always", "verified", "w3_always")
MONTE_CARLO = 100
LABEL = "simulated debris · sanity check, not validation"
DATA = {
    "real": ["satellite orbits (cached public OMM elements, renamed SAT-01..SAT-24)",
             "Sun/Earth geometry (JPL DE421) and the illumination derived from it",
             "ground station sites and 10 deg contact windows",
             "battery OCV curve, R0, coulombic efficiency and fade rate inside the truth simulator (NASA PCoE fit)"],
    "simulated": ["the debris object DEB-01 and its pass (data/debris/debris.json)",
                  "EPS truth, sensor noise and cell spreads (seeded)",
                  "burn power, burn duration and slew time (assumed, backend/eps/params.py)",
                  "payload tasking (full duty)"],
}


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _plan(be: FleetBackend, cj, contact, window_id: str, plan_id: str) -> Plan:
    """One CAM_BURN in the window, as the agent would propose it (30 s into the window, never before the uplink)."""
    w = cj.window(window_id)
    return Plan(plan_id=plan_id, sat_id=FOCUS, kind="maneuver", uplink=contact, created_by="bench-m2",
                commands=[Command(t_exec=max(w.t_start + 30.0, contact.aos), name="CAM_BURN",
                                  args={"window_id": window_id})])


def _choose(be: FleetBackend, cj, contact, arm: str) -> tuple[str | None, dict]:
    if arm == "w1_always":
        return "W1", {}
    if arm == "w3_always":
        return "W3", {}
    sims = {}
    for i, w in enumerate(cj.windows, 1):
        sims[w.window_id] = be.simulate(_plan(be, cj, contact, w.window_id, f"P-{i}"), "all", MONTE_CARLO)
    by_plan = {s.plan_id: wid for wid, s in sims.items()}
    ranked = [by_plan[p] for p in be.verifier.rank(FOCUS).ordered if p in by_plan]
    passing = [wid for wid in ranked if sims[wid].passes_all]
    verdicts = {wid: {"passes_all": bool(s.passes_all), "worst_margin_soc": round(float(s.worst_margin_soc), 4)}
                for wid, s in sims.items()}
    return (passing[0] if passing else None), verdicts


def case(seed: int, arm: str) -> dict:
    sc = scenarios.build("conjunction", seed=seed, sat_ids=SATS)
    be = FleetBackend(sc)
    t_alert, _ = demo.until_alert(be, FOCUS)
    cj = be.conjunctions(FOCUS)[0]
    contact = be.next_contact(FOCUS, t_alert)
    window_id, verdicts = _choose(be, cj, contact, arm)
    end = min(float(cj.tca) + K.ORBIT_S, float(sc.env.t[-1]))
    burns: list[Command] = []
    if window_id is not None:
        plan = _plan(be, cj, contact, window_id, "P-9")
        if contact.aos > be.now():
            be.advance_to(contact.aos)
        be.execute(plan)
        burns = [c for c in plan.commands if c.name == "CAM_BURN"]
    be.world.advance_to(end)

    tr = be.world.reveal(FOCUS)["truth_soc"]
    t, soc = np.asarray(tr["t"], float), np.asarray(tr["soc"], float)
    sel = t >= t_alert
    grid = sc.env.t[(sc.env.t >= t_alert) & (sc.env.t <= end)]
    _, block = burn_profile(grid, burns, {w.window_id: w for w in cj.windows}, EpsParams().cam_slew_s)
    w = cj.window(window_id) if window_id else None
    return {
        "seed": seed, "arm": arm, "window": window_id,
        "deferred": window_id is None,
        "violated": bool((soc[sel] < K.SOC_MIN).any()),
        "min_soc": round(float(soc[sel].min()), 4),
        "miss_after_km": round(float(w.miss_after_km if w else cj.miss_km), 3),
        "payload_uptime": round(float(1.0 - block.mean()) if len(grid) else 1.0, 4),
        "verifier": verdicts,
    }


def summarize(rows: list[dict]) -> dict:
    out = {}
    for arm in ARMS:
        rs = [r for r in rows if r["arm"] == arm]
        if not rs:
            continue
        hist = {wid: sum(r["window"] == wid for r in rs) for wid in ("W1", "W2", "W3")}
        hist["deferred"] = sum(r["deferred"] for r in rs)
        out[arm] = {
            "cases": len(rs),
            "soc_violations": sum(r["violated"] for r in rs),
            "min_soc_p5": round(float(np.percentile([r["min_soc"] for r in rs], 5)), 4),
            "window_histogram": hist,
            "miss_after_km_mean": round(float(np.mean([r["miss_after_km"] for r in rs])), 3),
            "miss_after_km_min": round(float(min(r["miss_after_km"] for r in rs)), 3),
            "payload_uptime_mean": round(float(np.mean([r["payload_uptime"] for r in rs])), 4),
        }
    return out


def findings(summary: dict) -> list[str]:
    """Plain statements read off the summary (so the text never drifts from the numbers)."""
    out = []
    s = summary
    if all(a in s for a in ARMS):
        viol = ", ".join(f"{a} {s[a]['soc_violations']}" for a in ARMS)
        out.append(f"True SOC below the floor after the alert: {viol} (cases).")
        p5 = ", ".join(f"{a} {s[a]['min_soc_p5']:.3f}" for a in ARMS)
        out.append(f"Lowest true SOC after the alert, 5th percentile over seeds: {p5}. The early cheap-delta-v window "
                   "leaves the least battery margin; that is the power coupling.")
        chosen = {w: n for w, n in s["verified"]["window_histogram"].items() if n}
        out.append(f"The verifier's choice: {chosen}. It is conservative (planning model, every plausible "
                   "hypothesis, p5): it can reject a window the truth would have survived.")
        misses = {s[a]["miss_after_km_mean"] for a in ARMS}
        if len(misses) == 1:
            out.append(f"Achieved miss distance is the same for every window here ({misses.pop()} km), so it does "
                       "not separate the arms in this scenario.")
    return out


def run(seeds: range) -> dict:
    t0 = time.perf_counter()
    rows = [case(s, a) for s in seeds for a in ARMS]
    return {
        "benchmark": "M2 collision-avoidance burn coupled with power (T31)", "label": LABEL,
        "generated_utc": _stamp(), "n": len(seeds), "seeds": list(seeds),
        "machine": {"python": platform.python_version(), "platform": platform.platform()},
        "runtime_s": round(time.perf_counter() - t0, 1),
        "settings": {"scenario": "conjunction (backend/fleet/scenarios.py), satellites " + ", ".join(SATS),
                     "focus": FOCUS, "monte_carlo": MONTE_CARLO, "soc_floor": K.SOC_MIN,
                     "horizon": "alert to closest approach + one orbit",
                     "burn_time": "30 s into the window, never before the uplink AOS",
                     "verified": "every window simulated against every plausible hypothesis; the top-ranked "
                                 "passing window burns; none passing -> CAM deferred (no burn)"},
        "summary": (summary := summarize(rows)),
        "findings": findings(summary),
        "notes": ["soc_violations = cases whose true SOC goes below the floor after the alert.",
                  "miss_after_km is the window's planned miss distance (backend/orbit model); a deferred CAM keeps "
                  "the raw miss distance. Payload uptime counts the time the burn blocks the payload.",
                  "The verifier uses the planning model, not the truth, so a verified burn can still breach the "
                  "floor in the truth; that is what this benchmark measures."],
        "data": DATA,
        "rows": rows,
    }


def plot(result: dict, path: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    s = result["summary"]
    arms = [a for a in ARMS if a in s]
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
    lows = [[r["min_soc"] for r in result["rows"] if r["arm"] == a] for a in arms]
    axes[0].boxplot(lows, tick_labels=arms, widths=0.5)
    axes[0].axhline(K.SOC_MIN, color="#c0392b", lw=1.2, ls="--", label=f"floor {K.SOC_MIN:.2f}")
    axes[0].set_ylim(min(K.SOC_MIN, *(min(v) for v in lows)) - 0.05, 1.0)
    axes[0].legend(fontsize=8, loc="upper right")
    viol = " · ".join(str(s[a]["soc_violations"]) for a in arms)
    axes[0].set_title(f"lowest true SOC per case (below floor: {viol})", fontsize=9)
    bottom = np.zeros(len(arms))
    for wid, col in (("W1", "#c0392b"), ("W2", "#f39c12"), ("W3", "#27ae60"), ("deferred", "#7f8c8d")):
        vals = np.array([s[a]["window_histogram"][wid] for a in arms], float)
        axes[1].bar(arms, vals, bottom=bottom, label=wid, color=col)
        bottom += vals
    axes[1].set_title("chosen window")
    axes[1].legend(fontsize=8)
    axes[2].bar(arms, [s[a]["payload_uptime_mean"] for a in arms], color="#2e86c1")
    axes[2].set_ylim(0, 1)
    axes[2].set_title("payload uptime (mean)")
    for ax in axes:
        ax.tick_params(axis="x", labelsize=8)
    fig.suptitle(f"M2 collision-avoidance burn vs power · {LABEL} · n={result['n']} seeds", fontsize=10)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.m2")
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--out", default=str(RESULTS / "m2.json"))
    args = ap.parse_args(argv)
    result = run(range(args.seeds))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
    plot(result, out.with_suffix(".png"))
    print(json.dumps(result["summary"], indent=1))


if __name__ == "__main__":
    main()
