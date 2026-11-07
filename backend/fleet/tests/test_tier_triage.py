import numpy as np
import pytest

from contracts import Alert, Command, Contact, FaultSpec, HypStats, Plan, SimResult
from backend.eps.faults import EpsWorld
from backend.fleet import demo, flight_rules, scenarios, triage
from backend.fleet.backend import FleetBackend
from backend.fleet.peers import peer_compare
from backend.verify import constants as K
from backend.verify import scoring

T0 = 1_794_000_000.0
UP = Contact(sat_id="SAT-12", station_id="TROLL", aos=T0, los=T0 + 360, max_el_deg=40)
CTX = flight_rules.RuleContext(now=T0 + 30, commanded_duty=0.8)


def plan(*cmds, kind="plan"):
    return Plan(plan_id="P-1", sat_id="SAT-12", kind=kind, uplink=UP, created_by="test",
                commands=[Command(t_exec=t, name=n, args=a) for t, n, a in cmds])


def sim(p, margin=0.3, passes=True, plan_hash=None):
    st = HypStats(pass_rate=1.0, min_soc_p5=0.5, min_v_bus=15.0, t_bat_min_c=5, t_bat_max_c=20, payload_uptime=0.5)
    return SimResult(sim_id="S-1", plan_id=p.plan_id, plan_hash=plan_hash or p.plan_hash, per_hyp={"H0": st},
                     plausible=["H0"], passes_all=passes, worst_margin_soc=margin, payload_uptime_worst=0.5,
                     n_commands=len(p.commands))


# ---------------- tier policy ----------------

def test_fr1_verified_diagnostic_in_current_pass():
    d = plan((T0 + 60, "LOAD_OFF", {"load": "AUX"}), (T0 + 120, "LOAD_OFF", {"load": "PAYLOAD"}),
             (T0 + 180, "LOAD_ON", {"load": "PAYLOAD"}), kind="diagnostic")
    assert flight_rules.tier(d, sim(d, margin=0.05), CTX) == (1, "FR-1")
    assert flight_rules.tier(d, sim(d, passes=False), CTX) == (2, None)
    later = flight_rules.RuleContext(now=T0 - 600, commanded_duty=0.8)           # pass not in progress
    assert flight_rules.tier(d, sim(d), later) == (2, None)
    slow = plan((T0 + 60, "LOAD_OFF", {"load": "AUX"}), (T0 + 200, "LOAD_ON", {"load": "AUX"}), kind="diagnostic")
    assert flight_rules.tier(slow, sim(slow), CTX) == (2, None)                    # held longer than one dwell
    late = plan((T0 + 300, "LOAD_OFF", {"load": "AUX"}), (T0 + 340, "LOAD_ON", {"load": "AUX"}), kind="diagnostic")
    assert flight_rules.tier(late, sim(late), CTX) == (2, None)                    # last dwell runs past LOS
    heater = plan((T0 + 60, "SET_HEATER_SETPOINT", {"setpoint_c": 10}), kind="diagnostic")
    assert flight_rules.tier(heater, sim(heater), CTX) == (2, None)


def test_fr2_duty_reduction_needs_margin():
    p = plan((T0 + 60, "SET_PAYLOAD_DUTY", {"duty": 0.3}))
    assert flight_rules.tier(p, sim(p, margin=K.TIER1_MARGIN), CTX) == (1, "FR-2")
    assert flight_rules.tier(p, sim(p, margin=K.TIER1_MARGIN - 0.01), CTX) == (2, None)
    up = plan((T0 + 60, "SET_PAYLOAD_DUTY", {"duty": 1.0}))
    assert flight_rules.tier(up, sim(up), CTX) == (2, None)                         # a duty increase


def test_fr3_load_shed_and_what_is_never_tier1():
    p = plan((T0 + 60, "LOAD_OFF", {"load": "AUX"}), (T0 + 60, "SET_PAYLOAD_DUTY", {"duty": 0.2}))
    assert flight_rules.tier(p, sim(p), CTX) == (1, "FR-3")
    for cmds in ([(T0 + 60, "SAFE_MODE", {})], [(T0 + 60, "LOAD_OFF", {"load": "HEATER"})],
                 [(T0 + 60, "LOAD_ON", {"load": "AUX"})], [(T0 + 60, "LOAD_OFF", {"load": "PAYLOAD"})]):
        q = plan(*cmds)
        assert flight_rules.tier(q, sim(q, margin=0.5), CTX) == (2, None), cmds


