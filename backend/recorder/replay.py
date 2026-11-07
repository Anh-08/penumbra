"""Replay recorded model responses while the tools run again."""

import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence

from backend.agent.model import Message, Model, ModelResponse

from .flight import load_events


class ReplayModel:
    """A Model implementation backed by JSONL model_response events."""

    def __init__(
        self,
        path: str | Path,
        *,
        pace_ms: int | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.responses = [
            event for event in load_events(path) if event.kind == "model_response"
        ]
        self.pace_ms = (
            int(os.environ.get("PENUMBRA_REPLAY_PACE_MS", "700"))
            if pace_ms is None else pace_ms
        )
        if self.pace_ms < 0:
            raise ValueError("PENUMBRA_REPLAY_PACE_MS must be nonnegative")
        self._sleeper = sleeper
        self._next = 0

    def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
    ) -> ModelResponse:
        """Return the next response with the original token count."""
        del system, messages, tools
        if self._next >= len(self.responses):
            raise EOFError("recorded model responses are exhausted")
        if self._next and self.pace_ms:
            self._sleeper(self.pace_ms / 1000.0)
        event = self.responses[self._next]
        self._next += 1
        return ModelResponse.from_payload(event.payload, tokens=event.tokens)


def model_from_env(fallback: Model) -> Model:
    """Select replay mode when PENUMBRA_REPLAY names a recorded run."""
    path = os.environ.get("PENUMBRA_REPLAY")
    return ReplayModel(path) if path else fallback
