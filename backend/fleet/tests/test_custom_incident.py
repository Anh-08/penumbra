"""T42: the judge-chosen incident builds, alerts for every chosen satellite, and rejects bad input."""
import pytest

from backend.fleet import scenarios
from backend.fleet.backend import FleetBackend
from backend.fleet.demo import until_alert

PEERS = ("SAT-03", "SAT-12", "SAT-17", "SAT-07", "SAT-19")   # a small fleet keeps these tests fast


def test_menu_matches_the_eps_fault_kinds():
    ids = [f["id"] for f in scenarios.FAULT_MENU]
    assert ids == ["array_degradation", "stuck_on_load", "capacity_fade", "sensor_bias"]
    assert all(f["label"] and f["spoken"] and 0 < f["soc0"] <= 1 for f in scenarios.FAULT_MENU)
    assert scenarios.MAX_INCIDENTS == 3


def test_normalize_accepts_tuples_and_dicts_and_rejects_bad_input():
    norm = scenarios.normalize_incidents
    assert norm([("SAT-07", "stuck_on_load")]) == [("SAT-07", "stuck_on_load")]
    assert norm([{"sat_id": "SAT-12", "fault": "capacity_fade"}, ("SAT-19", "array_degradation")]) == [
        ("SAT-12", "capacity_fade"), ("SAT-19", "array_degradation")]
    with pytest.raises(ValueError):
        norm([])
    with pytest.raises(ValueError):
        norm([("SAT-99", "stuck_on_load")])
    with pytest.raises(ValueError):
        norm([("SAT-07", "meteor")])
    with pytest.raises(ValueError):
        norm([("SAT-07", "stuck_on_load"), ("SAT-07", "capacity_fade")])
    with pytest.raises(ValueError):
        norm([("SAT-01", "stuck_on_load"), ("SAT-02", "stuck_on_load"), ("SAT-03", "stuck_on_load"),
              ("SAT-04", "stuck_on_load")])


ONSET = scenarios.T0 + scenarios.STORM_ONSET_S


@pytest.mark.parametrize("sat", ["SAT-07", "SAT-12"])          # mostly sunlit / 35 % shadow
@pytest.mark.parametrize("fault_id", [f["id"] for f in scenarios.FAULT_MENU])
def test_each_fault_alerts_after_onset_and_inside_six_hours_on_sunny_and_shadowed_sats(fault_id, sat):
    sc = scenarios.build("custom", seed=0, sat_ids=PEERS, incidents=[(sat, fault_id)])
    assert sc.name == "custom" and sc.focus_sat == sat
    be = FleetBackend(sc)
    t, msgs = until_alert(be, sat, step_s=120.0)
    assert ONSET < t <= ONSET + 6 * 3600.0, f"{fault_id} on {sat}: alert at onset {(t - ONSET) / 3600:+.1f} h"
    alert = next(m["data"] for m in msgs if m["type"] == "alert" and m["data"].sat_id == sat)
    assert alert.severity in ("warning", "critical")


def test_three_satellites_each_alert_and_the_others_stay_quiet():
    picks = [("SAT-07", "stuck_on_load"), ("SAT-12", "array_degradation"), ("SAT-19", "capacity_fade")]
    sc = scenarios.build("custom", seed=3, sat_ids=PEERS, incidents=picks)
    assert sc.focus_sat == "SAT-07"
    be = FleetBackend(sc)
    seen = {}
    end = ONSET + 6 * 3600.0
    while be.now() < end and len(seen) < 3:
        be.advance_to(be.now() + 120.0)
        for m in be.detector.drain_messages():
            if m["type"] == "alert":
                seen.setdefault(m["data"].sat_id, be.now())
    assert set(seen) == {"SAT-07", "SAT-12", "SAT-19"}, seen
    assert all(t > ONSET for t in seen.values()), "every alert comes after the fault onset"
    # the two healthy peers (duty 0.5, SOC 0.75) never alert in that window
    assert not {"SAT-03", "SAT-17"} & set(seen)


def test_same_seed_builds_the_same_world():
    import numpy as np

    a = scenarios.build("custom", seed=1, sat_ids=PEERS, hours=4.0, incidents=[("SAT-12", "array_degradation")])
    b = scenarios.build("custom", seed=1, sat_ids=PEERS, hours=4.0, incidents=[("SAT-12", "array_degradation")])
    assert a.seed == b.seed == 1 and a.focus_sat == "SAT-12"
    t1 = scenarios.T0 + 3.9 * 3600.0
    for w in (a, b):
        w.world.advance_to(t1)
    ta, tb = (np.asarray(w.world.telemetry("SAT-12", scenarios.T0, t1)["soc"], float) for w in (a, b))
    assert np.isfinite(ta).sum() > 0, "at least one pass downlinks SOC inside four hours"
    assert np.array_equal(ta, tb, equal_nan=True)
    assert a.initial.soc.tolist() == b.initial.soc.tolist()
