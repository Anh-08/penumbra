"""Offline flight recording and deterministic agent replay."""

import json

from backend.recorder import FlightRecorder, load_events, replay_events


def test_jsonl_has_contract_fields_and_monotonic_sequence(tmp_path):
    path = tmp_path / "run.jsonl"
    with FlightRecorder("run", path=path, clock=lambda: 1_794_074_400.0) as recorder:
        recorder.record("alert", {"sat_id": "SAT-07"})
        recorder.record(
            "model_response",
            {"text": "SAT-07 needs attention", "tool_calls": []},
            plan_hash="abc",
            tokens=17,
            latency_ms=3.5,
        )

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["seq"] for line in lines] == [0, 1]
    assert all(line["ts"] == 1_794_074_400.0 and line["run_id"] == "run" for line in lines)
    assert lines[0] == {
        "ts": 1_794_074_400.0,
        "run_id": "run",
        "seq": 0,
        "kind": "alert",
        "payload": {"sat_id": "SAT-07"},
    }
    assert lines[1]["plan_hash"] == "abc"
    assert lines[1]["tokens"] == 17
    assert lines[1]["latency_ms"] == 3.5
    assert [event.kind for event in load_events(path)] == ["alert", "model_response"]


def test_event_pacing_uses_environment_setting(tmp_path, monkeypatch):
    path = tmp_path / "paced.jsonl"
    with FlightRecorder("paced", path=path) as recorder:
        recorder.record("alert", {})
        recorder.record("execution", {})
        recorder.record("tool_result", {})

    monkeypatch.setenv("PENUMBRA_REPLAY_PACE_MS", "25")
    delays = []
    assert [event.kind for event in replay_events(path, sleeper=delays.append)] == [
        "alert", "execution", "tool_result"
    ]
    assert delays == [0.025, 0.025]
