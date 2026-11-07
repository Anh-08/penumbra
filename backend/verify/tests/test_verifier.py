import numpy as np
import pytest

from contracts import HYPOTHESES, Command, Contact, Plan, RankResult, SimResult, Trajectory
from backend.eps import planning, synthetic
from backend.verify import constants as K
from backend.verify.verifier import Belief, Verifier, belief_from, is_safe_mode

T0 = 1_794_000_000.0
SAT = "SAT-07"


@pytest.fixture(scope="module")
def env():
    return synthetic.environment((SAT,), hours=24, t0=T0, beta_deg=20.0, contact_every_s=5400.0, contact_len_s=600.0)


def uplink(env, after):
    m = env.in_contact[0]
    k = int(np.argmax(m & (env.t > after)))
    k1 = k + int(np.argmin(m[k:]))
    return Contact(sat_id=SAT, station_id="SVALBARD", aos=float(env.t[k]), los=float(env.t[k1 - 1]), max_el_deg=40)


def make_belief(plausible, params=None, soc=0.85, duty=0.8, t_state=T0 + 3 * 3600):
    init = planning.InitialState.nominal(1, soc=soc, params=planning.planning_params(initial_payload_duty=duty))
    return Belief(sat_id=SAT, t_state=t_state, soc=soc, t_bat_c=12.0, commands=[], initial=init,
                  plausible=list(plausible), hyp_params=params or {})


def verifier(env, belief):
    return Verifier(env, lambda s: belief)


def plan(env, pid, *cmds, kind="plan", after=T0 + 3 * 3600):
    up = uplink(env, after)
    return Plan(plan_id=pid, sat_id=SAT, kind=kind, uplink=up, created_by="test",
                commands=[Command(t_exec=up.aos + 30 + 60 * i, name=n, args=a) for i, (n, a) in enumerate(cmds)])


STUCK = {"H2": {"load": "AUX"}}
AUX_OFF = ("LOAD_OFF", {"load": "AUX"})
DUTY_LOW = ("SET_PAYLOAD_DUTY", {"duty": 0.2})
SAFE = ("SAFE_MODE", {})


# ---------------- CLAUDE.md section 8: verifier ----------------

def test_plan_draining_under_a_plausible_hypothesis_is_never_feasible(env):
    v = verifier(env, make_belief(["H0", "H2"], STUCK))
    r = v.simulate(plan(env, "P-1", AUX_OFF))
    assert r.per_hyp["H0"].pass_rate >= K.PASS_RATE_REQUIRED      # fine if nothing is stuck
    assert r.per_hyp["H2"].pass_rate < K.PASS_RATE_REQUIRED       # drains if AUX is stuck
    assert not r.passes_all and r.worst_margin_soc < 0
    assert v.rank(SAT).status == "infeasible"                     # it is the only plan
    v.simulate(plan(env, "P-2", DUTY_LOW))
    rk = v.rank(SAT)
    assert rk.status == "feasible" and rk.ordered == ["P-2", "P-1"]


def test_plan_hash_changes_when_any_command_changes(env):
    v = verifier(env, make_belief(["H0"]))
    a = plan(env, "P-1", DUTY_LOW)
    r = v.simulate(a)
    assert r.plan_hash == a.plan_hash
    changed = [plan(env, "P-1", ("SET_PAYLOAD_DUTY", {"duty": 0.3})), plan(env, "P-1", AUX_OFF),
               plan(env, "P-1", DUTY_LOW, AUX_OFF), plan(env, "P-1", DUTY_LOW, kind="diagnostic"),
               Plan(**{**a.model_dump(), "commands": [Command(t_exec=a.commands[0].t_exec + 10, name="SET_PAYLOAD_DUTY",
                                                               args={"duty": 0.2})]})]
    for b in changed:
        assert b.plan_hash != a.plan_hash
        assert v.latest("P-1").plan_hash != b.plan_hash   # an approval with the old hash would be stale


