"""T29 benchmarks B2, B3 and B5. Each writes its own results file with n, seeds and real vs simulated inputs.

B2  Diagnosis ablation: top-1 accuracy with and without peer comparison and the diagnostic test, on the
    T16 evaluation worlds (synthetic orbit, seeded faults). Tool calls follow the agent's protocol.
B3  Operator queue: a seeded multi-satellite fault storm on the real orbit, worked by one simulated
    operator who needs X minutes per decision. FCFS (alert order, next contact) against triage order
    (backend triage) plus the uplink scheduler. Outcome from the truth simulator: satellites whose
    true SOC falls below SOC_MIN, and alert-to-uplink delay.
B5  Payload uptime: for each faulty satellite in the same storms, the verifier's best passing plan
    against SAFE_MODE at the first chance. Payload uptime from the commanded duty timeline; SOC
    violations from the truth simulator.

    python -m bench.t29 [b2|b3|b5|all] [--seeds N]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from contracts import FAULT_KINDS, HYPOTHESES, SAT_IDS, Command, FaultSpec, Plan, ScheduleRequest

RESULTS = Path(__file__).parent / "results"
OPERATOR_MIN_PER_DECISION = 20.0          # X for B3: read, decide, approve one satellite
STORM_FAULTY = 6                          # faulty satellites per B3/B5 storm
STORM_HEALTHY = 4                         # healthy peers in the run
STORM_HOURS = 12.0
FIX = ({"name": "SET_PAYLOAD_DUTY", "args": {"duty": 0.2}}, {"name": "LOAD_OFF", "args": {"load": "AUX"}})

REAL_ORBIT = ["24 satellite orbits (cached public OMM elements, renamed SAT-01..SAT-24)",
              "Sun/Earth geometry (JPL DE421) and the illumination derived from it",
              "ground station sites and 10 deg contact windows"]
PCOE = "battery OCV curve, R0, coulombic efficiency and fade rate inside the truth simulator (NASA PCoE fit)"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(name: str, payload: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(payload, indent=1, default=str) + "\n", encoding="utf-8")
    return path


# ----------------------------------------------------------------------------- B2
def b2(seeds: range = range(40)) -> dict:
    """Top-1 diagnosis accuracy for four arms on the same worlds and telemetry."""
    from backend.eps import eval_diagnosis as E
    from backend.eps import faults, synthetic
    from backend.verify import diagnostics, scoring

    arms = {"peers+diagnostic": [], "peers_only": [], "diagnostic_only": [], "neither": []}
    rows = []
    for seed in seeds:
        env = synthetic.environment(E.FLEET, hours=E.HOURS, beta_deg=20.0, contact_every_s=E.CONTACT_EVERY_S,
                                    contact_len_s=E.CONTACT_LEN_S)
        fault = faults.seeded_fault(seed, E.TARGET, E.T0, 4 * 3600.0)
        truth_h = HYPOTHESES[1 + list(FAULT_KINDS).index(fault.kind)]
        world = faults.EpsWorld(env, [fault], seed=seed)
        row_i = E.FLEET.index(E.TARGET)
        runs = [(a, b) for a, b in scoring.passes(env.in_contact[row_i]) if env.t[a] > fault.onset_t]
        world.advance_to(env.t[runs[1][1] - 1])

        def evidence(cmds):
            return {s: scoring.evidence(s, env.t, env.illum[i], env.beta_deg[i], world.telemetry(s),
                                        commands=cmds.get(s, [])) for i, s in enumerate(env.sat_ids)}

        pre = evidence({})
        a3, b3_ = runs[2]
        diag = diagnostics.standard_diagnostic(env.t[a3], env.t[b3_ - 1])
        world.execute(E.TARGET, diag)
        world.advance_to(env.t[b3_ - 1])
        post = evidence({E.TARGET: diag})
        top = {
            "peers+diagnostic": scoring.score_fleet(post)[E.TARGET].top,
            "peers_only": scoring.score_fleet(pre)[E.TARGET].top,
            "diagnostic_only": scoring.posterior(post[E.TARGET]).top,
            "neither": scoring.posterior(pre[E.TARGET]).top,
        }
        for arm, h in top.items():
            arms[arm].append(h == truth_h)
        rows.append({"seed": seed, "truth": truth_h, "kind": fault.kind, **{f"top_{k}": v for k, v in top.items()}})
    # tool calls per case in the agent protocol (backend/agent stub flow, T20): detect + telemetry + score,
    # and with a diagnostic also propose_diagnostic_test, simulate, schedule_uplink, request_approval, rescore
    tool_calls = {"peers+diagnostic": 8, "peers_only": 3, "diagnostic_only": 8, "neither": 3}
    by_kind = {}
    for r in rows:
        k = by_kind.setdefault(r["kind"], {a: [0, 0] for a in arms})
        for a in arms:
            k[a][0] += r[f"top_{a}"] == r["truth"]
            k[a][1] += 1
    return {
        "benchmark": "B2 diagnosis ablation (T29)", "generated_utc": _stamp(),
        "n": len(rows), "seeds": list(seeds),
        "accuracy_top1": {a: round(float(np.mean(v)), 3) for a, v in arms.items()},
        "tool_calls_per_case": tool_calls,
        "accuracy_by_fault_kind": {k: {a: f"{c}/{n}" for a, (c, n) in v.items()} for k, v in by_kind.items()},
        "settings": {"fleet": list(E.FLEET), "target": E.TARGET, "hours": E.HOURS, "beta_deg": 20.0,
                     "contacts": "10 min every 90 min", "diagnostic": "backend.verify.diagnostics.standard_diagnostic",
                     "scored_after": "two passes with the fault (no diagnostic) or the diagnostic pass"},
        "notes": ["Same worlds and telemetry for all arms; only the scorer input changes.",
                  "'peers' = backend.verify.scoring.score_fleet (beta season is one fleet event); without peers "
                  "= per-satellite posterior.", "Tool calls are the agent protocol's count, not a measured agent run."],
        "data": {"real": [PCOE], "simulated": ["orbit (synthetic circular 500 km, beta 20 deg)", "contacts",
                                               "EPS telemetry", "faults (seeded, backend.eps.faults.seeded_fault)"]},
        "rows": rows,
    }


# ----------------------------------------------------------------------------- storms for B3 / B5
def _storm(seed: int):
    """Seeded storm on the real orbit: STORM_FAULTY satellites with faults at T0 + 2 h, flying duty 1.0."""
    from backend.eps.faults import EpsWorld
    from backend.eps.planning import InitialState, planning_params
    from backend.fleet import scenarios

    rng = np.random.default_rng([seed, 2029])
    picked = [str(s) for s in rng.choice(SAT_IDS, STORM_FAULTY + STORM_HEALTHY, replace=False)]
    faulty, healthy = picked[:STORM_FAULTY], picked[STORM_FAULTY:]
    sat_ids = tuple(sorted(picked))
    env, contacts, model = scenarios.orbit_environment(sat_ids, hours=STORM_HOURS)
    onset = scenarios.T0 + 2 * 3600.0
    fault_list = []
    for s in faulty:
        kind = str(rng.choice(["stuck_on_load", "array_degradation", "capacity_fade"]))
        params = ({"load": "AUX"} if kind == "stuck_on_load" else
                  {"loss": float(rng.uniform(0.25, 0.4)), "panel": "ALL"} if kind == "array_degradation" else
                  {"remaining": float(rng.uniform(0.4, 0.6))})
        fault_list.append(FaultSpec(kind=kind, target=s, params=params, onset_t=onset))
    soc = np.array([float(rng.uniform(0.6, 0.8)) if s in faulty else 0.75 for s in env.sat_ids])
    world = EpsWorld(env, fault_list, seed=seed, soc0=soc)
    for s in faulty:
        world.execute(s, [Command(t_exec=scenarios.T0, name="SET_PAYLOAD_DUTY", args={"duty": 1.0})])
    initial = InitialState.nominal(len(env.sat_ids), soc=soc, params=planning_params())
    sc = scenarios.Scenario(name=f"bench-storm-{seed}", seed=seed, env=env, contacts=contacts, world=world,
                            focus_sat=None, initial=initial, orbit=model)
    return sc, faulty, healthy, fault_list


def _fix_commands(t: float) -> list[Command]:
    return [Command(t_exec=t, name=c["name"], args=c["args"]) for c in FIX]


def _truth_violations(world, sats, after_t, fixed_at: dict | None = None) -> dict:
    """True SOC below 0.30 after onset, and separately after that satellite's fix executed."""
    out = {}
    for s in sats:
        tr = world.reveal(s)["truth_soc"]
        t, soc = np.array(tr["t"]), np.array(tr["soc"])
        sel = t >= after_t
        row = {"min_soc": round(float(soc[sel].min()), 3), "violated": bool((soc[sel] < 0.30).any())}
        fx = (fixed_at or {}).get(s)
        if fx is not None:
            after = t >= fx
            at_fix = float(soc[int(np.searchsorted(t, fx))]) if after.any() else float("nan")
            row["soc_at_fix"] = round(at_fix, 3)
            row["min_soc_after_fix"] = round(float(soc[after].min()), 3) if after.any() else None
            row["violated_after_fix"] = bool((soc[after] < 0.30).any())
            # the plan's own doing: above the floor when it executed, below it afterwards
            row["plan_attributable_violation"] = bool(at_fix >= 0.30 and (soc[after] < 0.30).any())
        out[s] = row
    return out


