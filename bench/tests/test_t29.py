import json
from pathlib import Path

import pytest

from bench import t29
from contracts import Command

RESULTS = Path(__file__).resolve().parents[1] / "results"
T0 = 1_000_000.0


def test_uptime_follows_the_commanded_duty():
    cmds = [Command(t_exec=T0 + 100, name="SET_PAYLOAD_DUTY", args={"duty": 0.5}),
            Command(t_exec=T0 + 300, name="SAFE_MODE")]
    assert t29._uptime(cmds, T0, T0 + 400, 1.0) == pytest.approx((100 * 1.0 + 200 * 0.5) / 400)
    assert t29._uptime([], T0, T0 + 10, 0.8) == pytest.approx(0.8)
    off = [Command(t_exec=T0, name="LOAD_OFF", args={"load": "PAYLOAD"})]
    assert t29._uptime(off, T0, T0 + 50, 1.0) == pytest.approx(0.0)


def test_storms_are_seeded():
    a = t29._storm(3)
    b = t29._storm(3)
    assert a[1] == b[1] and [f.model_dump() for f in a[3]] == [f.model_dump() for f in b[3]]
    assert len(a[1]) == t29.STORM_FAULTY and len(a[2]) == t29.STORM_HEALTHY
    assert t29._storm(4)[1] != a[1]


def test_b2_one_seed_has_all_arms():
    r = t29.b2(range(1))
    assert set(r["accuracy_top1"]) == {"peers+diagnostic", "peers_only", "diagnostic_only", "neither"}
    assert r["n"] == 1 and r["seeds"] == [0]


@pytest.mark.parametrize("name", ["b2", "b3", "b5"])
def test_results_files_state_n_seeds_and_provenance(name):
    path = RESULTS / f"{name}.json"
    if not path.exists():
        pytest.skip(f"{path.name} not generated")
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["n"] == len(d["seeds"]) and d["n"] > 0
    assert d["data"]["real"] and d["data"]["simulated"]
    if name == "b3":
        assert d["operator_minutes_per_decision"] == t29.OPERATOR_MIN_PER_DECISION
        assert set(d["summary"]) == {"fcfs", "triage"}
    if name == "b5":
        assert set(d["summary"]) == {"verified_plan", "safe_mode_immediately"}
