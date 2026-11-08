"""T17: model selection (live / PENUMBRA_REPLAY / stub) and recorded runs that replay to the same result."""
import json

import pytest

from backend.agent import gemini, session
from backend.agent.__main__ import main
from backend.agent.dispatcher import Dispatcher
from backend.agent.gemini import GeminiModel
from backend.agent.loop import run_agent
from backend.agent.mock_backend import MockBackend
from backend.agent.session import new_run_id, record_run, select_model
from backend.agent.stub import StubModel
from backend.agent.tests.test_gemini import PINNED, FakeClient, stub_script
from backend.recorder import ReplayModel, load_events


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("PENUMBRA_REPLAY", raising=False)
    monkeypatch.setattr(gemini, "_make_client", lambda api_key: FakeClient(stub_script()))


def live_model():
    return GeminiModel(PINNED, client=FakeClient(stub_script()), sleeper=lambda s: None)


def test_select_model_defaults_to_stub():
    assert isinstance(select_model(live=False), StubModel)


def test_select_model_uses_replay_when_penumbra_replay_is_set(tmp_path, monkeypatch):
    rec = record_run(StubModel(), Dispatcher(MockBackend()), run_id="stub-x", runs_dir=tmp_path)
    monkeypatch.setenv("PENUMBRA_REPLAY", str(rec.path))
    assert isinstance(select_model(live=False), ReplayModel)


def test_select_model_live_reads_gemini_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_MODEL", PINNED)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("PENUMBRA_REPLAY", str(tmp_path / "ignored.jsonl"))  # run live beats replay
    m = select_model(live=True)
    assert isinstance(m, GeminiModel) and m.name == PINNED


def test_run_ids_are_unique_and_recorder_safe():
    ids = {new_run_id("live", now=0.0) for _ in range(20)}
    assert len(ids) == 20
    assert all(i.startswith("live-19700101T000000Z-") for i in ids)


def test_live_run_is_recorded_to_jsonl_with_tokens_and_latency(tmp_path):
    seen = []
    rec = record_run(live_model(), Dispatcher(MockBackend()), runs_dir=tmp_path, on_event=seen.append)
    assert rec.run_id.startswith("live-")
    assert rec.path == tmp_path / f"{rec.run_id}.jsonl"
    rows = [json.loads(line) for line in rec.path.read_text().splitlines()]
    assert len(rows) == len(seen) > 0
    assert [r["seq"] for r in rows] == list(range(len(rows)))
    responses = [r for r in rows if r["kind"] == "model_response"]
    assert responses and all("tokens" in r and "latency_ms" in r for r in responses)
    assert rows[0]["kind"] == "model_request" and rows[0]["payload"]["model"] == PINNED


def test_recorded_live_run_replays_to_the_same_result(tmp_path):
    live = record_run(live_model(), Dispatcher(MockBackend()), runs_dir=tmp_path)
    replay = run_agent(ReplayModel(live.path, pace_ms=0), Dispatcher(MockBackend()))
    assert replay.final_text == live.run.final_text
    assert replay.rendered_text == live.run.rendered_text
    assert [(c.name, c.args) for c in replay.tool_calls] == [(c.name, c.args) for c in live.run.tool_calls]
    tokens = [e.tokens for e in load_events(live.path) if e.kind == "model_response"]
    assert all(t is not None for t in tokens)


def test_cli_live_always_records(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_MODEL", PINNED)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    main(["--live", "--runs-dir", str(tmp_path)])
    files = list(tmp_path.glob("live-*.jsonl"))
    assert len(files) == 1
    out = capsys.readouterr().out
    assert "status=done" in out and f"recorded: {files[0]}" in out
    assert "not-a-real-key" not in out


def test_cli_stub_does_not_write_unless_asked(tmp_path, capsys):
    main(["--runs-dir", str(tmp_path)])
    assert list(tmp_path.iterdir()) == []
    main(["--record", "--runs-dir", str(tmp_path)])
    assert len(list(tmp_path.glob("stub-*.jsonl"))) == 1
    assert session.model_kind(StubModel()) == "stub"


def test_tee_recorder_writes_through_and_calls_back(tmp_path):
    from backend.agent.session import TeeRecorder
    from backend.recorder import FlightRecorder

    seen = []
    with FlightRecorder("tee", path=tmp_path / "tee.jsonl") as flight:
        run_agent(StubModel(), Dispatcher(MockBackend()), TeeRecorder(flight, seen.append))
    written = load_events(tmp_path / "tee.jsonl")
    assert [e.seq for e in seen] == [e.seq for e in written] and len(seen) > 0
    assert all(type(e).__name__ == "RecorderEvent" for e in seen)