# ----------------------------------------------------------------------------- B3
def _operator_run(seed: int, policy: str, x_min: float) -> dict:
    from backend.fleet import scenarios
    from backend.fleet.backend import FleetBackend
    from backend.sched import UplinkScheduler

    sc, faulty, healthy, _ = _storm(seed)
    be = FleetBackend(sc)
    end = scenarios.T0 + STORM_HOURS * 3600.0 - 60.0
    alerted: dict[str, float] = {}
    handled: dict[str, dict] = {}
    t_free = float(sc.env.t[0])
    busy: tuple[str, float, float] | None = None   # (sat, decision_start, decision_done)
    scheduler = UplinkScheduler(sc.contacts)
    t = float(sc.env.t[0])
    while t < end and len(handled) < len(alerted) + 1 and len(handled) < len(faulty) + len(healthy):
        t = min(t + 60.0, end)
        be.advance_to(t)
        be.detector.drain_messages()
        for a in be.detect(t):
            if a.severity in ("warning", "critical") and a.sat_id not in alerted:
                alerted[a.sat_id] = t
        if busy is not None and t >= busy[2]:
            sat, _, done = busy
            if policy == "triage":
                # as the dispatcher does: the plan's uplink is the next contact, deadline its LOS
                up, probe = None, done
                for _ in range(3):
                    c = sc.next_contact(sat, probe)
                    if c is None:
                        break
                    res = scheduler.schedule([ScheduleRequest(plan_id="P-1", sat_id=sat, priority=1.0,
                                                              deadline_t=c.los, duration_s=60.0)], now=done)
                    if res.assignments:
                        up = res.assignments[0].start_t
                        break
                    probe = c.los + 1.0
            else:
                c = sc.next_contact(sat, done)
                up = max(c.aos, done) if c is not None else None
            if up is not None and up < end:
                be.world.execute(sat, _fix_commands(max(up, be.now()) + 30.0))
            handled[sat] = {"alert_t": alerted[sat], "decision_done": done, "uplink_t": up}
            busy, t_free = None, done
        if busy is None:
            queue = [s for s in alerted if s not in handled]
            if queue:
                if policy == "fcfs":
                    pick = min(queue, key=lambda s: (alerted[s], s))
                else:
                    order = [e.sat_id for e in be.triage()]
                    pick = min(queue, key=lambda s: order.index(s) if s in order else len(order))
                start = max(t, t_free)
                busy = (pick, start, start + x_min * 60.0)
        if not [s for s in alerted if s not in handled] and busy is None and t > scenarios.T0 + 8 * 3600:
            break
    be.advance_to(end)
    fixed = {s: h["uplink_t"] + 30.0 for s, h in handled.items() if h["uplink_t"] is not None}
    truth = _truth_violations(be.world, faulty, scenarios.T0 + 2 * 3600.0, fixed)
    delays = [(h["uplink_t"] - h["alert_t"]) / 60.0 for h in handled.values() if h["uplink_t"] is not None]
    return {"seed": seed, "policy": policy, "faulty": faulty, "alerted": len(alerted), "handled": len(handled),
            "violations": sum(v["violated"] for v in truth.values()),
            "violations_after_fix": sum(v.get("violated_after_fix", False) for v in truth.values()),
            "plan_attributable_violations": sum(v.get("plan_attributable_violation", False) for v in truth.values()),
            "false_alerts": sum(s not in faulty for s in alerted),
            "mean_alert_to_uplink_min": round(float(np.mean(delays)), 1) if delays else None, "truth": truth}


