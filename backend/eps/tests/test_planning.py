import logging

import numpy as np
import pytest

from contracts import ESSENTIAL_LOADS, HYPOTHESES, LOADS, Command, FaultSpec
from backend.eps import faults, planning, synthetic, truth
from backend.eps.params import PLANNING, PLANNING_DIFFERENCES, TRUTH

T0 = 1_794_000_000.0


def c(t, name, **args):
    return Command(t_exec=t, name=name, args=args)


@pytest.fixture(scope="module")
def env():
    return synthetic.environment(("SAT-07", "SAT-12"), hours=6, beta_deg=20.0)


def batch(env, plans, hyps=None, perturb=None, **kw):
    s = len(env.sat_ids)
    init = planning.InitialState.nominal(s)
    return planning.simulate_batch(env.t, env.illum, env.beta_deg[:, 0], plans, init, hyps, perturb, **kw)


PLANS = [[], [c(T0, "LOAD_OFF", load="AUX")], [c(T0, "SET_PAYLOAD_DUTY", duty=0.1)], [c(T0, "SAFE_MODE")]]


def test_batch_shape_and_no_time_history(env):
    hy = planning.Hypotheses.build([{}, {}])
    r = batch(env, [PLANS, PLANS[:2]], hy, planning.Perturbation(n_mc=7))
    shape = (2, len(HYPOTHESES), len(PLANS), 7)
    for name in ("min_soc", "min_v_bus", "t_bat_min_c", "t_bat_max_c", "payload_uptime", "soc_end", "t_soc_below"):
        assert getattr(r, name).shape == shape, name
    assert r.soc_history is None
    assert r.hyp_names == HYPOTHESES
    assert r.n_commands.tolist() == [[0, 1, 1, 1], [0, 1, 0, 0]]  # SAT-12 padded with no-op plans


def test_float32_option(env):
    hy = planning.Hypotheses.build([{}, {}])
    a = batch(env, [PLANS, PLANS], hy)
    b = batch(env, [PLANS, PLANS], hy, dtype=np.float32)
    assert b.min_soc.dtype == np.float32 and b.min_v_bus.dtype == np.float32
    assert np.allclose(a.min_soc, b.min_soc, atol=1e-4)
    assert np.allclose(a.min_v_bus, b.min_v_bus, atol=1e-3)


def test_logs_evals_per_second(env, caplog):
    with caplog.at_level(logging.INFO, logger="penumbra.eps.planning"):
        r = batch(env, [PLANS, PLANS])
    assert r.evals_per_s > 0  # no speed floor here: CI runners vary; fleet-scale numbers are in progress/B1.md
    assert any("evals/s" in m for m in caplog.messages)


def test_parameters_deliberately_mismatched():
    for key in ("cell_capacity_ah", "ocv_cell_v", "r0_cell_ohm", "r_temp_b", "coulombic_eff"):
        assert key in PLANNING_DIFFERENCES
    for key in PLANNING_DIFFERENCES:
        assert PLANNING[key].source != TRUTH[key].source, key
    assert planning.planning_params().capacity_ah != pytest.approx(
        TRUTH["cell_capacity_ah"].value * TRUTH["n_parallel"].value)


def test_nominal_plan_tracks_truth(env):
    tr = truth.run(env, seed=1)
    r = batch(env, [[[]], [[]]])
    assert np.allclose(r.min_soc[:, 0, 0, 0], tr.soc.min(axis=1), atol=0.03)
    assert np.allclose(r.soc_end[:, 0, 0, 0], tr.soc_final, atol=0.05)
    assert np.allclose(r.t_bat_max_c[:, 0, 0, 0], tr.t_bat_c.max(axis=1), atol=2.0)
    assert np.allclose(r.payload_uptime[:, 0, 0, 0], tr.payload_uptime, atol=1e-6)


def test_hypothesis_matches_truth_with_that_fault():
    env1 = synthetic.environment(("SAT-07",), hours=6, beta_deg=20.0)
    cases = [
        ("H2", FaultSpec(kind="stuck_on_load", target="SAT-07", params={"load": "AUX"}, onset_t=T0)),
        ("H3", FaultSpec(kind="capacity_fade", target="SAT-07", params={"remaining": 0.55}, onset_t=T0)),
        ("H1", FaultSpec(kind="array_degradation", target="SAT-07", params={"loss": 0.5, "panel": "ALL"}, onset_t=T0)),
    ]
    for h, f in cases:
        tr = truth.run(env1, seed=1, mods=faults.build_modifiers([f], env1))
        hy = planning.Hypotheses.build([{h: f.params}], names=("H0", h))
        r = batch(env1, [[[]]], hy)
        # The mapping is right if planning lands near the faulty truth. Measured gap (planning - truth)
        # is +0.01..+0.04 SOC, largest under deep fade (datasheet 2.0 Ah vs PCoE 1.91 Ah); the verifier's
        # Monte Carlo sigma on capacity must cover it (T12).
        assert r.min_soc[0, 1, 0, 0] == pytest.approx(tr.soc[0].min(), abs=0.05), h
        assert r.min_soc[0, 1, 0, 0] < r.min_soc[0, 0, 0, 0], h


