"""T31: collision-avoidance burn coupled with power."""
import numpy as np
import pytest

from contracts import CAM_LEAD_S, Command, Contact, Plan
from backend.eps import planning, synthetic, truth
from backend.eps.maneuver import burn_profile, windows_by_sat
from backend.eps.params import EpsParams
from backend.fleet import demo, flight_rules, scenarios
from backend.fleet.backend import FleetBackend
from backend.verify import constants as K

SATS = ("SAT-03", "SAT-12", "SAT-17")


@pytest.fixture(scope="module")
def at_alert():
    sc = scenarios.build("conjunction", seed=0, sat_ids=SATS)
    be = FleetBackend(sc)
    t, msgs = demo.until_alert(be, "SAT-17")
    return sc, be, t, msgs


def maneuver(be, t, wid, pid):
    cj = be.conjunctions("SAT-17")[0]
    w = cj.window(wid)
    c = be.next_contact("SAT-17", t)
    return Plan(plan_id=pid, sat_id="SAT-17", kind="maneuver", uplink=c, created_by="test",
                commands=[Command(t_exec=max(w.t_start + 30, c.aos), name="CAM_BURN", args={"window_id": wid})])


def test_burn_profile_and_truth_load():
    env = synthetic.environment(("SAT-01",), hours=2, in_contact_all=True)
    env.illum = np.ones_like(env.illum)
    from contracts import ManeuverWindow

    t0 = float(env.t[0])
    w = ManeuverWindow(window_id="W1", t_start=t0 + 600, t_end=t0 + 1200, dv_mps=0.05, burn_s=20, power_w=45,
                       miss_after_km=2)
    burn = [Command(t_exec=t0 + 630, name="CAM_BURN", args={"window_id": "W1"})]
    extra, block = burn_profile(env.t, burn, {"W1": w}, EpsParams().cam_slew_s)
    on = extra > 0
    assert on.sum() * 10 == pytest.approx(EpsParams().cam_slew_s + 20, abs=10) and (block == on).all()
    base = truth.run(env, None, seed=0, soc0=0.6)
    r = truth.run(env, {"SAT-01": burn}, seed=0, soc0=0.6, maneuvers={"SAT-01": {"W1": w}})
    k = int(np.argmax(on))
    assert r.p_load[0, k] - base.p_load[0, k] == pytest.approx(45 - 15, abs=2)   # +45 W burn, payload (15 W) off
    e = {key: v[0] for key, v in r.energy_wh.items()}
    err = e["array_used"] - e["load"] - (e["stored_end"] - e["stored_start"]) - e["ohmic"] - e["coulombic"]
    assert abs(err) / e["array_used"] < 0.01                                     # energy balance still closes


def test_planning_carries_the_burn(at_alert):
    sc, be, t, _ = at_alert
    wmap = windows_by_sat(be.conjunctions())
    p = maneuver(be, t, "W1", "P-91")
    ctl = planning.build_controls(sc.env.t, [[[], list(p.commands)]], planning.InitialState.nominal(1),
                                  maneuvers=[wmap["SAT-17"]])
    assert ctl.extra_w[0, 0].sum() == 0 and ctl.extra_w[0, 1].max() == pytest.approx(45.0)
    assert ctl.payload_block[0, 1].any() and not ctl.payload_block[0, 0].any()


def test_conjunction_alert_at_t0_and_scenario(at_alert):
    sc, be, t, msgs = at_alert
    a = [m["data"] for m in msgs if m["type"] == "alert"][0]
    cj = be.conjunctions("SAT-17")[0]
    assert a.reason_key == "conjunction" and a.severity == "warning"          # TCA 5.2 h away: < 6 h
    assert a.time_to_critical_s == pytest.approx(cj.tca - t)
    assert "conjunction with DEB-01" in [m["data"] for m in msgs if m["type"] == "callout"][0].text
    assert [w.window_id for w in cj.windows] == ["W1", "W2", "W3"]
    assert all(w.t_end <= cj.tca - CAM_LEAD_S for w in cj.windows)
    assert be.conjunctions("SAT-03") == []


def test_verifier_power_coupled_choice(at_alert):
    sc, be, t, _ = at_alert
    results = {wid: be.simulate(maneuver(be, t, wid, f"P-{i}"), "all", 100) for i, wid in enumerate(("W1", "W2", "W3"), 1)}
    assert not results["W1"].passes_all                    # cheap delta-v, but the burn lands in eclipse at low SOC
    assert results["W3"].passes_all                        # expensive delta-v, battery charged, in sunlight
    assert results["W1"].worst_margin_soc < results["W3"].worst_margin_soc
    rk = be.rank("SAT-17")
    assert rk.status == "feasible" and rk.ordered[0] == "P-3"
    plan3 = maneuver(be, t, "W3", "P-3")
    assert be.tier(plan3, results["W3"]) == (2, None)      # a CAM is never Tier 1
    end = be.verifier.horizon_end(plan3, t)
    assert end == pytest.approx(be.conjunctions("SAT-17")[0].tca + K.ORBIT_S)


def test_flight_rules_never_auto_execute_a_burn():
    t0 = scenarios.T0
    up = Contact(sat_id="SAT-17", station_id="TROLL", aos=t0, los=t0 + 600, max_el_deg=40)
    p = Plan(plan_id="P-1", sat_id="SAT-17", kind="maneuver", uplink=up, created_by="t",
             commands=[Command(t_exec=t0 + 60, name="CAM_BURN", args={"window_id": "W1"}),
                       Command(t_exec=t0 + 60, name="SET_PAYLOAD_DUTY", args={"duty": 0.2})])
    from backend.fleet.tests.test_tier_triage import sim

    ctx = flight_rules.RuleContext(now=t0 + 30, commanded_duty=1.0)
    assert flight_rules.tier(p, sim(p, margin=0.9), ctx) == (2, None)


def test_alert_clears_after_the_burn_executes():
    sc = scenarios.build("conjunction", seed=0, sat_ids=SATS)
    be = FleetBackend(sc)
    t, _ = demo.until_alert(be, "SAT-17")
    plan = maneuver(be, t, "W3", "P-3")
    be.advance_to(plan.uplink.aos)
    be.execute(plan)
    assert not [a for a in be.detect(be.now()) if a.reason_key == "conjunction"]
