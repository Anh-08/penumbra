"""Fleet-backed API adapter for live positions, verification, approval, and reveal."""
from __future__ import annotations

import math
from threading import RLock
from typing import Any

from contracts import ApprovalRefused, ExecutionMsg, FleetStateMsg, HYPOTHESIS_LABELS, SAT_IDS


def _field(row: Any, key: str) -> Any:
    return row[key] if isinstance(row, dict) else getattr(row, key)


def fleet_state_from_backend(backend: Any, t: float, speed: float) -> FleetStateMsg:
    """Advance the real stack before collecting orbit, downlink, and triage state."""
    backend.advance_to(t)
    orbit = backend.scenario.orbit
    positions = orbit.positions(t)
    statuses = {_field(row, "sat_id"): _field(row, "status") for row in backend.triage()}
    sats = []
    for sat_id in SAT_IDS:
        position = positions[sat_id]
        latest_soc = backend.world.latest(sat_id)["soc"]
        soc = latest_soc if math.isfinite(latest_soc) else None
        sats.append({
            "sat_id": sat_id,
            "lat_deg": position["lat_deg"],
            "lon_deg": position["lon_deg"],
            "alt_km": position["alt_km"],
            "illum": position["illum"],
            "soc": soc,
            "status": statuses.get(sat_id, "nominal"),
        })
    return FleetStateMsg.model_validate({
        "t": t, "speed": speed,
        "sun_direction": orbit.sun_direction(t),
        "sats": sats,
    })


class RealProvider:
    """The real fleet stack behind the API's existing provider boundary."""

    background_scenario = True

    def __init__(self, name: str = "single-stuck-load", seed: int = 1):
        self._lock = RLock()
        self.executed: set[str] = set()
        self._pending_messages: list[dict[str, Any]] = []
        self.set_scenario(name, seed)

    def set_scenario(self, name: str, seed: int) -> None:
        from backend.agent.dispatcher import Dispatcher
        from backend.fleet.backend import FleetBackend
        from backend.fleet.scenarios import build

        scenario = build(name, seed)
        backend = FleetBackend(scenario)
        dispatcher = Dispatcher(backend)
        with self._lock:
            self.backend = backend
            self.dispatcher = dispatcher
            self.scenario_name = name
            self.seed = seed
            self.t0 = float(scenario.env.t[0])
            self.executed.clear()
            self._pending_messages.clear()

    def fleet_state(self, t: float, speed: float) -> FleetStateMsg:
        with self._lock:
            state = fleet_state_from_backend(self.backend, t, speed)
            self._pending_messages.extend(self.backend.detector.drain_messages())
            return state

    def drain_messages(self) -> list[dict[str, Any]]:
        with self._lock:
            messages = self._pending_messages
            self._pending_messages = []
            return messages

    def storm_alerts(self, t: float) -> list[Any]:
        with self._lock:
            alerts = self.backend.advance_to(t)
            self._pending_messages.extend(self.backend.detector.drain_messages())
            return alerts

    def operator_ticket(self, plan_id: str) -> Any:
        with self._lock:
            return self.dispatcher.operator_ticket(plan_id)

    def matrix(self, sat_id: str, complete: bool = True) -> Any:
        with self._lock:
            return self.backend.matrix(sat_id, complete)

    def trajectory(self, plan_id: str, hypothesis: str) -> Any:
        with self._lock:
            return self.backend.verifier.trajectory(plan_id, hypothesis)

    def approve(self, approval_id: str, plan_hash: str) -> ApprovalRefused | ExecutionMsg:
        with self._lock:
            result = self.dispatcher.approve(approval_id, plan_hash)
            if isinstance(result, ApprovalRefused):
                return result
            self.dispatcher.execute(approval_id)
            self.executed.add(result.sat_id)
            return ExecutionMsg(plan_id=result.plan_id, sat_id=result.sat_id,
                                status="executed", t=self.backend.now())

    def reveal(self, sat_id: str) -> dict[str, Any] | None:
        if sat_id not in self.executed:
            return None
        with self._lock:
            truth = self.backend.world.reveal(sat_id)
            scores = self.backend.score_hypotheses(sat_id).scores
            hypothesis = max(scores, key=scores.get)
            return {
                "sat_id": sat_id,
                "truth": truth["truth"],
                "diagnosed": {"H": hypothesis, "label": HYPOTHESIS_LABELS[hypothesis],
                              "posterior": scores[hypothesis]},
                "truth_soc": truth["truth_soc"],
            }
