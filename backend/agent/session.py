"""Choosing the model and running one agent run with a flight record (T17).

`select_model(live)` is the one place the app picks a model:
- live=True  -> Gemini (`GeminiModel.from_env()`, needs GEMINI_MODEL and GEMINI_API_KEY);
- live=False -> `backend.recorder.model_from_env(StubModel())`: the recorded run in PENUMBRA_REPLAY, else the stub.

`record_run(model, dispatcher)` runs the loop with a `FlightRecorder` writing `runs/<run_id>.jsonl`, so every live
run is recorded. `on_event` receives each RecorderEvent as it is written (the API streams them as `trace_event`).
`fleet=True` works the triage queue satellite by satellite (fleet.run_fleet) into the same single record.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .dispatcher import Dispatcher
from .fleet import FleetRun, run_fleet
from .loop import AgentRun, run_agent
from .model import Model
from .prompt import DEFAULT_GOAL
from .stub import StubModel


@dataclass(frozen=True)
class RecordedRun:
    run: AgentRun | FleetRun
    run_id: str
    path: Path


def select_model(live: bool = False) -> Model:
    if live:
        from .gemini import GeminiModel

        return GeminiModel.from_env()
    from backend.recorder import model_from_env

    return model_from_env(StubModel())


def model_kind(model: Model) -> str:
    if getattr(model, "live", False):
        return "live"
    return "replay" if type(model).__name__ == "ReplayModel" else "stub"


def new_run_id(kind: str, now: float | None = None) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() if now is None else now))
    return f"{kind}-{stamp}-{secrets.token_hex(3)}"


class _Tee:
    """Recorder that writes through to the flight recorder, then hands the event to a callback."""

    def __init__(self, inner: Any, on_event: Callable[[Any], None]) -> None:
        self._inner = inner
        self._on_event = on_event

    def record(self, kind, payload, *, plan_hash=None, tokens=None, latency_ms=None):
        event = self._inner.record(kind, payload, plan_hash=plan_hash, tokens=tokens, latency_ms=latency_ms)
        self._on_event(event)
        return event


def record_run(
    model: Model,
    dispatcher: Dispatcher,
    *,
    run_id: str | None = None,
    runs_dir: str | Path = "runs",
    on_event: Callable[[Any], None] | None = None,
    goal: str = DEFAULT_GOAL,
    fleet: bool = False,
) -> RecordedRun:
    from backend.recorder import FlightRecorder

    run_id = run_id or new_run_id(model_kind(model))
    path = Path(runs_dir) / f"{run_id}.jsonl"
    with FlightRecorder(run_id, path=path) as flight:
        recorder = _Tee(flight, on_event) if on_event is not None else flight
        run = run_fleet(model, dispatcher, recorder) if fleet else run_agent(model, dispatcher, recorder, goal=goal)
    return RecordedRun(run=run, run_id=run_id, path=path)
