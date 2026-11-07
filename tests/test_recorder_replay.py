"""Replaying a recorded agent run must repeat its tool outputs and plan hash."""

from backend.agent.dispatcher import Dispatcher
from backend.agent.interfaces import ListRecorder
from backend.agent.loop import run_agent
from backend.agent.mock_backend import MockBackend
from backend.agent.stub import StubModel
from backend.recorder import FlightRecorder, ReplayModel, load_events, model_from_env


def test_replay_reexecutes_tools_with_identical_results_and_plan_hash(tmp_path, monkeypatch):
    path = tmp_path / "agent-run.jsonl"
    with FlightRecorder("agent-run", path=path) as recorder:
        original = run_agent(StubModel(), Dispatcher(MockBackend()), recorder)

    monkeypatch.setenv("PENUMBRA_REPLAY", str(path))
    monkeypatch.setenv("PENUMBRA_REPLAY_PACE_MS", "0")
    replay_model = model_from_env(StubModel())
    assert isinstance(replay_model, ReplayModel)

    replay_recorder = ListRecorder("replay")
    replayed = run_agent(replay_model, Dispatcher(MockBackend()), replay_recorder)
    recorded_events = load_events(path)
    original_results = [event.payload for event in recorded_events if event.kind == "tool_result"]
    replay_results = [event.payload for event in replay_recorder.events if event.kind == "tool_result"]
    assert replay_results == original_results

    def plan_hashes(results):
        return [result["result"]["plan_hash"] for result in results if "plan_hash" in result["result"]]

    assert plan_hashes(replay_results) == plan_hashes(original_results)
    assert plan_hashes(replay_results)
    assert replayed.final_text == original.final_text