def test_stuck_load_makes_load_off_useless_but_duty_still_helps(env):
    hy = planning.Hypotheses.build([{"H2": {"load": "AUX"}}] * 2)
    plans = [[[c(T0, "LOAD_ON", load="AUX")], [c(T0, "LOAD_ON", load="AUX"), c(T0 + 60, "LOAD_OFF", load="AUX")],
              [c(T0, "LOAD_ON", load="AUX"), c(T0 + 60, "SET_PAYLOAD_DUTY", duty=0.0)]]] * 2
    r = batch(env, plans, hy)
    h0, h2 = 0, HYPOTHESES.index("H2")
    assert r.min_soc[0, h0, 1, 0] > r.min_soc[0, h0, 0, 0] + 0.01          # under H0, LOAD_OFF helps
    assert r.min_soc[0, h2, 1, 0] == pytest.approx(r.min_soc[0, h2, 0, 0])  # under H2 it does nothing
    assert r.min_soc[0, h2, 2, 0] > r.min_soc[0, h2, 0, 0] + 0.01          # cutting payload still helps


def test_safe_mode_maximizes_margin_and_zeroes_uptime(env):
    r = batch(env, [PLANS, PLANS], planning.Hypotheses.build([{}, {}]))
    safe = len(PLANS) - 1
    assert np.all(r.min_soc[:, :, safe] >= r.min_soc[:, :, 0] - 1e-9)
    assert np.allclose(r.payload_uptime[:, :, safe], 0.0, atol=1e-3)
    assert np.all(r.payload_uptime[:, :, 2] < r.payload_uptime[:, :, 0])


def test_controls_follow_commands_and_keep_essentials_on(env):
    init = planning.InitialState.nominal(1)
    t_off = T0 + 1800
    ctl = planning.build_controls(env.t, [[[c(t_off, "SAFE_MODE"), c(t_off + 600, "LOAD_ON", load="PAYLOAD")]]], init)
    k = int(np.searchsorted(env.t, t_off))
    pay = LOADS.index("PAYLOAD")
    assert ctl.switch[0, 0, pay, k - 1] and not ctl.switch[0, 0, pay, k]
    assert ctl.switch[0, 0, pay, k + 60]
    assert ctl.duty[0, 0, k] == 0.0
    ess = [LOADS.index(x) for x in ESSENTIAL_LOADS]
    assert ctl.switch[:, :, ess].all()


def test_monte_carlo_perturbations_spread_and_order(env):
    caps = np.linspace(0.8, 1.2, 9).reshape(1, 1, 1, 9)
    r = batch(env, [[[]], [[]]], perturb=planning.Perturbation(capacity_factor=caps, n_mc=9))
    ms = r.min_soc[0, 0, 0]
    assert np.all(np.diff(ms) > 0)  # more capacity, shallower discharge
    soc0 = planning.Perturbation(soc0_offset=np.array([-0.1, 0.0, 0.1]).reshape(1, 1, 1, 3), n_mc=3)
    r = batch(env, [[[]], [[]]], perturb=soc0)
    assert np.all(np.diff(r.min_soc[0, 0, 0]) > 0)


def test_time_below_threshold(env):
    r = batch(env, [[[]], [[]]], soc_threshold=0.85)
    assert np.isfinite(r.t_soc_below).all()
    assert (r.t_soc_below >= T0).all() and (r.t_soc_below <= env.t[-1]).all()
    assert np.isnan(batch(env, [[[]], [[]]]).t_soc_below).all()


def test_soc_history_only_on_request(env):
    r = batch(env, [PLANS, PLANS], perturb=planning.Perturbation(n_mc=2), record_soc=True)
    assert r.soc_history.shape == (2, 1, len(PLANS), 2, len(env.t))
    assert np.allclose(r.soc_history.min(axis=-1), r.min_soc, atol=1e-3)


def test_batch_equals_per_satellite_runs(env):
    hy = planning.Hypotheses.build([{"H1": {"loss": 0.3, "panel": "PY"}}, {}])
    both = batch(env, [PLANS, PLANS], hy)
    for i in range(2):
        e = truth.Environment(t=env.t, sat_ids=env.sat_ids[i:i + 1], illum=env.illum[i:i + 1],
                              in_contact=env.in_contact[i:i + 1], beta_deg=env.beta_deg[i:i + 1, 0])
        hy1 = planning.Hypotheses.build([[{"H1": {"loss": 0.3, "panel": "PY"}}, {}][i]])
        one = batch(e, [PLANS], hy1)
        assert np.allclose(one.min_soc[0], both.min_soc[i])


def test_soc_sensor_bias_means_less_charge_than_reported():
    hy = planning.Hypotheses.build([{"H4": {"channel": "soc", "bias": 0.1}}])
    assert hy.soc_offset[0, HYPOTHESES.index("H4")] == pytest.approx(-0.1)
    hy = planning.Hypotheses.build([{"H4": {"channel": "v_bus", "bias": 0.5}}])
    assert hy.soc_offset[0, HYPOTHESES.index("H4")] == 0.0