def b3(seeds: range = range(10), x_min: float = OPERATOR_MIN_PER_DECISION) -> dict:
    rows = [_operator_run(s, p, x_min) for s in seeds for p in ("fcfs", "triage")]
    summary = {}
    for p in ("fcfs", "triage"):
        r = [x for x in rows if x["policy"] == p]
        summary[p] = {"violations_total": sum(x["violations"] for x in r),
                      "violations_after_fix": sum(x["violations_after_fix"] for x in r),
                      "plan_attributable_violations": sum(x["plan_attributable_violations"] for x in r),
                      "satellites_at_risk": sum(len(x["faulty"]) for x in r),
                      "mean_alert_to_uplink_min": round(float(np.mean([x["mean_alert_to_uplink_min"] for x in r
                                                                       if x["mean_alert_to_uplink_min"] is not None])), 1)}
    return {
        "benchmark": "B3 operator queue: FCFS vs triage + scheduler (T29)", "generated_utc": _stamp(),
        "n": len(seeds), "seeds": list(seeds), "operator_minutes_per_decision": x_min, "summary": summary,
        "settings": {"faulty_per_storm": STORM_FAULTY, "healthy_per_storm": STORM_HEALTHY, "hours": STORM_HOURS,
                     "onset": "T0 + 2 h", "fault_kinds": ["stuck_on_load (AUX)", "array_degradation 0.25-0.40",
                                                          "capacity_fade 0.40-0.60"],
                     "tasking": "faulty satellites fly payload duty 1.0, start SOC 0.6-0.8",
                     "fix": "SET_PAYLOAD_DUTY 0.2 + LOAD_OFF AUX, 30 s after the uplink time",
                     "fcfs": "alerts in arrival order, uplink at the next contact",
                     "triage": "backend triage order (severity, then slack); uplink slot from backend/sched inside "
                               "the next contact (deadline = its LOS), as the agent's dispatcher books it"},
        "notes": ["One operator, one decision at a time; each decision takes X minutes of sim time.",
                  "violations = faulty satellites whose true SOC goes below 0.30 after onset (truth simulator); "
                  "violations_after_fix only after that satellite's fix executed.",
                  "The same fix is used in both arms. With one decision at a time the scheduler rarely meets a "
                  "station conflict, so the arms differ mainly in the order the operator works the queue."],
        "data": {"real": REAL_ORBIT + [PCOE], "simulated": ["EPS telemetry and truth", "faults and tasking (seeded)",
                                                            "the operator"]},
        "rows": [{k: v for k, v in r.items() if k != "truth"} | {"truth": r["truth"]} for r in rows],
    }


