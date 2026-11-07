import numpy as np
import pytest

from contracts import ESSENTIAL_LOADS, LOADS, Command, Contact
from backend.eps import physics, synthetic, truth
from backend.eps.params import EpsParams

T0 = 1_794_000_000.0
FLEET = ("SAT-01", "SAT-07", "SAT-12", "SAT-19")


def cmd(t, name, **args):
    return Command(t_exec=t, name=name, args=args)


def drain_commands(sat_ids, t=T0):
    return {s: [cmd(t, "SET_PAYLOAD_DUTY", duty=1.0), cmd(t, "LOAD_ON", load="AUX"),
                cmd(t, "SET_HEATER_SETPOINT", setpoint_c=20.0)] for s in sat_ids}


def imbalance(r, i):
    e = {k: v[i] for k, v in r.energy_wh.items()}
    stored = e["stored_end"] - e["stored_start"]
    return e["array_used"] - e["load"] - stored - e["ohmic"] - e["coulombic"] - e["capacity_loss"], e["array_used"]


# ---------------- CLAUDE.md section 8: EPS ----------------

def test_soc_stays_in_unit_interval():
    # hard drain in permanent shadow from a low start: SOC hits the floor even after load shedding
    env = synthetic.environment(FLEET, hours=12, in_contact_all=True)
    env.illum = np.zeros_like(env.illum)
    r = truth.run(env, drain_commands(FLEET), seed=3, soc0=0.25)
    assert r.soc.min() >= 0.0 and r.soc.max() <= 1.0 and r.soc_final.min() >= 0.0
    assert r.soc.min() == 0.0  # the scenario really reaches empty
    assert r.brownout.any()
    tm = r.telemetry["soc"]
    assert np.nanmin(tm) >= 0.0 and np.nanmax(tm) <= 1.0

    # permanent sun, light load, nearly full: never above 1
    env = synthetic.environment(FLEET, hours=6, in_contact_all=True)
    env.illum = np.ones_like(env.illum)
    off = {s: [cmd(T0, "LOAD_OFF", load="PAYLOAD")] for s in FLEET}
    r = truth.run(env, off, seed=3, soc0=0.995)
    assert r.soc.max() <= 1.0 and r.soc_final.max() <= 1.0
    assert r.energy_wh["shunt"].min() > 0  # surplus was shunted, not stored


@pytest.mark.parametrize("soc0", [0.5, 0.95])
def test_energy_balance_one_orbit_constant_load(soc0):
    period = synthetic.period_s(500.0)
    env = synthetic.environment(("SAT-03",), hours=period / 3600.0, in_contact_all=True)
    heater_off = {"SAT-03": [cmd(T0, "LOAD_OFF", load="HEATER")]}
    r = truth.run(env, heater_off, seed=11, soc0=soc0)
    assert np.ptp(r.p_load[0]) < 1e-9  # constant load
    assert not r.brownout.any()
    err, e_in = imbalance(r, 0)
    assert abs(err) / e_in < 0.01
    assert r.energy_wh["array_used"][0] > 0 and r.energy_wh["load"][0] > 0


def test_same_seed_identical_telemetry():
    env = synthetic.environment(FLEET, hours=6)
    cmds = {"SAT-07": [cmd(T0 + 3600, "LOAD_OFF", load="PAYLOAD")]}
    a = truth.run(env, cmds, seed=42)
    b = truth.run(env, cmds, seed=42)
    for ch in a.telemetry:
        assert np.array_equal(a.telemetry[ch], b.telemetry[ch], equal_nan=True), ch
    c = truth.run(env, cmds, seed=43)
    assert not np.array_equal(a.telemetry["v_bus"], c.telemetry["v_bus"], equal_nan=True)


def test_satellite_independent_of_fleet_and_horizon():
    full = truth.run(synthetic.environment(FLEET, hours=6), seed=5)
    alone_env = synthetic.environment(FLEET, hours=3)
    # same illumination/contacts for SAT-12 alone, shorter horizon
    i = FLEET.index("SAT-12")
    env = truth.Environment(t=alone_env.t, sat_ids=("SAT-12",), illum=alone_env.illum[i:i + 1],
                            in_contact=alone_env.in_contact[i:i + 1], beta_deg=np.zeros(1))
    alone = truth.run(env, seed=5)
    n = len(env.t)
    for ch, x in alone.telemetry.items():
        assert np.array_equal(x[0], full.telemetry[ch][i, :n], equal_nan=True), ch


