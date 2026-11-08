"""FastAPI boundary; the deterministic provider remains replaceable via one switch."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from contracts import (
    DEMO_SPEED,
    ApprovalRefused,
    ApprovalRefusedMsg,
    ApproveRequest,
    CalloutMsg,
    ClockRequest,
    DemoRequest,
    DemoStateMsg,
    ExecutionMsg,
    RevealMsg,
    SatId,
    ScenarioRequest,
)

from .clock import SimClock
from .messages import validate_message
from .providers import MockProvider, select_provider

FLEET_INTERVAL_S = 2 / 3  # 1.5 Hz, inside the contracted 1–2 Hz range.


class ScenarioResponse(BaseModel):
    name: str
    seed: int
    t: float
    status: str


class StormResponse(BaseModel):
    started: bool
    alert_count: int


class ClockResponse(BaseModel):
    t: float
    speed: float


class RevealRequest(BaseModel):
    sat_id: SatId


def create_app(provider: Any | None = None) -> FastAPI:
    app = FastAPI(title="Penumbra API", version="0.1.0")
    source = provider if provider is not None else select_provider()
    t0 = getattr(source, "t0", datetime.now(timezone.utc).timestamp())
    clock = SimClock(t0=t0)
    listeners: set[asyncio.Queue[dict[str, Any]]] = set()
    app.state.provider = source
    app.state.clock = clock
    app.state.scenario_task = None

    def scenario_ready() -> bool:
        task = app.state.scenario_task
        return task is None or (task.done() and task.exception() is None)

    def publish(kind: str, payload: Any) -> None:
        message = validate_message(kind, payload)
        for listener in tuple(listeners):
            if listener.full():
                listener.get_nowait()
            listener.put_nowait(message)

    app.state.publish = publish

    @app.websocket("/ws")
    async def websocket_fleet(websocket: WebSocket) -> None:
        await websocket.accept()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        listeners.add(queue)
        next_fleet = time.monotonic()
        try:
            while True:
                now = time.monotonic()
                if now >= next_fleet:
                    if scenario_ready():
                        state = source.fleet_state(clock.now(), clock.speed)
                        if state is not None:
                            await websocket.send_json(validate_message("fleet_state", state))
                    next_fleet = now + FLEET_INTERVAL_S
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=max(0.0, next_fleet - time.monotonic()))
                except TimeoutError:
                    continue
                await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            listeners.discard(queue)

    @app.post("/api/scenario", response_model=ScenarioResponse)
    async def scenario(request: ScenarioRequest) -> ScenarioResponse:
        if getattr(source, "background_scenario", False):
            task = app.state.scenario_task
            if task is not None and not task.done():
                raise HTTPException(status_code=409, detail="scenario build already in progress")
            clock.reset(t0)
            app.state.scenario_task = asyncio.create_task(
                asyncio.to_thread(source.set_scenario, request.name, request.seed))
            return ScenarioResponse(name=request.name, seed=request.seed, t=clock.now(), status="building")
        source.set_scenario(request.name, request.seed)
        clock.reset(t0)
        return ScenarioResponse(name=request.name, seed=request.seed, t=clock.now(), status="ready")

    @app.post("/api/fault-storm", response_model=StormResponse)
    async def fault_storm() -> StormResponse:
        if not scenario_ready():
            raise HTTPException(status_code=503, detail="scenario is not ready")
        alerts = source.storm_alerts(clock.now())
        for alert in alerts:
            publish("alert", alert)
            if alert.severity in ("warning", "critical"):
                publish("callout", CalloutMsg(text=f"{alert.sat_id} requires attention", priority=2))
        return StormResponse(started=True, alert_count=len(alerts))

    @app.post("/api/demo", response_model=DemoStateMsg)
    async def demo(request: DemoRequest) -> DemoStateMsg:
        speed = DEMO_SPEED[request.state]
        clock.set_speed(speed)
        state = DemoStateMsg(state=request.state, speed=speed)
        publish("demo_state", state)
        return state

    @app.post("/api/approve", response_model=ApprovalRefused | ExecutionMsg)
    async def approve(request: ApproveRequest) -> ApprovalRefused | ExecutionMsg:
        result = source.approve(request.approval_id, request.plan_hash)
        if isinstance(result, ApprovalRefused):
            publish("approval_refused", ApprovalRefusedMsg(
                approval_id=request.approval_id,
                reason_key=result.reason_key,
                failing_hypotheses=result.failing_hypotheses,
            ))
        else:
            publish("execution", result)
        return result

    @app.post("/api/clock", response_model=ClockResponse)
    async def set_clock(request: ClockRequest) -> ClockResponse:
        if not 1 <= request.speed <= 600:
            raise HTTPException(status_code=422, detail="speed must be between 1 and 600")
        clock.set_speed(request.speed)
        return ClockResponse(t=clock.now(), speed=clock.speed)

    @app.get("/api/bench", response_model=dict[str, Any])
    async def bench() -> dict[str, Any]:
        results_dir = Path(__file__).resolve().parents[2] / "bench" / "results"
        results: dict[str, Any] = {}
        if results_dir.is_dir():
            for path in sorted(results_dir.glob("*.json")):
                results[path.name] = json.loads(path.read_text(encoding="utf-8"))
        return {"results": results}

    @app.post("/api/reveal", response_model=RevealMsg)
    async def reveal(request: RevealRequest) -> RevealMsg:
        result = source.reveal(request.sat_id)
        if result is None:
            raise HTTPException(status_code=409, detail="truth can be revealed only after execution")
        message = RevealMsg.model_validate(result)
        publish("reveal", message)
        return message

    return app


app = create_app()
