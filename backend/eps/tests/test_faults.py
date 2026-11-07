import re
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from contracts import FAULT_KINDS, LOADS, Command, FaultSpec
from backend.eps import faults, synthetic, truth
from backend.eps.params import EpsParams

T0 = 1_794_000_000.0
ONSET = T0 + 2 * 3600
REPO = Path(__file__).resolve().parents[3]


def fault(kind, target="SAT-07", onset=ONSET, **params):
    return FaultSpec(kind=kind, target=target, params=params, onset_t=onset)


def world(fs, sat_ids=("SAT-07",), hours=6, seed=1, **kw):
    env = synthetic.environment(sat_ids, hours=hours, in_contact_all=True, **kw)
    return faults.EpsWorld(env, fs, seed=seed)


def runs(fs, sat_ids=("SAT-07",), hours=6, commands=None, seed=1, **kw):
    """(nominal, faulty) truth runs on the same environment and seed."""
    env = synthetic.environment(sat_ids, hours=hours, in_contact_all=True, **kw)
    nominal = truth.run(env, commands, seed=seed)
    faulty = truth.run(env, commands, seed=seed, mods=faults.build_modifiers(fs, env))
    return env, nominal, faulty


def k_onset(env, t=ONSET):
    return int(np.searchsorted(env.t, t))


def same_before_onset(env, a, b):
    k = k_onset(env)
    for ch in a.telemetry:
        assert np.array_equal(a.telemetry[ch][:, :k], b.telemetry[ch][:, :k], equal_nan=True), ch
    assert np.array_equal(a.soc[:, :k], b.soc[:, :k])


# ---------------- the five kinds ----------------

def test_array_degradation_hits_one_panel_after_onset():
    env, nom, bad = runs([fault("array_degradation", loss=0.5, panel="PY")])
    same_before_onset(env, nom, bad)
    k = k_onset(env)
    lit = env.illum[0, k:] > 0.99
    py = bad.p_array_avail[0, k:][lit] / nom.p_array_avail[0, k:][lit]
    assert np.allclose(py, 0.75, atol=0.01)  # one of two (nearly equal, seeded spread) wings at half output
    assert bad.soc_final[0] < nom.soc_final[0]


def test_array_degradation_ramp():
    env = synthetic.environment(("SAT-07",), hours=6, in_contact_all=True)
    env.illum = np.ones_like(env.illum)
    nom = truth.run(env, seed=1)
    bad = truth.run(env, seed=1, mods=faults.build_modifiers(
        [fault("array_degradation", loss=0.4, panel="ALL", ramp_s=3600.0)], env))
    ratio = bad.p_array_avail[0] / nom.p_array_avail[0]
    k = k_onset(env)
    assert ratio[k] == pytest.approx(1.0)
    assert ratio[k + 180] == pytest.approx(0.8, abs=1e-6)   # halfway through the ramp
    assert ratio[-1] == pytest.approx(0.6)


def test_stuck_on_load_ignores_load_off_safe_mode_and_shedding():
    cmds = {"SAT-07": [Command(t_exec=ONSET + 600, name="LOAD_OFF", args={"load": "AUX"}),
                       Command(t_exec=ONSET + 1200, name="SAFE_MODE")]}
    env, nom, bad = runs([fault("stuck_on_load", load="AUX")], commands=cmds)
    same_before_onset(env, nom, bad)
    aux = LOADS.index("AUX")
    k = k_onset(env)
    assert bad.load_on[0, aux, k:].all()
    assert not nom.load_on[0, aux].any()
    # switch telemetry shows the commanded state, the power shows the truth
    assert bad.telemetry["load_AUX"][0, -1] == 0.0
    extra = bad.p_load[0, -100:].mean() - nom.p_load[0, -100:].mean()
    assert extra == pytest.approx(EpsParams().load_power["AUX"], rel=0.1)


def test_stuck_heater_bypasses_thermostat():
    env, nom, bad = runs([fault("stuck_on_load", load="HEATER")],
                         commands={"SAT-07": [Command(t_exec=T0, name="LOAD_OFF", args={"load": "HEATER"})]})
    k = k_onset(env)
    heater_w = EpsParams().load_power["HEATER"]
    assert bad.p_load[0, k:].mean() - nom.p_load[0, k:].mean() == pytest.approx(heater_w, rel=0.1)
    assert bad.t_bat_c[0, -1] > nom.t_bat_c[0, -1] + 5


