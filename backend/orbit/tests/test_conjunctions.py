import json

import numpy as np
import pytest

from contracts import CAM_LEAD_S, Conjunction
from backend.orbit import OrbitModel, load_fleet
from backend.orbit import conjunctions as C

T0 = 1794052800.0          # 2026-11-07 12:00 UTC, the scenario epoch


@pytest.fixture(scope="module")
def model():
    return OrbitModel(load_fleet(), t0=T0)


def test_planted_debris_is_found_at_the_known_time(model):
    tca, miss = T0 + 5 * 3600, 0.3
    deb = C.plant_debris(model, "SAT-09", tca, miss, 60.0, "DEB-01")
    found = C.find_conjunctions(model, [deb])
    assert len(found) == 1
    cj = found[0]
    assert isinstance(cj, Conjunction) and cj.sat_id == "SAT-09" and cj.object_id == "DEB-01"
    assert abs(cj.tca - tca) < 1.0 and abs(cj.miss_km - miss) < 0.05
    assert [w.window_id for w in cj.windows] == ["W1", "W2", "W3"]
    assert all(w.t_end <= cj.tca - CAM_LEAD_S for w in cj.windows)
    dv = [w.dv_mps for w in cj.windows]
    assert dv[0] < dv[1] < dv[2]                     # early is cheap, late is expensive
    assert all(w.miss_after_km > cj.miss_km for w in cj.windows)


def test_far_debris_gives_nothing(model):
    deb = C.plant_debris(model, "SAT-09", T0 + 5 * 3600, 3.0, 60.0, "DEB-02")   # outside the 1 km screen
    assert C.find_conjunctions(model, [deb]) == []


def test_deterministic_and_committed_file_matches(model):
    a = [c.model_dump() for c in C.find_conjunctions(model)]
    b = [c.model_dump() for c in C.find_conjunctions(model)]
    assert a == b and len(a) == 1 and a[0]["sat_id"] == "SAT-17"       # the T31 scenario's object
    assert [w["window_id"] for w in a[0]["windows"]] == ["W1", "W2", "W3"]
    committed = json.loads(C.DEFAULT_DEBRIS.read_text())
    assert all(d["simulated"] and d["object_id"].startswith("DEB-") for d in committed)
    assert model.conjunctions()[0].conj_id == "CJ-1"


def test_debris_record_can_set_its_own_window_leads(model):
    deb = C.plant_debris(model, "SAT-09", T0 + 5 * 3600, 0.3, 60.0, "DEB-03")
    deb["window_leads_s"] = [3 * 3600, 2 * 3600, 3600]
    cj = C.find_conjunctions(model, [deb])[0]
    assert [round((cj.tca - w.t_end) / 3600, 3) for w in cj.windows] == [3.0, 2.0, 1.0]


def test_two_body_propagation_conserves_energy():
    r0, v0 = np.array([6878.0, 0.0, 0.0]), np.array([0.0, 7.61, 0.0])
    pos = C.propagate(r0, v0, 0.0, np.array([-3000.0, 0.0, 5700.0]))
    assert np.allclose(np.linalg.norm(pos, axis=1), 6878.0, rtol=2e-3)
