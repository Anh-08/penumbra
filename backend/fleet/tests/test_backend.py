"""FleetBackend (A1's Backend port on the real stack), on a three-satellite single-stuck-load."""
import numpy as np
import pytest

from contracts import Command, HypothesisScores, Plan, ScheduleRequest
from backend.fleet import demo, scenarios
from backend.fleet.backend import FleetBackend

SATS = ("SAT-03", "SAT-12", "SAT-17")


@pytest.fixture(scope="module")
def at_alert():
    sc = scenarios.build("single-stuck-load", seed=0, sat_ids=SATS)
    be = FleetBackend(sc)
    t, msgs = demo.until_alert(be, "SAT-17")
    return sc, be, t, msgs


def test_alert_reaches_the_backend(at_alert):
    sc, be, t, msgs = at_alert
    assert be.now() == t
    alerts = be.detect(t)
    assert [a.sat_id for a in alerts] == ["SAT-17"]
    tri = be.triage()
    assert tri[0].sat_id == "SAT-17" and tri[0].priority == 1.0 and tri[0].status == "queued"


def test_telemetry_and_scores(at_alert):
    sc, be, t, _ = at_alert
    tm = be.telemetry("SAT-17", t - 3600, t)
    assert tm["t"][-1] <= t and set(tm) >= {"t", "soc", "v_bus", "i_bat", "p_load", "t_bat_c", "load_AUX"}
    assert all(isinstance(x, float) for x in tm["soc"])
    sc_ = be.score_hypotheses("SAT-17")
    assert isinstance(sc_, HypothesisScores) and max(sc_.scores, key=sc_.scores.get) == "H2"


def test_schedule_never_books_the_past(at_alert):
    sc, be, t, _ = at_alert
    c = be.next_contact("SAT-17", t)
    assert c.aos < t < c.los                       # the alert fires mid-pass
    res = be.schedule([ScheduleRequest(plan_id="P-9", sat_id="SAT-17", priority=1.0, deadline_t=c.los, duration_s=60.0)])
    assert res.assignments and res.assignments[0].start_t >= t


def test_simulate_and_tier(at_alert):
    sc, be, t, _ = at_alert
    c = be.next_contact("SAT-17", t)
    p = Plan(plan_id="P-9", sat_id="SAT-17", kind="plan", uplink=c, created_by="test",
             commands=[Command(t_exec=t + 60, name="SET_PAYLOAD_DUTY", args={"duty": 0.2})])
    r = be.simulate(p, "all", 20)
    assert r.passes_all and "H2" in r.plausible
    assert be.tier(p, r) == (2, None)


def test_execute_rejects_the_past_and_runs_a_diagnostic():
    sc = scenarios.build("single-stuck-load", seed=0, sat_ids=SATS)
    be = FleetBackend(sc)
    t, _ = demo.until_alert(be, "SAT-17")
    c = be.next_contact("SAT-17", t)
    past = Plan(plan_id="P-1", sat_id="SAT-17", kind="plan", uplink=c, created_by="test",
                commands=[Command(t_exec=c.aos, name="SET_PAYLOAD_DUTY", args={"duty": 0.2})])
    with pytest.raises(ValueError):
        be.execute(past)
    diag = Plan(plan_id="P-2", sat_id="SAT-17", kind="diagnostic", uplink=c, created_by="test",
                commands=[Command(t_exec=t + 60, name="LOAD_OFF", args={"load": "AUX"}),
                          Command(t_exec=t + 120, name="LOAD_OFF", args={"load": "PAYLOAD"})])
    t_done = be.execute(diag)
    assert t_done == pytest.approx(t + 120 + 60, abs=10)
    assert be.world.executed("SAT-17")[-1].t_exec == t + 120


def test_next_eclipse_helper():
    sc = scenarios.build("single-stuck-load", seed=0, sat_ids=SATS)
    a, b = demo.next_eclipse(sc.env, "SAT-17", sc.env.t[0])
    i = sc.env.sat_ids.index("SAT-17")
    k = np.searchsorted(sc.env.t, [a, b])
    assert sc.env.illum[i, k[0]] < 0.5 <= sc.env.illum[i, k[1]] and 15 * 60 < b - a < 45 * 60