def test_stale_result_is_tier2():
    p = plan((T0 + 60, "SET_PAYLOAD_DUTY", {"duty": 0.3}))
    assert flight_rules.tier(p, sim(p, plan_hash="0" * 64), CTX) == (2, None)
    assert [r.rule_id for r in flight_rules.FLIGHT_RULES] == ["FR-1", "FR-2", "FR-3"]


# ---------------- triage ----------------

def alert(sat, sev, ttc, aos):
    return Alert(alert_id=f"AL-{sat[-2:]}", sat_id=sat, severity=sev, time_to_critical_s=ttc, next_contact_aos=aos,
                 reason_key="soc_trend_low")


def test_triage_orders_by_severity_then_slack():
    now = T0
    alerts = [alert("SAT-01", "warning", 6 * 3600, now + 600),        # slack 5.8 h
              alert("SAT-02", "warning", 4 * 3600, now + 3 * 3600),   # slack 1 h: more urgent
              alert("SAT-03", "watch", None, now + 600),
              alert("SAT-04", "critical", 2 * 3600, now + 60)]
    out = triage.triage(alerts, {"SAT-02": "working"}, now)
    assert [e.sat_id for e in out] == ["SAT-04", "SAT-02", "SAT-01", "SAT-03"]
    assert [e.priority for e in out] == [1.0, 2.0, 3.0, 4.0]
    assert out[1].status == "working" and out[0].status == "queued"
    assert triage.slack_s(alerts[2], now) == np.inf


# ---------------- backend bookkeeping ----------------

def test_status_flow_through_the_backend():
    sc = scenarios.build("single-stuck-load", seed=0, sat_ids=("SAT-03", "SAT-12", "SAT-17"))
    be = FleetBackend(sc)
    t, _ = demo.until_alert(be, "SAT-17")
    assert [(e.sat_id, e.status) for e in be.triage()] == [("SAT-17", "queued")]
    be.score_hypotheses("SAT-17")
    assert be.triage()[0].status == "working"
    c = be.next_contact("SAT-17", t)
    p = Plan(plan_id="P-1", sat_id="SAT-17", kind="plan", uplink=c, created_by="test",
             commands=[Command(t_exec=t + 60, name="LOAD_OFF", args={"load": "PAYLOAD"})])
    r = be.simulate(p, "all", 20)
    assert be.tier(p, r) == (2, None) and be.triage()[0].status == "awaiting_approval"
    be.execute(p)
    assert be.triage()[0].status == "executed"
    # a result the verifier did not produce never earns Tier 1
    assert be.tier(p, sim(p, margin=0.9).model_copy(update={"sim_id": "S-99"})) == (2, None)
    with pytest.raises(Exception):
        be.mark("SAT-17", "not-a-status")


# ---------------- peer comparison ----------------

PEERS = ("SAT-03", "SAT-12", "SAT-17", "SAT-21")


@pytest.fixture(scope="module")
def orbit_env():
    env, contacts, _ = scenarios.orbit_environment(PEERS, hours=8.0)
    return env


def evidence(env, world):
    world.advance_to(env.t[-1])
    return {s: scoring.evidence(s, env.t, env.illum[i], env.beta_deg[i], world.telemetry(s))
            for i, s in enumerate(env.sat_ids)}


@pytest.mark.parametrize("seed", [0, 1])
def test_peer_comparison_separates_environment_from_hardware(orbit_env, seed):
    onset = orbit_env.t[0] + 1800
    fleet = EpsWorld(orbit_env, [FaultSpec(kind="beta_season", target="FLEET", params={"extra_beta_deg": 35.0},
                                           onset_t=onset)], seed=seed, soc0=0.75)
    single = EpsWorld(orbit_env, [FaultSpec(kind="array_degradation", target="SAT-12",
                                            params={"loss": 0.4, "panel": "ALL"}, onset_t=onset)], seed=seed, soc0=0.75)
    env_case = peer_compare(evidence(orbit_env, fleet), "SAT-12")
    hw_case = peer_compare(evidence(orbit_env, single), "SAT-12")
    assert env_case.env_score > env_case.hw_score
    assert "peers_show_same_array_deficit" in env_case.explanation_keys
    assert "fleet_wide_environment" in env_case.explanation_keys
    assert hw_case.hw_score > hw_case.env_score
    assert "peers_nominal" in hw_case.explanation_keys and "own_array_output_low" in hw_case.explanation_keys