def test_capacity_fade_deepens_eclipse_discharge():
    env, nom, bad = runs([fault("capacity_fade", remaining=0.5, onset=T0)], hours=4)
    # same SOC at onset, about twice the SOC swing per eclipse
    swing_nom = nom.soc[0].max() - nom.soc[0].min()
    swing_bad = bad.soc[0].max() - bad.soc[0].min()
    assert swing_bad > 1.7 * swing_nom
    assert bad.v_bus[0].min() < nom.v_bus[0].min()


def test_capacity_fade_onset_keeps_energy_balance():
    env, nom, bad = runs([fault("capacity_fade", remaining=0.6)])
    e = {k: v[0] for k, v in bad.energy_wh.items()}
    assert e["capacity_loss"] > 0
    err = (e["array_used"] - e["load"] - (e["stored_end"] - e["stored_start"]) - e["ohmic"]
           - e["coulombic"] - e["capacity_loss"])
    assert abs(err) / e["array_used"] < 0.01
    k = k_onset(env)
    assert bad.soc[0, k] == pytest.approx(bad.soc[0, k - 1], abs=1e-3)  # SOC continuous at onset


@pytest.mark.parametrize("channel,bias", [("v_bus", 0.5), ("soc", -0.1), ("i_bat", 0.4), ("i_array_MY", -0.3)])
def test_sensor_bias_changes_reading_not_system(channel, bias):
    env, nom, bad = runs([fault("sensor_bias", channel=channel, bias=bias)])
    # the system is fine: true state identical
    for name in ("soc", "v_bus", "i_bat", "p_load", "t_bat_c", "load_on"):
        assert np.array_equal(getattr(nom, name), getattr(bad, name)), name
    k = k_onset(env)
    diff = bad.telemetry[channel][0, k:] - nom.telemetry[channel][0, k:]
    if channel == "soc":   # clipped at [0,1]
        diff = diff[(nom.telemetry["soc"][0, k:] > 0.25) & (nom.telemetry["soc"][0, k:] < 0.75)]
    assert np.allclose(diff, bias, atol=0.011)
    assert np.array_equal(bad.telemetry[channel][0, :k], nom.telemetry[channel][0, :k], equal_nan=True)


def test_beta_season_is_fleet_wide():
    sats = ("SAT-01", "SAT-07", "SAT-19")
    env, nom, bad = runs([fault("beta_season", target="FLEET", extra_beta_deg=30.0)], sat_ids=sats, beta_deg=10.0)
    k = k_onset(env)
    lit = env.illum[:, k:] > 0.99
    ratio = bad.p_array_avail[:, k:][lit] / nom.p_array_avail[:, k:][lit]
    assert np.allclose(ratio, np.cos(np.radians(40.0)) / np.cos(np.radians(10.0)))


# ---------------- validation, seeds, secrecy ----------------

@pytest.mark.parametrize("kind,params", [
    ("array_degradation", {"loss": 1.5, "panel": "PY"}),
    ("array_degradation", {"loss": 0.2, "panel": "PZ"}),
    ("stuck_on_load", {"load": "BUS"}),
    ("stuck_on_load", {}),
    ("capacity_fade", {"remaining": 0.0}),
    ("sensor_bias", {"channel": "temperature", "bias": 1.0}),
    ("beta_season", {"extra_beta_deg": "high"}),
])
def test_invalid_fault_params_rejected(kind, params):
    with pytest.raises(ValueError):
        faults.validate(FaultSpec(kind=kind, target="SAT-01", params=params, onset_t=T0))


def test_unknown_kind_rejected_by_contract():
    with pytest.raises(ValidationError):
        FaultSpec(kind="meteor", target="SAT-01", params={}, onset_t=T0)


