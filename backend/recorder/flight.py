"""Append-only, offline flight event recording."""

import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterator, Mapping

from backend.agent.interfaces import RecorderEvent, RecorderKind


class FlightRecorder:
    """Write one contract-shaped JSON object per event, flushing each line."""

    def __init__(
        self,
        run_id: str,
        *,
        path: str | Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise ValueError("run_id must use letters, digits, hyphens, or underscores")
        self.run_id = run_id
        self.path = Path(path) if path is not None else Path("runs") / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("x", encoding="utf-8")
        self._clock = clock
        self.events: list[RecorderEvent] = []

    def record(
        self,
        kind: RecorderKind,
        payload: Mapping[str, Any],
        *,
        plan_hash: str | None = None,
        tokens: int | None = None,
        latency_ms: float | None = None,
    ) -> RecorderEvent:
        event = RecorderEvent(
            ts=float(self._clock()),
            run_id=self.run_id,
            seq=len(self.events),
            kind=kind,
            payload=dict(payload),
            plan_hash=plan_hash,
            tokens=tokens,
            latency_ms=latency_ms,
        )
        row = event.model_dump(mode="json", exclude_none=True)
        self._stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        self._stream.flush()
        self.events.append(event)
        return event

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> "FlightRecorder":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def load_events(path: str | Path) -> list[RecorderEvent]:
    """Read a recorded run without contacting any service."""
    with Path(path).open(encoding="utf-8") as stream:
        return [RecorderEvent(**json.loads(line)) for line in stream if line.strip()]


def replay_events(
    path: str | Path,
    *,
    pace_ms: int | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> Iterator[RecorderEvent]:
    """Yield trace events at the configured pace for the demo UI."""
    if pace_ms is None:
        pace_ms = int(os.environ.get("PENUMBRA_REPLAY_PACE_MS", "700"))
    if pace_ms < 0:
        raise ValueError("PENUMBRA_REPLAY_PACE_MS must be nonnegative")
    for index, event in enumerate(load_events(path)):
        if index and pace_ms:
            sleeper(pace_ms / 1000.0)
        yield event
