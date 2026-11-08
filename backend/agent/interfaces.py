"""What the agent needs from the rest of the backend, and where it sends trace events.

`Backend` is the agent's view of orbit (B2), eps/verify/fleet (B1) and sched (B2). Method names and types follow
CONTRACTS.md. Until those modules merge, `mock_backend.MockBackend` stands in (SIMULATED data); the real adapter
is wired at integration (T22).

The agent owns the plan registry: `simulate` and `tier` receive the `Plan` object, so the verifier never needs
a separate lookup by plan_id.

`Recorder` is the flight-recorder seam (B2, T13). `NullRecorder` is the default until T13 merges.
"""
from __future__ import annotations

import time
from typing import Any, Literal, Mapping, Protocol, Sequence

from contracts import (
    Alert,
    Conjunction,
    Contact,
    HypothesisScores,
    Plan,
    RecorderEvent,
    RecorderKind,
    ScheduleRequest,
    ScheduleResult,
    SimResult,
    TriageEntry,
)


class Backend(Protocol):
    def now(self) -> float:
        """Current sim time, POSIX seconds UTC."""
        ...

    # fleet (B1)
    def detect(self, t: float) -> list[Alert]: ...
    def triage(self) -> list[TriageEntry]: ...
    def tier(self, plan: Plan, sim: SimResult) -> tuple[Literal[1, 2], str | None]:
        """Tier policy: (1, "FR-<n>") for a pre-approved flight rule, else (2, None)."""
        ...

    # eps (B1)
    def telemetry(self, sat_id: str, t_from: float, t_to: float) -> Mapping[str, Sequence[float]]:
        """Grid arrays on [t_from, t_to]: key "t" plus telemetry channels. NaN outside contacts."""
        ...

    def score_hypotheses(self, sat_id: str) -> HypothesisScores: ...

    # orbit (B2)
    def contacts(self) -> list[Contact]: ...
    def next_contact(self, sat_id: str, t: float) -> Contact | None: ...

    # verify (B1)
    def simulate(self, plan: Plan, hypotheses: str | list[str], monte_carlo: int) -> SimResult: ...

    # sched (B2)
    def schedule(self, requests: list[ScheduleRequest]) -> ScheduleResult: ...

    # orbit, T31 (optional: a backend without it simply has no conjunctions, and CAM plans are refused)
    def conjunctions(self) -> list[Conjunction]:
        """Predicted conjunctions with simulated debris, each with its maneuver windows W1..W3."""
        ...

    # execution (B1, wired at T22)
    def execute(self, plan: Plan) -> float:
        """Uplink an approved (or Tier 1) plan; its commands run at their t_exec. For kind "diagnostic", return
        only once the test's telemetry is downlinked (the sim clock moves past the last step plus one dwell), so
        the next score_hypotheses includes it. Returns the sim time after execution, POSIX seconds UTC.
        Raise ValueError if the plan cannot run (for example a t_exec in the past)."""
        ...


class Recorder(Protocol):
    def record(
        self,
        kind: RecorderKind,
        payload: dict[str, Any],
        *,
        plan_hash: str | None = None,
        tokens: int | None = None,
        latency_ms: float | None = None,
    ) -> Any:
        """Append one flight-recorder event. The recorder adds ts, run_id and seq."""
        ...


class NullRecorder:
    """No-op recorder, used until the flight recorder (T13) merges."""

    def record(self, kind, payload, *, plan_hash=None, tokens=None, latency_ms=None) -> None:
        return None


class ListRecorder:
    """Keeps contracts.RecorderEvent objects in memory (tests, mock API)."""

    def __init__(self, run_id: str = "mock") -> None:
        self.run_id = run_id
        self.events: list[RecorderEvent] = []

    def record(self, kind, payload, *, plan_hash=None, tokens=None, latency_ms=None) -> RecorderEvent:
        event = RecorderEvent(
            ts=time.time(),
            run_id=self.run_id,
            seq=len(self.events),
            kind=kind,
            payload=payload,
            plan_hash=plan_hash,
            tokens=tokens,
            latency_ms=latency_ms,
        )
        self.events.append(event)
        return event