# ----------------------------------------------------------------------------- B5
def _uptime(cmds: list[Command], t0: float, t1: float, initial_duty: float) -> float:
    """Mean commanded payload duty over [t0, t1] (0 while the payload is off or in SAFE_MODE)."""
    edges = sorted([c for c in cmds if c.t_exec <= t1], key=lambda c: c.t_exec)
    duty, on, t, acc = initial_duty, True, t0, 0.0
    for c in edges:
        if c.t_exec > t0:
            acc += (min(c.t_exec, t1) - t) * (duty if on else 0.0)
            t = min(c.t_exec, t1)
        if c.name == "SET_PAYLOAD_DUTY":
            duty = float(c.args["duty"])
        elif c.name == "SAFE_MODE":
            on, duty = False, 0.0
        elif c.name == "LOAD_OFF" and c.args.get("load") == "PAYLOAD":
            on = False
        elif c.name == "LOAD_ON" and c.args.get("load") == "PAYLOAD":
            on = True
    acc += (t1 - t) * (duty if on else 0.0)
    return acc / (t1 - t0)


def _b5_case(seed: int, arm: str) -> dict:
    from backend.fleet import demo, scenarios
    from backend.fleet.backend import FleetBackend

    sc, faulty, _, _ = _storm(seed)
    be = FleetBackend(sc)
    end = scenarios.T0 + STORM_HOURS * 3600.0 - 60.0
    done: dict[str, dict] = {}
    t = float(sc.env.t[0])
    while t < end and len(done) < len(faulty):
        t = min(t + 60.0, end)
        be.advance_to(t)
        be.detector.drain_messages()
        for a in be.detect(t):
            s = a.sat_id
            if s in done or s not in faulty or a.severity not in ("warning", "critical"):
                continue
            c = sc.next_contact(s, t)
            if c is None:
                continue
            t_exec = max(c.aos, t) + 30.0
            if arm == "safe_mode_immediately":
                cmds = [Command(t_exec=t_exec, name="SAFE_MODE")]
                choice = "SAFE_MODE"
            else:
                be.score_hypotheses(s)
                cands = [[("SET_PAYLOAD_DUTY", {"duty": d})] for d in (0.6, 0.4, 0.2)]
                cands += [[("SET_PAYLOAD_DUTY", {"duty": 0.2}), ("LOAD_OFF", {"load": "AUX"})],
                          [("LOAD_OFF", {"load": "PAYLOAD"})], [("SAFE_MODE", {})]]
                plans = []
                for i, cl in enumerate(cands):
                    p = Plan(plan_id=f"P-{seed * 100 + len(done) * 10 + i + 1}", sat_id=s, kind="plan", uplink=c,
                             created_by="bench", commands=[Command(t_exec=t_exec, name=n, args=x) for n, x in cl])
                    be.simulate(p, "all", 40)
                    plans.append(p)
                best = be.rank(s).ordered[0]
                plan = next(p for p in plans if p.plan_id == best)
                cmds = list(plan.commands)
                choice = " + ".join(f"{x.name}{'(' + str(list(x.args.values())[0]) + ')' if x.args else ''}"
                                    for x in cmds)
            be.world.execute(s, cmds)
            done[s] = {"alert_t": t, "t_exec": t_exec, "choice": choice}
    be.advance_to(end)
    truth = _truth_violations(be.world, faulty, scenarios.T0 + 2 * 3600.0, {s: d["t_exec"] for s, d in done.items()})
    rows = []
    for s in faulty:
        if s not in done:
            continue
        up = _uptime(be.world.executed(s), done[s]["alert_t"], end, 1.0)
        rows.append({"sat_id": s, "choice": done[s]["choice"], "payload_uptime_after_alert": round(up, 3),
                     **truth[s]})
    return {"seed": seed, "arm": arm, "cases": rows}