def test_seeded_faults_deterministic_and_cover_all_kinds():
    a = [faults.seeded_fault(s, "SAT-05", T0, 86400.0) for s in range(60)]
    b = [faults.seeded_fault(s, "SAT-05", T0, 86400.0) for s in range(60)]
    assert a == b
    assert {f.kind for f in a} == set(FAULT_KINDS)
    for f in a:
        faults.validate(f)
        assert T0 <= f.onset_t <= T0 + 0.25 * 86400.0
        assert f.target == ("FLEET" if f.kind == "beta_season" else "SAT-05")
    assert faults.seeded_fault(1, "SAT-05", T0, 86400.0) != faults.seeded_fault(1, "SAT-06", T0, 86400.0)
    for kind in FAULT_KINDS:
        assert faults.seeded_fault(3, "SAT-01", T0, 86400.0, kind=kind).kind == kind


def test_same_seed_identical_telemetry_with_faults():
    fs = [faults.seeded_fault(7, "SAT-07", T0, 6 * 3600.0, kind="stuck_on_load"),
          fault("sensor_bias", channel="v_bus", bias=0.3)]
    a, b = world(fs, seed=7), world(fs, seed=7)
    for w in (a, b):
        w.advance_to(T0 + 6 * 3600)
    ta, tb = a.telemetry("SAT-07"), b.telemetry("SAT-07")
    for ch in ta:
        assert np.array_equal(ta[ch], tb[ch], equal_nan=True), ch


def test_world_telemetry_never_shows_the_future():
    w = world([fault("array_degradation", loss=0.5, panel="ALL")])
    assert len(w.telemetry("SAT-07")["t"]) == 1
    w.advance_to(T0 + 3600)
    tm = w.telemetry("SAT-07", T0, T0 + 5 * 3600)
    assert tm["t"].max() <= T0 + 3600
    with pytest.raises(ValueError):
        w.advance_to(T0)
    assert w.latest("SAT-07")["t"] == tm["t"][-1]


def test_world_execute_cannot_change_the_past():
    w = world([])
    w.advance_to(T0 + 3600)
    with pytest.raises(ValueError):
        w.execute("SAT-07", [Command(t_exec=T0, name="SAFE_MODE")])
    before = w.telemetry("SAT-07")
    w.execute("SAT-07", [Command(t_exec=T0 + 3700, name="LOAD_OFF", args={"load": "PAYLOAD"})])
    after = w.telemetry("SAT-07")
    for ch in before:
        assert np.array_equal(before[ch], after[ch], equal_nan=True), ch
    w.advance_to(T0 + 7200)
    assert w.telemetry("SAT-07")["load_PAYLOAD"][-1] == 0.0
    assert w.executed("SAT-07")[0].name == "LOAD_OFF"


def test_reveal_gives_truth():
    fs = [fault("sensor_bias", target="SAT-19", channel="soc", bias=0.1, onset=T0 + 60),
          fault("capacity_fade", target="SAT-19", remaining=0.5, onset=T0)]
    w = world(fs, sat_ids=("SAT-07", "SAT-19"))
    w.advance_to(T0 + 600)
    r = w.reveal("SAT-19")
    assert r["truth"].kind == "capacity_fade" and len(r["faults"]) == 2
    assert len(r["truth_soc"]["t"]) == len(r["truth_soc"]["soc"]) == 61
    assert w.reveal("SAT-07")["truth"] is None


def test_world_public_surface_hides_faults_and_seed():
    fs = [fault("stuck_on_load", load="AUX")]
    w = world(fs, seed=12345)
    assert "stuck" not in repr(w) and "12345" not in repr(w)
    public = [n for n in dir(w) if not n.startswith("_")]
    assert set(public) == {"sat_ids", "t_grid", "t_now", "advance_to", "execute", "executed", "telemetry",
                           "latest", "reveal"}
    for name in ("sat_ids", "t_grid", "t_now"):
        assert not isinstance(getattr(w, name), FaultSpec)
    w.advance_to(T0 + 3 * 3600)
    blob = repr(w.telemetry("SAT-07")) + repr(w.latest("SAT-07"))
    assert "stuck_on_load" not in blob and "12345" not in blob


def test_no_code_outside_eps_and_api_reaches_fault_specs():
    pattern = re.compile(r"\.reveal\(|_EpsWorld__|build_modifiers|seeded_fault|sample_fault")
    offenders = []
    for path in (REPO / "backend").rglob("*.py"):
        rel = path.relative_to(REPO / "backend").parts
        if rel[0] in ("eps", "api"):
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"only backend/eps and backend/api may touch fault specs: {offenders}"
