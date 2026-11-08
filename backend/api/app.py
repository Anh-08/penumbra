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
from .storm import STORM_SEED, StormDirector

FLEET_INTERVAL_S = 2 / 3  # 1.5 Hz, inside the contracted 1–2 Hz range.


class ScenarioResponse(BaseModel):
    name: str
    seed: int
    t: float
    status: str


class StormResponse(BaseModel):
    started: bool
    alert_count: int


class StormRequest(BaseModel):
    live: bool = False


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
    app.state.loop = None
    app.state.provider = source
    app.state.clock = clock
    app.state.scenario_task = None
    app.state.demo_state = 'attract'
    app.state.run_live = False

    def scenario_ready() -> bool:
        task = app.state.scenario_task
        return task is None or (task.done() and task.exception() is None)

    def transition(state: str) -> DemoStateMsg:
        speed = DEMO_SPEED[state]
        clock.set_speed(speed)
        app.state.demo_state = state
        message = DemoStateMsg(state=state, speed=speed)
        publish('demo_state', message)
        return message

    def enter_agent_after_alerts(events: list[dict[str, Any]]) -> None:
        if app.state.demo_state == 'storm' and any(event['type'] == 'alert' for event in events):
            transition('agent')

    def enqueue(message: dict[str, Any]) -> None:
        for listener in tuple(listeners):
            if listener.full():
                listener.get_nowait()
            listener.put_nowait(message)

    async def after_matrix(message: dict[str, Any]) -> None:
        storm = app.state.storm
        if storm is not None and storm.status.running:
            return  # the storm director tickets the satellite on screen itself
        results = message["data"]["results"]
        ticket_for = getattr(source, "operator_ticket", None)
        trajectory_for = getattr(source, "trajectory", None)

        def compute() -> tuple[list[Any], Any | None]:
            tickets = [ticket_for(result["plan_id"]) for result in results] if ticket_for else []
            trajectory = None
            if results and trajectory_for:
                best = results[0]
                plausible = [hyp for hyp in best["plausible"] if hyp in best["per_hyp"]]
                if plausible:
                    worst = min(plausible, key=lambda hyp: best["per_hyp"][hyp]["min_soc_p5"])
                    trajectory = trajectory_for(best["plan_id"], worst)
            return tickets, trajectory

        tickets, trajectory = await asyncio.to_thread(compute)
        for ticket in tickets:
            publish("approval_request", ticket)
        if trajectory is not None:
            publish("trajectory", trajectory)

    def publish(kind: str, payload: Any) -> None:
        message = validate_message(kind, payload)
        loop = app.state.loop
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if loop is not None and running_loop is not loop:
            loop.call_soon_threadsafe(enqueue, message)
        else:
            enqueue(message)
        if kind == "matrix" and message["data"]["complete"] and loop is not None:
            if running_loop is loop:
                loop.create_task(after_matrix(message))
            else:
                loop.call_soon_threadsafe(lambda: loop.create_task(after_matrix(message)))

    app.state.publish = publish
    app.state.storm = (StormDirector(source, publish, transition)
                       if getattr(source, "supports_storm", False) else None)

    @app.websocket("/ws")
    async def websocket_fleet(websocket: WebSocket) -> None:
        await websocket.accept()
        app.state.loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        listeners.add(queue)
        next_fleet = time.monotonic()
        try:
            while True:
                now = time.monotonic()
                if now >= next_fleet:
                    if scenario_ready():
                        state = await asyncio.to_thread(source.fleet_state, clock.now(), clock.speed)
                        if state is not None:
                            await websocket.send_json(validate_message("fleet_state", state))
                        if hasattr(source, "drain_messages"):
                            events = source.drain_messages()
                            for event in events:
                                publish(event["type"], event["data"])
                            enter_agent_after_alerts(events)
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
            if not listeners:
                app.state.loop = None

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
    async def fault_storm(request: StormRequest | None = None) -> StormResponse:
        if not scenario_ready():
            raise HTTPException(status_code=503, detail="scenario is not ready")
        app.state.run_live = request.live if request else False
        storm = app.state.storm
        if storm is not None:
            if storm.busy:
                raise HTTPException(status_code=409, detail="a fault storm is already running")
            app.state.scenario_task = asyncio.create_task(
                asyncio.to_thread(source.start_storm, 'fault-storm', STORM_SEED))
            await app.state.scenario_task
            clock.reset(source.t0)
            storm.start(app.state.run_live)
            return StormResponse(started=True, alert_count=0)
        if getattr(source, 'background_scenario', False):
            app.state.scenario_task = asyncio.create_task(
                asyncio.to_thread(source.set_scenario, 'fault-storm', 1))
            await app.state.scenario_task
            clock.reset(source.t0)
            transition('storm')
            return StormResponse(started=True, alert_count=0)
        source.set_scenario('fault-storm', 1)
        clock.reset(source.t0)
        transition('storm')
        alerts = await asyncio.to_thread(source.storm_alerts, clock.now())
        if hasattr(source, "drain_messages"):
            events = source.drain_messages()
            for event in events:
                publish(event["type"], event["data"])
            enter_agent_after_alerts(events)
            return StormResponse(started=True, alert_count=sum(event["type"] == "alert" for event in events))
        events = []
        for alert in alerts:
            publish("alert", alert)
            events.append({'type': 'alert', 'data': alert})
            if alert.severity in ("warning", "critical"):
                publish("callout", CalloutMsg(text=f"{alert.sat_id} requires attention", priority=2))
        enter_agent_after_alerts(events)
        return StormResponse(started=True, alert_count=len(alerts))

    @app.post("/api/demo", response_model=DemoStateMsg)
    async def demo(request: DemoRequest) -> DemoStateMsg:
        return transition(request.state)

    @app.post("/api/approve", response_model=ApprovalRefused | ExecutionMsg)
    async def approve(request: ApproveRequest) -> ApprovalRefused | ExecutionMsg:
        result = await asyncio.to_thread(source.approve, request.approval_id, request.plan_hash)
        if isinstance(result, ApprovalRefused):
            publish("approval_refused", ApprovalRefusedMsg(
                approval_id=request.approval_id,
                reason_key=result.reason_key,
                failing_hypotheses=result.failing_hypotheses,
            ))
        else:
            publish("execution", result)
            if app.state.storm is not None:
                app.state.storm.approved(result.sat_id)
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
        result = await asyncio.to_thread(source.reveal, request.sat_id)
        if result is None:
            raise HTTPException(status_code=409, detail="truth can be revealed only after execution")
        message = RevealMsg.model_validate(result)
        publish("reveal", message)
        return message

    return app


app = create_app()
