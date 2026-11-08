import json
from pathlib import Path

from bench import b4

RESULTS = Path(__file__).resolve().parents[1] / "results" / "b4.json"


def test_planning_case_reports_throughput_and_ram():
    r = b4.planning_case(48, step_s=60, hours=1.0, monte_carlo=4, dtype="float32")
    assert r["batch_elements"] == 48 * 6 * 3 * 4 and r["steps"] == 60
    assert r["evals_per_s"] > 0 and r["peak_ram_mib"] > 0 and r["dtype"] == "float32"
    assert 0.0 <= r["check_min_soc_range"][0] <= r["check_min_soc_range"][1] <= 1.0


def test_scheduler_case_uses_only_legal_satellites():
    r = b4.scheduler_case(24)
    assert r["satellites"] == 24 and r["stations"] == 3
    assert r["scheduled"] + r["unscheduled"] == 24 and r["solve_ms"] >= 0


def test_results_file_states_settings_provenance_n_and_seeds():
    d = json.loads(RESULTS.read_text(encoding="utf-8"))
    assert [r["satellites"] for r in d["planning_scale"]] == [24, 1000, 10000]
    ten_k = d["planning_scale"][-1]
    assert (ten_k["dtype"], ten_k["step_s"], ten_k["hours"], ten_k["monte_carlo"]) == ("float32", 60, 3.0, 20)
    assert [r["requests"] for r in d["scheduler"]] == [24, 200]
    assert all(r["satellites"] == 24 for r in d["scheduler"])
    assert d["data"]["real"] and d["data"]["simulated"]
    assert d["n"] and d["seeds"]
