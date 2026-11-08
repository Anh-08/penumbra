from __future__ import annotations

from pathlib import Path

from backend.fleet.scenarios import build
from bench.run import load_cached_proposal, run_benchmark


def test_cached_gemini_proposal_is_an_allowlisted_command() -> None:
    command = load_cached_proposal(sat_id=build("single-stuck-load").focus_sat)
    assert command.name == "LOAD_OFF"
    assert command.args == {"load": "PAYLOAD"}


def test_four_arms_share_seeded_truth_trials(tmp_path: Path) -> None:
    result = run_benchmark(seeds=2, hours=6, output=tmp_path / "b1.json")

    assert result["n"] == 2
    assert set(result["arms"]) == {
        "brute_force", "random_valid", "gemini_unverified", "gemini_verified"
    }
    for arm in result["arms"].values():
        assert arm["n"] == 2
        assert len(arm["margins_soc"]) == 2
        assert 0 <= arm["violations"] <= 2
    assert result["arms"]["brute_force"]["violations"] <= result["arms"]["random_valid"]["violations"]
    assert (tmp_path / "b1.json").exists()
    assert result["inputs"]["simulated"]
    assert result["focus_sat"] == build("single-stuck-load").focus_sat
    assert result["inputs"]["recorded"]
    assert result["inputs"]["real"] == []
