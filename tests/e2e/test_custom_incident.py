"""The operator's chosen fault crosses the API, agent and human approval boundary offline."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient


def test_operator_incident_options_to_reveal_with_fake_model(monkeypatch, tmp_path):
    from backend.agent import session
    from backend.agent.stub import StubModel
    from backend.api import app as api_module
    from backend.api.app import create_app
    from backend.api.real_provider import RealProvider

    monkeypatch.setenv("PENUMBRA_OFFLINE", "1")
    monkeypatch.setenv("PENUMBRA_REPLAY_PACE_MS", "0")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    # Model availability is simulated; the production director/model-selection path still runs.
    monkeypatch.setattr(api_module, "live_available", lambda: True)
    model_requests = []

    def fake_model(*, live):
        model_requests.append(live)
        return StubModel()

    monkeypatch.setattr(session, "select_model", fake_model)
    app = create_app(provider=RealProvider("single-stuck-load", 0))
    app.state.storm.runs_dir = tmp_path
    seen = []

    def until(ws, state):
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            message = ws.receive_json()
            seen.append(message)
            if message["type"] == "demo_state" and message["data"]["state"] == state:
                return
            if message["type"] == "callout":
                assert not message["data"]["text"].startswith("Incident stopped:")
        raise AssertionError(f"incident never reached {state}")

    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        options = client.get("/api/incident/options")
        assert options.status_code == 200
        choices = options.json()
        assert choices["live_available"] is True
        assert choices["max_incidents"] == 3
        assert choices["sats"] == [f"SAT-{i:02}" for i in range(1, 25)]
        assert {fault["id"] for fault in choices["faults"]} == {
            "array_degradation", "stuck_on_load", "capacity_fade", "sensor_bias",
        }
        started = client.post("/api/incident", json={
            "incidents": [{"sat_id": "SAT-07", "fault": "stuck_on_load"}], "seed": 0,
        })
        assert started.status_code == 200
        assert started.json()["mode"] == "live" and started.json()["started"] is True
        until(ws, "approval")
        assert model_requests == [True]
        assert any(m["type"] == "alert" and m["data"]["sat_id"] == "SAT-07" for m in seen)
        tickets = {(m["data"]["sat_id"], m["data"]["plan_id"]): m["data"]
                   for m in seen if m["type"] == "approval_request"}
        matrix = next(m["data"] for m in reversed(seen) if m["type"] == "matrix"
                      and m["data"]["sat_id"] == "SAT-07" and m["data"]["complete"])
        best = matrix["results"][0]
        ticket = tickets[("SAT-07", best["plan_id"])]
        assert ticket["tier"] == 2 and ticket["plan_hash"] == best["plan_hash"]
        approved = client.post("/api/approve", json={
            "approval_id": ticket["approval_id"], "plan_hash": ticket["plan_hash"],
        })
        assert approved.status_code == 200 and approved.json()["status"] == "executed"
        until(ws, "reveal")
        reveal = client.post("/api/reveal", json={"sat_id": "SAT-07"})
        assert reveal.status_code == 200
        assert reveal.json()["truth"]["kind"] == "stuck_on_load"
        assert app.state.storm.status.mode == "live"
        assert app.state.storm.status.error is None
        assert app.state.storm.status.run_id.startswith("incident-")