def b5(seeds: range = range(10)) -> dict:
    out = [_b5_case(s, a) for s in seeds for a in ("verified_plan", "safe_mode_immediately")]
    summary = {}
    for a in ("verified_plan", "safe_mode_immediately"):
        cases = [c for r in out if r["arm"] == a for c in r["cases"]]
        summary[a] = {"cases": len(cases),
                      "mean_payload_uptime_after_alert": round(float(np.mean([c["payload_uptime_after_alert"]
                                                                               for c in cases])), 3),
                      "soc_violations": sum(c["violated"] for c in cases),
                      "soc_violations_after_fix": sum(c.get("violated_after_fix", False) for c in cases),
                      "plan_attributable_violations": sum(c.get("plan_attributable_violation", False) for c in cases),
                      "already_below_floor_at_fix": sum((c.get("soc_at_fix") or 1.0) < 0.30 for c in cases)}
    return {
        "benchmark": "B5 payload uptime: verified plan vs SAFE_MODE immediately (T29)", "generated_utc": _stamp(),
        "n": len(seeds), "seeds": list(seeds), "summary": summary,
        "settings": {"storm": "same seeded storms as B3", "verified_plan": "best of 6 candidates by the verifier's "
                     "rank (duty 0.6/0.4/0.2, duty 0.2 + AUX off, payload off, SAFE_MODE), MC 40",
                     "safe_mode_immediately": "SAFE_MODE at the first contact after the alert",
                     "uptime": "mean commanded payload duty from the alert to the end of the 12 h run"},
        "notes": ["Payload uptime is computed from the commanded duty timeline (what the ground flies).",
                  "soc_violations = faulty satellites whose true SOC goes below 0.30 after onset; most happen "
                  "before any uplink (detection waits for a contact). plan_attributable_violations counts the "
                  "cases that were above 0.30 when the plan executed and fell below it afterwards: that is what "
                  "the plan choice controls. A verified plan can still breach the floor in the truth because the "
                  "planning model differs from the truth (T11) and the scored hypothesis may be wrong."],
        "data": {"real": REAL_ORBIT + [PCOE], "simulated": ["EPS telemetry and truth", "faults and tasking (seeded)"]},
        "rows": out,
    }


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.t29")
    ap.add_argument("which", nargs="?", default="all", choices=["b2", "b3", "b5", "all"])
    ap.add_argument("--seeds", type=int, default=None)
    args = ap.parse_args(argv)
    if args.which in ("b2", "all"):
        r = b2(range(args.seeds or 40))
        print("b2", _write("b2", r), r["accuracy_top1"])
    if args.which in ("b3", "all"):
        r = b3(range(args.seeds or 10))
        print("b3", _write("b3", r), r["summary"])
    if args.which in ("b5", "all"):
        r = b5(range(args.seeds or 10))
        print("b5", _write("b5", r), r["summary"])


if __name__ == "__main__":
    main()