def test_essential_loads_never_off():
    env = synthetic.environment(FLEET, hours=12, in_contact_all=True)
    env.illum = np.where(env.t[None, :] < T0 + 4 * 3600, env.illum, 0.0)   # sun lost after 4 h: drain and shed
    cmds = drain_commands(FLEET)
    cmds["SAT-19"].append(cmd(T0 + 7200, "SAFE_MODE"))
    r = truth.run(env, cmds, seed=9, soc0=0.3)
    ess = [LOADS.index(x) for x in ESSENTIAL_LOADS]
    assert r.load_cmd[:, ess].all() and r.load_on[:, ess].all()
    for name in ESSENTIAL_LOADS:
        x = r.telemetry[f"load_{name}"]
        assert np.all(x[~np.isnan(x)] == 1.0)
    # the scenario really exercised undervoltage shedding of the non-essential loads
    assert r.shed.any()
    rows, cols = np.nonzero(r.shed)
    k = cols[0]
    assert not r.load_cmd[rows[0], LOADS.index("PAYLOAD"), k + 1]


def test_essential_load_off_is_rejected_before_reaching_the_sim():
    with pytest.raises(ValueError):
        cmd(T0, "LOAD_OFF", load="COMMS")


# ---------------- models and behavior ----------------

def test_telemetry_nan_outside_contacts_and_quantized():
    env = synthetic.environment(("SAT-02",), hours=6)
    r = truth.run(env, seed=1)
    mask = env.in_contact[0]
    assert 0 < mask.sum() < len(mask)
    sens = EpsParams().sensors
    for ch, x in r.telemetry.items():
        assert np.isnan(x[0, ~mask]).all(), ch
        assert np.isfinite(x[0, mask]).all(), ch
        if not ch.startswith("load_"):
            lsb = sens["i_array" if ch.startswith("i_array_") else ch][1]
            q = x[0, mask] / lsb
            assert np.allclose(q, np.round(q), atol=1e-6), ch
    # noise and bias are applied: readings differ from the truth
    assert not np.allclose(r.telemetry["v_bus"][0, mask], r.v_bus[0, mask])
    assert np.nanmax(np.abs(r.telemetry["v_bus"][0] - r.v_bus[0])) < 0.2


def test_contact_mask_from_contacts():
    t = T0 + np.arange(0, 1000, 10.0)
    contacts = [Contact(sat_id="SAT-05", station_id="TROLL", aos=T0 + 100, los=T0 + 200, max_el_deg=30)]
    m = truth.contact_mask(t, contacts, ("SAT-04", "SAT-05"))
    assert not m[0].any() and m[1].sum() == 11


def test_load_commands_take_effect_at_t_exec():
    env = synthetic.environment(("SAT-01",), hours=2, in_contact_all=True)
    t_off = T0 + 1800
    r = truth.run(env, {"SAT-01": [cmd(t_off, "LOAD_OFF", load="PAYLOAD"),
                                   cmd(T0, "LOAD_OFF", load="HEATER")]}, seed=2)
    k = int(np.searchsorted(env.t, t_off))
    before, after = r.p_load[0, k - 1], r.p_load[0, k]
    p = EpsParams()
    assert before - after == pytest.approx(p.load_power["PAYLOAD"] * p.initial_payload_duty, rel=0.1)
    assert r.telemetry["load_PAYLOAD"][0, k - 1] == 1 and r.telemetry["load_PAYLOAD"][0, k] == 0


def test_safe_mode_sheds_payload_and_aux():
    env = synthetic.environment(("SAT-01",), hours=1, in_contact_all=True)
    r = truth.run(env, {"SAT-01": [cmd(T0, "LOAD_ON", load="AUX"), cmd(T0 + 600, "SAFE_MODE")]}, seed=2)
    k = int(np.searchsorted(env.t, T0 + 600))
    assert r.load_on[0, LOADS.index("AUX"), k - 1]
    assert not r.load_on[0, LOADS.index("AUX"), k] and not r.load_on[0, LOADS.index("PAYLOAD"), k]
    assert r.heater_setpoint_c[0, k] == EpsParams().safe_heater_setpoint_c
    assert r.payload_uptime[0] < 0.1


def test_heater_thermostat_holds_setpoint_and_costs_power():
    env = synthetic.environment(("SAT-01",), hours=8, in_contact_all=True)
    base = truth.run(env, seed=4)
    warm = truth.run(env, {"SAT-01": [cmd(T0, "SET_HEATER_SETPOINT", setpoint_c=20.0)]}, seed=4)
    assert warm.t_bat_c[0, -360:].mean() > base.t_bat_c[0, -360:].mean() + 3
    assert warm.energy_wh["load"][0] > base.energy_wh["load"][0] + 10


