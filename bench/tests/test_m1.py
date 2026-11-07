"""The M1 sanity check must retain real/simulated provenance and anonymity."""

import json
from pathlib import Path

from bench.m1 import build_result, write_result


DATA = Path(__file__).resolve().parents[2] / "data"


def test_real_and_simulated_signatures_are_reported_without_satellite_names(tmp_path):
    result = build_result(DATA)
    path = write_result(result, tmp_path / "m1.json")
    saved = json.loads(path.read_text())

    assert saved["label"] == "sanity check, not validation"
    assert saved["inputs"]["real"] and saved["inputs"]["simulated"]
    capacity = saved["capacity_fade"]
    assert capacity["discharge_cycles"] == 168
    assert len(capacity["real_normalized_capacity"]) == 168
    assert capacity["real_normalized_capacity"][0] == 1.0
    assert 0.65 < capacity["real_normalized_capacity"][-1] < 0.8
    assert capacity["simulated_remaining_fraction"] == 0.7
    assert capacity["endpoint_abs_gap"] < 0.1

    panels = saved["array_degradation"]
    assert panels["workbooks_used"] == 4
    assert len(panels["real_failed_ratios"]) == 2
    assert len(panels["real_healthy_ratios"]) == 2
    assert max(panels["real_failed_ratios"]) < min(panels["real_healthy_ratios"])
    assert 0.3 < panels["simulated_fault_ratio"] < 0.5

    serialized = path.read_text()
    assert all(p.stem not in serialized for p in (DATA / "birds").glob("*.xlsx"))


def test_plot_is_generated_from_result(tmp_path):
    from pytest import importorskip

    importorskip("matplotlib")
    from bench.plot_m1 import render_plot

    result = build_result(DATA)
    png = render_plot(result, tmp_path / "m1.png")
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert png.stat().st_size > 10_000
