import numpy as np
import pytest

from contracts import HYPOTHESES, Command, FaultSpec, HypothesisScores
from backend.eps import synthetic
from backend.eps.faults import EpsWorld, validate
from backend.verify import constants as K
from backend.verify import diagnostics, scoring

T0 = 1_794_000_000.0
FLEET = ("SAT-07", "SAT-12", "SAT-19")


def make_env(hours=8.0, beta=20.0):
    return synthetic.environment(FLEET, hours=hours, beta_deg=beta, contact_every_s=5400.0, contact_len_s=600.0)


def fleet_scores(world, env, commands=None):
    evs = {s: scoring.evidence(s, env.t, env.illum[i], env.beta_deg[i], world.telemetry(s),
                               commands=(commands or {}).get(s, []))
           for i, s in enumerate(env.sat_ids)}
    return scoring.score_fleet(evs)


def test_passes_split_on_gaps():
    seen = np.array([0, 1, 1, 0, 0, 1, 0, 1, 1, 1], bool)
    assert scoring.passes(seen) == [(1, 3), (5, 6), (7, 10)]
    assert scoring.passes(np.zeros(4, bool)) == []


def test_candidate_grid_covers_every_hypothesis_with_valid_params():
    grid = scoring.candidate_grid()
    assert {c.hyp for c in grid} == set(HYPOTHESES)
    for c in grid:
        if c.hyp != "H0":
            validate(FaultSpec(kind=scoring.FAULT_OF[c.hyp], target="SAT-01", params=c.params, onset_t=T0))
    h0 = [c for c in grid if c.hyp == "H0"]
    assert len(h0) == len(K.H0_CAPACITY_SCALE) * len(K.H0_ARRAY_SCALE)


def test_no_downlink_gives_the_prior():
    env = make_env(hours=1)
    tm = {"t": env.t[:1], **{ch: np.array([np.nan]) for ch in ("soc", "v_bus", "i_bat", "p_load", "t_bat_c")}}
    ev = scoring.evidence("SAT-07", env.t, env.illum[0], 20.0, tm)
    sc = scoring.posterior(ev)
    assert sc.posterior == pytest.approx(K.PRIOR)


def test_healthy_fleet_scores_nominal():
    env = make_env()
    w = EpsWorld(env, [], seed=1003)
    w.advance_to(env.t[-1])
    for s, sc in fleet_scores(w, env).items():
        assert sc.top == "H0", (s, sc.posterior)
        assert "H0" in sc.plausible


def test_posterior_shape_and_contract():
    env = make_env(hours=4)
    w = EpsWorld(env, [FaultSpec(kind="stuck_on_load", target="SAT-07", params={"load": "AUX"}, onset_t=T0)], seed=2)
    w.advance_to(env.t[-1])
    sc = fleet_scores(w, env)["SAT-07"]
    assert set(sc.posterior) == set(HYPOTHESES)
    assert sum(sc.posterior.values()) == pytest.approx(1.0)
    assert sc.top in sc.plausible
    assert all(sc.posterior[h] >= K.PLAUSIBLE_MIN or h == sc.top for h in sc.plausible)
    c = sc.to_contract()
    assert isinstance(c, HypothesisScores) and c.plausible == sc.plausible
    assert sc.top == "H2" and sc.map_params["H2"]["load"] == "AUX"
    assert sc.labels["H2"] == "Stuck-on load"


def test_handles_gaps_inside_a_pass():
    env = make_env(hours=6)
    w = EpsWorld(env, [FaultSpec(kind="stuck_on_load", target="SAT-07", params={"load": "AUX"}, onset_t=T0)], seed=2)
    w.advance_to(env.t[-1])
    tm = w.telemetry("SAT-07")
    seen = np.nonzero(np.isfinite(tm["soc"]))[0]
    for ch in tm:
        if ch != "t":
            tm[ch][seen[10:25]] = np.nan   # a dropout in the middle of the first pass
    ev = scoring.evidence("SAT-07", env.t, env.illum[0], 20.0, tm)
    assert scoring.posterior(ev).top == "H2"


def test_beta_season_needs_the_whole_fleet():
    """One satellite losing array power with healthy peers is H1; the whole fleet losing it is H5."""
    env = make_env(hours=8)
    onset = T0 + 1800
    single = EpsWorld(env, [FaultSpec(kind="array_degradation", target="SAT-07",
                                      params={"loss": 0.3, "panel": "ALL"}, onset_t=onset)], seed=5)
    fleet = EpsWorld(env, [FaultSpec(kind="beta_season", target="FLEET",
                                     params={"extra_beta_deg": 30.0}, onset_t=onset)], seed=5)
    for w in (single, fleet):
        w.advance_to(env.t[-1])
    assert fleet_scores(single, env)["SAT-07"].top == "H1"
    assert fleet_scores(fleet, env)["SAT-07"].top == "H5"


def test_diagnostic_updates_the_posterior():
    """A stuck PAYLOAD looks nominal until the diagnostic commands it off."""
    env = make_env(hours=8)
    w = EpsWorld(env, [FaultSpec(kind="stuck_on_load", target="SAT-07", params={"load": "PAYLOAD"},
                                 onset_t=T0 + 600)], seed=4)
    runs = scoring.passes(env.in_contact[0])
    a, b = runs[2]
    w.advance_to(env.t[a - 1])
    pre = fleet_scores(w, env)["SAT-07"]
    diag = diagnostics.standard_diagnostic(env.t[a], env.t[b - 1])
    w.execute("SAT-07", diag)
    w.advance_to(env.t[b - 1])
    post = fleet_scores(w, env, {"SAT-07": diag})["SAT-07"]
    assert pre.top != "H2"
    assert post.top == "H2" and post.posterior["H2"] > 0.9
    assert post.map_params["H2"]["load"] == "PAYLOAD"


def test_standard_diagnostic_fits_one_contact():
    cmds = diagnostics.standard_diagnostic(T0, T0 + 600)
    assert all(T0 <= c.t_exec <= T0 + 600 for c in cmds)
    assert {c.name for c in cmds} >= {"LOAD_OFF", "LOAD_ON", "SET_PAYLOAD_DUTY"}
    assert all(isinstance(c, Command) for c in cmds)
    with pytest.raises(ValueError):
        diagnostics.standard_diagnostic(T0, T0 + 200)
