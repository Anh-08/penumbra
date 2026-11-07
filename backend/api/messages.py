"""Validate every outbound WebSocket payload against the frozen contracts."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from contracts import (
    Alert,
    ApprovalRefusedMsg,
    ApprovalRequestMsg,
    CalloutMsg,
    DemoStateMsg,
    ExecutionMsg,
    FleetStateMsg,
    HypothesesMsg,
    MatrixMsg,
    RecorderEvent,
    RevealMsg,
    ScheduleResult,
    Trajectory,
)


WS_SCHEMAS: dict[str, type[BaseModel]] = {
    "fleet_state": FleetStateMsg,
    "demo_state": DemoStateMsg,
    "alert": Alert,
    "callout": CalloutMsg,
    "trace_event": RecorderEvent,
    "hypotheses": HypothesesMsg,
    "matrix": MatrixMsg,
    "trajectory": Trajectory,
    "schedule": ScheduleResult,
    "approval_request": ApprovalRequestMsg,
    "approval_refused": ApprovalRefusedMsg,
    "execution": ExecutionMsg,
    "reveal": RevealMsg,
}


def validate_message(kind: str, payload: Any) -> dict[str, Any]:
    """The wire envelope is `{type, data}`; `data` is contract-validated."""
    if kind not in WS_SCHEMAS:
        raise ValueError(f"unknown WebSocket message type: {kind}")
    model = WS_SCHEMAS[kind].model_validate(payload)
    return {"type": kind, "data": model.model_dump(mode="json")}
