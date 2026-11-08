"""M2 (T31): burn window vs power over seeded conjunction scenarios; 2 seeds here, 50 in bench/results/m2.json."""
import json

import pytest

from bench import m2


@pytest.fixture(scope="module")
def result():
    return m2.run(range(2))


def test_result_has_n_seeds_arms_and_provenance(result):
    assert result["n"] == 2 and result["seeds"] == [0, 1]
    assert {r["arm"] for r in result["rows"]} == set(m2.ARMS) and len(result["rows"]) == 6
    assert result["label"] == "simulated debris · sanity check, not validation"
    assert result["data"]["real"] and any("debris" in s for s in result["data"]["simulated"])
    json.dumps(result, allow_nan=False)


def test_each_arm_reports_the_four_metrics(result):
    for arm in m2.ARMS:
        s = result["summary"][arm]
        assert s["cases"] == 2
        assert 0 <= s["soc_violations"] <= 2
        assert sum(s["window_histogram"].values()) == 2
        assert s["miss_after_km_min"] > 0 and 0 < s["payload_uptime_mean"] <= 1
    assert result["summary"]["w1_always"]["window_histogram"]["W1"] == 2
    assert result["summary"]["w3_always"]["window_histogram"]["W3"] == 2


def test_verified_arm_burns_only_a_window_the_verifier_passed(result):
    for r in result["rows"]:
        if r["arm"] != "verified":
            continue
        assert set(r["verifier"]) == {"W1", "W2", "W3"}
        if r["deferred"]:
            assert not any(v["passes_all"] for v in r["verifier"].values())
        else:
            assert r["verifier"][r["window"]]["passes_all"]


def test_a_burn_costs_payload_time(result):
    burned = [r for r in result["rows"] if not r["deferred"]]
    assert burned and all(r["payload_uptime"] < 1.0 for r in burned)


def test_deterministic_and_plots(tmp_path, result):
    again = m2.case(0, "w3_always")
    first = next(r for r in result["rows"] if r["seed"] == 0 and r["arm"] == "w3_always")
    assert again == first
    png = m2.plot(result, tmp_path / "m2.png")
    assert png.exists() and png.stat().st_size > 1000