def test_no_charging_below_min_temperature():
    env = synthetic.environment(("SAT-01",), hours=1, in_contact_all=True)
    env.illum = np.ones_like(env.illum)
    p = EpsParams(sink_sun_c=-20.0, sink_eclipse_c=-20.0)
    r = truth.run(env, {"SAT-01": [cmd(T0, "LOAD_OFF", load="HEATER")]}, seed=1, params=p,
                  soc0=0.5, t_bat0_c=-10.0)
    assert r.soc_final[0] <= 0.5 + 1e-12
    assert r.energy_wh["shunt"][0] > 0


def test_array_power_follows_beta_drift_and_degradation():
    env = synthetic.environment(("SAT-01", "SAT-02"), hours=2, beta_deg=[0.0, 60.0], in_contact_all=True)
    env.illum = np.ones_like(env.illum)
    r = truth.run(env, seed=0, params=EpsParams(spread_array=0.0))
    # wings are set up for each orbit's beta: in full sun, the orbit's own beta costs nothing...
    assert r.p_array_avail[1].mean() == pytest.approx(r.p_array_avail[0].mean(), rel=1e-6)
    # ...a drift from the design beta does
    drift = truth.run(env, seed=0, params=EpsParams(spread_array=0.0),
                      mods=truth.Modifiers(extra_beta_deg=np.array([0.0, 60.0])))
    ratio = drift.p_array_avail[1].mean() / drift.p_array_avail[0].mean()
    assert ratio == pytest.approx(np.cos(np.radians(60.0)), rel=1e-6)

    mods = truth.Modifiers(array_factor=np.array([[0.5, 1.0], [1.0, 1.0]]))
    d = truth.run(env, seed=0, params=EpsParams(spread_array=0.0), mods=mods, soc0=0.3)
    assert d.p_array_avail[0].mean() == pytest.approx(0.75 * r.p_array_avail[0].mean(), rel=1e-6)


def test_degraded_panel_current_drops_on_that_panel_only():
    env = synthetic.environment(("SAT-01",), hours=1, in_contact_all=True)
    env.illum = np.ones_like(env.illum)
    mods = truth.Modifiers(array_factor=np.array([[0.4, 1.0]]))
    r = truth.run(env, seed=0, params=EpsParams(spread_array=0.0), mods=mods, soc0=0.3)
    py, my = r.telemetry["i_array_PY"][0], r.telemetry["i_array_MY"][0]
    assert np.mean(py) / np.mean(my) == pytest.approx(0.4, abs=0.02)


def test_resistance_rises_when_cold():
    p = EpsParams()
    assert physics.resistance(p.r0_ohm, -10.0, p.r_temp_b) > 2 * physics.resistance(p.r0_ohm, 25.0, p.r_temp_b)
    assert physics.resistance(p.r0_ohm, 25.0, p.r_temp_b) == pytest.approx(p.r0_ohm)


def test_stored_energy_matches_numeric_integral():
    p = EpsParams()
    s = np.linspace(0, 0.73, 20001)
    num = np.trapezoid(p.n_series * physics.ocv_cell(s, p.soc_grid, p.ocv_cell_v), s) * p.capacity_ah
    assert physics.stored_energy_wh(0.73, p.capacity_ah, p.n_series, p.soc_grid, p.ocv_cell_v) == pytest.approx(num, rel=1e-6)


def test_synthetic_eclipse_sane():
    t = T0 + np.arange(0, synthetic.period_s(500.0), 1.0)
    il = synthetic.illumination(t, 500.0, 0.0)
    assert il.min() >= 0.0 and il.max() <= 1.0
    shadow_min = (il < 0.5).sum() / 60.0
    assert 34.0 < shadow_min < 37.0
    assert ((il > 0) & (il < 1)).sum() > 0  # penumbra has nonzero duration
    assert synthetic.illumination(t, 500.0, 80.0).min() == 1.0  # no eclipse at high beta


def test_fleet_run_is_fast_enough():
    import time

    env = synthetic.environment(tuple(f"SAT-{i:02d}" for i in range(1, 25)), hours=24)
    t = time.perf_counter()
    truth.run(env, seed=0)
    assert time.perf_counter() - t < 10.0
