"""T27: fault-storm and beta-season scenarios."""
import json
from pathlib import Path

import numpy as np
import pytest

from contracts import Command, Contact, Plan
from backend.agent.session import select_model
from backend.fleet import demo, flight_rules, scenarios
from backend.fleet.peers import peer_compare
from backend.verify import scoring

STORM = ("SAT-12", "SAT-07", "SAT-19")
RUN = Path(__file__).resolve().parents[3] / "runs" / "demo" / "live-fault-storm.jsonl"


def evidence_at(sc, t):
    sc.world.advance_to(t)
    env = sc.env
    return {s: scoring.evidence(s, env.t, env.illum[i], env.beta_deg[i], sc.world.telemetry(s),
                                commands=sc.world.executed(s)) for i, s in enumerate(env.sat_ids)}


def check_storm(out):
    o = out["outcomes"]
    assert set(o) == set(STORM)
    assert o["SAT-12"]["tier"] == 1 and o["SAT-12"]["flight_rule"] in ("FR-2", "FR-3")
    assert o["SAT-12"]["executed"] and o["SAT-12"]["triage_status"] == "auto_executed"
    assert o["SAT-07"]["tier"] == 2 and o["SAT-07"]["executed"] and o["SAT-07"]["triage_status"] == "executed"
    assert o["SAT-19"]["rank_status"] == "infeasible" and not o["SAT-19"]["executed"]
    assert o["SAT-19"]["triage_status"] == "safe_mode_recommended"


def test_fault_storm_three_outcomes_with_the_stub(tmp_path):
    check_storm(demo.run_storm(select_model(live=False), run_id="storm", runs_dir=tmp_path))


@pytest.mark.skipif(not RUN.exists(), reason="recorded storm run not present")
def test_recorded_live_storm_replays_to_the_same_outcomes(tmp_path, monkeypatch):
    monkeypatch.setenv("PENUMBRA_REPLAY", str(RUN))
    monkeypatch.setenv("PENUMBRA_REPLAY_PACE_MS", "0")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    out = demo.run_storm(select_model(live=False), run_id="storm-replay", runs_dir=tmp_path)
    check_storm(out)
    recorded = [json.loads(line) for line in RUN.read_text(encoding="utf-8").splitlines()]
    replayed = [json.loads(line) for line in (tmp_path / "storm-replay.jsonl").read_text().splitlines()]
    hashes = lambda ev: [e.get("plan_hash") for e in ev if e["kind"] == "execution"]  # noqa: E731
    assert hashes(replayed) == hashes(recorded) and len(hashes(recorded)) == 2


def test_peer_comparison_classifies_both_scenarios():
    storm = scenarios.build("fault-storm", seed=0)
    ev = evidence_at(storm, scenarios.T0 + 6 * 3600)
    for s in STORM:
        pc = peer_compare(ev, s)
        assert pc.hw_score > pc.env_score and "peers_nominal" in pc.explanation_keys, s
    beta = scenarios.build("beta-season", seed=0)
    ev = evidence_at(beta, scenarios.T0 + 6 * 3600)
    wins = [s for s in beta.env.sat_ids if (lambda pc: pc.env_score > pc.hw_score)(peer_compare(ev, s))]
    assert len(wins) == 24
    pc = peer_compare(ev, "SAT-12")
    assert "fleet_wide_environment" in pc.explanation_keys and "peers_show_same_array_deficit" in pc.explanation_keys


@pytest.mark.parametrize("name", ["fault-storm", "beta-season"])
def test_scenarios_are_deterministic(name):
    a, b = scenarios.build(name, seed=3), scenarios.build(name, seed=3)
    t = scenarios.T0 + 4 * 3600
    for sc in (a, b):
        sc.world.advance_to(t)
    for s in ("SAT-07", "SAT-12", "SAT-19"):
        ta, tb = a.world.telemetry(s), b.world.telemetry(s)
        for ch in ta:
            assert np.array_equal(ta[ch], tb[ch], equal_nan=True), (name, s, ch)
    assert [c.model_dump() for c in a.contacts] == [c.model_dump() for c in b.contacts]


def test_plan_commanding_a_suspect_load_is_tier2():
    t0 = scenarios.T0
    up = Contact(sat_id="SAT-07", station_id="SVALBARD", aos=t0, los=t0 + 360, max_el_deg=40)
    p = Plan(plan_id="P-1", sat_id="SAT-07", kind="plan", uplink=up, created_by="t",
             commands=[Command(t_exec=t0 + 60, name="LOAD_OFF", args={"load": "AUX"}),
                       Command(t_exec=t0 + 60, name="SET_PAYLOAD_DUTY", args={"duty": 0.2})])
    from backend.fleet.tests.test_tier_triage import sim   # same SimResult builder
    ok = flight_rules.RuleContext(now=t0 + 30, commanded_duty=1.0)
    stuck = flight_rules.RuleContext(now=t0 + 30, commanded_duty=1.0, suspect_loads=frozenset({"AUX"}))
    assert flight_rules.tier(p, sim(p), ok) == (1, "FR-3")
    assert flight_rules.tier(p, sim(p), stuck) == (2, None)
    duty_only = Plan(**{**p.model_dump(), "commands": [p.commands[1]]})
    assert flight_rules.tier(duty_only, sim(duty_only), stuck) == (1, "FR-2")   # does not touch the suspect
    payload_stuck = flight_rules.RuleContext(now=t0 + 30, commanded_duty=1.0, suspect_loads=frozenset({"PAYLOAD"}))
    assert flight_rules.tier(duty_only, sim(duty_only), payload_stuck) == (2, None)


def test_run_storm_streams_events_and_exposes_the_backend(tmp_path):
    seen, backends = [], []
    out = demo.run_storm(select_model(live=False), run_id="storm-stream", runs_dir=tmp_path,
                         on_event=seen.append, on_backend=backends.append)
    lines = (tmp_path / "storm-stream.jsonl").read_text().splitlines()
    assert len(seen) == len(lines) and {e.kind for e in seen} >= {"alert", "tool_call", "execution"}
    assert backends == [out["backend"]]


def test_run_storm_hands_tier2_to_the_flight_director(tmp_path):
    from backend.fleet.backend import FleetBackend

    be = FleetBackend(scenarios.build("fault-storm", seed=0))
    got, clicks = {}, []

    def flight_director(sat, dispatcher, ticket):        # the clock waits while this runs
        clicks.append((sat, be.now()))
        plan = dispatcher.approve(ticket.approval_id, ticket.plan_hash)
        assert plan.plan_id == ticket.plan_id
        dispatcher.execute(ticket.approval_id)

    out = demo.run_storm(select_model(live=False), run_id="storm-fd", runs_dir=tmp_path, backend=be,
                         on_dispatcher=lambda s, d: got.setdefault(s, d), on_approval=flight_director)
    assert out["backend"] is be and set(got) == set(STORM) == set(out["dispatchers"])
    o = out["outcomes"]
    assert o["SAT-12"]["tier"] == 1 and o["SAT-12"]["executed"]          # Tier 1 still auto-executes
    assert [s for s, _ in clicks] == ["SAT-07"] and o["SAT-07"]["executed"]
    assert o["SAT-19"]["triage_status"] == "safe_mode_recommended"