def test_nothing_feasible_means_infeasible_with_safe_mode():
    # 60 % of the array gone (40 % of each orbit in shadow) from SOC 0.6: cutting the payload to 0.2
    # still drains it, only SAFE_MODE survives (margins about -0.30 vs +0.22, found by a parameter scan)
    env = synthetic.environment((SAT,), hours=24, t0=T0, beta_deg=0.0, contact_every_s=5400.0, contact_len_s=600.0)
    b = make_belief(["H1"], {"H1": {"loss": 0.6, "panel": "ALL"}}, soc=0.6, duty=1.0)
    v = verifier(env, b)
    rd = v.simulate(plan(env, "P-1", DUTY_LOW))
    rs = v.simulate(plan(env, "P-2", SAFE))
    assert not rd.passes_all and rs.passes_all
    rk = v.rank(SAT)
    assert rk.status == "infeasible" and rk.ordered[0] == "P-2"


def test_no_passing_plan_at_all_is_infeasible(env):
    v = verifier(env, make_belief(["H2"], STUCK, soc=0.35))
    v.simulate(plan(env, "P-1", AUX_OFF))
    assert v.rank(SAT).status == "infeasible"


# ---------------- contract shape and behavior ----------------

def test_simresult_matches_contract_and_ids_increment(env):
    v = verifier(env, make_belief(["H0", "H2"], STUCK))
    r1 = v.simulate(plan(env, "P-1", DUTY_LOW))
    r2 = v.simulate(plan(env, "P-2", AUX_OFF), hypotheses=["H0"], monte_carlo=20)
    assert isinstance(r1, SimResult) and r1.sim_id == "S-1" and r2.sim_id == "S-2"
    assert set(r1.per_hyp) == set(HYPOTHESES)
    assert set(r2.per_hyp) == {"H0", "H2"}           # plausible H2 is always simulated
    assert r1.plausible == ["H0", "H2"] and r1.n_commands == 1
    for s in r1.per_hyp.values():
        assert 0 <= s.pass_rate <= 1 and s.t_bat_min_c <= s.t_bat_max_c


def test_passes_all_only_counts_plausible_hypotheses(env):
    v = verifier(env, make_belief(["H0"]))
    r = v.simulate(plan(env, "P-1", AUX_OFF), hypotheses="all")
    assert r.per_hyp["H2"].pass_rate < K.PASS_RATE_REQUIRED    # would fail if AUX were stuck...
    assert r.passes_all                                        # ...but H2 is not plausible


def test_monte_carlo_default_and_determinism(env):
    v = verifier(env, make_belief(["H0", "H2"], STUCK))
    p = plan(env, "P-1", DUTY_LOW)
    a, b = v.simulate(p), v.simulate(p)
    assert K.MONTE_CARLO_N == 100
    assert a.per_hyp == b.per_hyp and a.sim_id != b.sim_id
    assert set(K.MC_SIGMA) == {"array_efficiency", "capacity", "load_power", "initial_soc", "soc_sensor_bias"}


def test_safe_mode_ranks_after_other_passing_plans(env):
    v = verifier(env, make_belief(["H0"]))
    v.simulate(plan(env, "P-1", SAFE))
    v.simulate(plan(env, "P-2", DUTY_LOW))
    v.simulate(plan(env, "P-3", ("SET_PAYLOAD_DUTY", {"duty": 0.5})))
    rk = v.rank(SAT)
    assert isinstance(rk, RankResult) and rk.status == "feasible"
    assert rk.ordered[-1] == "P-1"
    # among the others: higher margin first (duty 0.2 keeps more SOC than duty 0.5)
    assert rk.ordered[:2] == ["P-2", "P-3"]
    assert is_safe_mode(v._plans["P-1"])


def test_fewer_commands_break_ties(env):
    v = verifier(env, make_belief(["H0"]))
    v.simulate(plan(env, "P-1", DUTY_LOW, DUTY_LOW))
    v.simulate(plan(env, "P-2", DUTY_LOW))
    r1, r2 = v.latest("P-1"), v.latest("P-2")
    if abs(r1.worst_margin_soc - r2.worst_margin_soc) < 1e-12 and abs(r1.payload_uptime_worst - r2.payload_uptime_worst) < 1e-12:
        assert v.rank(SAT).ordered[0] == "P-2"


def test_diagnostic_judged_over_its_own_window(env):
    v = verifier(env, make_belief(["H2"], STUCK, soc=0.6))
    diag = plan(env, "P-1", ("LOAD_OFF", {"load": "PAYLOAD"}), ("LOAD_ON", {"load": "PAYLOAD"}), kind="diagnostic")
    full = plan(env, "P-2", ("LOAD_OFF", {"load": "PAYLOAD"}), ("LOAD_ON", {"load": "PAYLOAD"}))
    rd, rf = v.simulate(diag), v.simulate(full)
    assert rd.passes_all and not rf.passes_all      # safe for its window, does not fix the 24 h drain
    end = v.horizon_end(diag, T0 + 3 * 3600)
    assert end == pytest.approx(diag.commands[-1].t_exec + K.DIAGNOSTIC_DWELL_S + K.ORBIT_S)


def test_trajectory_for_drawing(env):
    v = verifier(env, make_belief(["H0", "H2"], STUCK))
    v.simulate(plan(env, "P-1", DUTY_LOW), monte_carlo=30)
    tr = v.trajectory("P-1", "H2")
    assert isinstance(tr, Trajectory) and len(tr.t) == len(tr.soc_p5) == len(tr.soc_p95)
    assert np.all(np.diff(tr.t) == pytest.approx(K.TRAJECTORY_STEP_S))
    assert np.all(np.array(tr.soc_p5) <= np.array(tr.soc_p50) + 1e-12)
    assert np.all(np.array(tr.soc_p50) <= np.array(tr.soc_p95) + 1e-12)
    with pytest.raises(KeyError):
        v.trajectory("P-9", "H0")


def test_belief_from_telemetry():
    init = planning.InitialState.nominal(1)
    tm = {"t": np.array([T0, T0 + 10, T0 + 20]), "soc": np.array([np.nan, 0.8, np.nan]),
          "t_bat_c": np.array([np.nan, 11.0, np.nan])}
    b = belief_from(SAT, tm, [], init)
    assert b.t_state == T0 + 10 and b.soc == 0.8 and b.plausible == ["H0"]
    with pytest.raises(ValueError):
        belief_from(SAT, {"t": tm["t"], "soc": np.full(3, np.nan), "t_bat_c": np.full(3, np.nan)}, [], init)


def test_runs_on_real_orbit_inputs():
    """B2's OrbitModel (cached OMM + DE421, offline) feeds the verifier the same way as the synthetic orbit."""
    from backend.eps.truth import Environment, contact_mask
    from backend.orbit import OrbitModel, load_fleet

    assets = load_fleet()
    model = OrbitModel(assets, t0=T0, horizon_s=6 * 3600)
    contacts = [c for c in model.contacts() if c.sat_id == SAT]
    env = Environment(t=model.t, sat_ids=(SAT,), illum=model.illumination(SAT)[None, :],
                      in_contact=contact_mask(model.t, contacts, (SAT,)), beta_deg=np.array([model.beta_deg(SAT)]))
    assert contacts, "need at least one contact in 6 h"
    up = contacts[0]
    b = make_belief(["H0"], t_state=float(model.t[0]))
    v = Verifier(env, lambda s: b)
    p = Plan(plan_id="P-1", sat_id=SAT, kind="plan", uplink=up, created_by="test",
             commands=[Command(t_exec=up.aos + 30, name="SET_PAYLOAD_DUTY", args={"duty": 0.2})])
    r = v.simulate(p, monte_carlo=10)
    assert r.passes_all and 0 < r.per_hyp["H0"].min_soc_p5 <= 1
