"""T35: the FAULT STORM trigger plays the recorded storm through the API in paced replay (here PACE 0), with the
judge's Approve as the only required click: storm -> agent (SAT-12, Tier 1) -> storm -> agent (SAT-07) ->
approval -> storm -> agent (SAT-19, infeasible) -> fastforward -> reveal."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.real_provider import RealProvider
from backend.api.storm import STORM_RUN, StormDirector
from contracts import DEMO_SPEED

pytestmark = pytest.mark.skipif(not STORM_RUN.exists(), reason="recorded storm run not present")


def recorded_execution_hashes():
    rows = [json.loads(line) for line in Path(STORM_RUN).read_text(encoding="utf-8").splitlines()]
    return {r["payload"]["sat_id"]: r["plan_hash"] for r in rows if r["kind"] == "execution"}


def receive_until(ws, seen, stop, limit=4000):
    for _ in range(limit):
        message = ws.receive_json()
        seen.append(message)
        if stop(message):
            return message
    raise AssertionError(f"stop condition never met; last types {[m['type'] for m in seen[-10:]]}")


def demo_state(name):
    return lambda m: m["type"] == "demo_state" and m["data"]["state"] == name


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("PENUMBRA_REPLAY_PACE_MS", "0")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = RealProvider("single-stuck-load", 1)
    app = create_app(provider=provider)
    app.state.storm.runs_dir = tmp_path
    with TestClient(app) as client:
        yield client


def test_fault_storm_plays_the_recorded_run_with_one_approval_click(api):
    seen = []
    with api.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "fleet_state"
        response = api.post("/api/fault-storm", json={"live": False})
        assert response.status_code == 200 and response.json()["started"] is True
        assert api.post("/api/fault-storm", json={"live": False}).status_code == 409  # one storm at a time

        receive_until(ws, seen, demo_state("approval"))
        types = [m["type"] for m in seen]
        states = [m["data"]["state"] for m in seen if m["type"] == "demo_state"]
        assert states == ["storm", "agent", "storm", "agent", "approval"]
        speeds = {m["data"]["state"]: m["data"]["speed"] for m in seen if m["type"] == "demo_state"}
        assert speeds == {s: DEMO_SPEED[s] for s in speeds}  # agent and approval are paused (sim time frozen)
        for kind in ("alert", "callout", "trace_event", "hypotheses", "matrix", "schedule", "execution",
                     "approval_request", "trajectory"):
            assert kind in types, kind

        auto = next(m["data"] for m in seen if m["type"] == "execution")
        assert (auto["sat_id"], auto["status"], auto["flight_rule"]) == ("SAT-12", "auto_executed", "FR-2")
        matrix = [m["data"] for m in seen if m["type"] == "matrix" and m["data"]["complete"]
                  and m["data"]["sat_id"] == "SAT-07"][-1]
        tickets = {m["data"]["plan_id"]: m["data"] for m in seen if m["type"] == "approval_request"}
        assert set(tickets) == {r["plan_id"] for r in matrix["results"]}
        assert all(t["sat_id"] == "SAT-07" and t["tier"] == 2 for t in tickets.values())
        best = tickets[matrix["results"][0]["plan_id"]]
        hashes = recorded_execution_hashes()
        assert best["plan_hash"] == hashes["SAT-07"]  # the replay reproduces the recorded plan exactly

        # sim time is frozen while the judge decides
        t_a = ws.receive_json()
        while t_a["type"] != "fleet_state":
            t_a = ws.receive_json()
        assert api.post("/api/demo", json={"state": "reveal"}).json()["state"] == "approval"  # director owns it

        executed = api.post("/api/approve", json={"approval_id": best["approval_id"], "plan_hash": best["plan_hash"]})
        assert executed.status_code == 200 and executed.json()["status"] == "executed"
        receive_until(ws, seen, demo_state("reveal"))
        states = [m["data"]["state"] for m in seen if m["type"] == "demo_state"]
        assert states[5:] == ["storm", "agent", "fastforward", "reveal"]
        assert any(m["type"] == "execution" and m["data"]["sat_id"] == "SAT-07" for m in seen)
        last_fleet = [m["data"] for m in seen if m["type"] == "fleet_state"][-1]
        status = {s["sat_id"]: s["status"] for s in last_fleet["sats"]}
        assert (status["SAT-12"], status["SAT-07"], status["SAT-19"]) == (
            "auto_executed", "executed", "safe_mode_recommended")

        # the physics veto on SAT-19 (infeasible): every plan has a ticket, a red one is refused in reveal
        sat19 = [m["data"] for m in seen if m["type"] == "matrix" and m["data"]["complete"]
                 and m["data"]["sat_id"] == "SAT-19"][-1]
        red = {m["data"]["plan_id"]: m["data"] for m in seen
               if m["type"] == "approval_request" and m["data"]["sat_id"] == "SAT-19"}
        assert set(red) == {r["plan_id"] for r in sat19["results"]}
        assert not any(r["passes_all"] for r in sat19["results"])
        veto = red[sat19["results"][0]["plan_id"]]
        refused = api.post("/api/approve", json={"approval_id": veto["approval_id"], "plan_hash": veto["plan_hash"]})
        body = refused.json()
        assert body["refused"] is True and body["reason_key"] == "failed_verification"
        assert body["failing_hypotheses"]
        msg = receive_until(ws, seen, lambda m: m["type"] == "approval_refused")
        assert msg["data"] == {"approval_id": veto["approval_id"], "reason_key": "failed_verification",
                               "failing_hypotheses": body["failing_hypotheses"]}
        assert api.app.state.demo_state == "reveal"

    reveal = api.post("/api/reveal", json={"sat_id": "SAT-07"})
    assert reveal.status_code == 200 and reveal.json()["truth"]["kind"] == "stuck_on_load"
    storm = api.app.state.storm
    assert storm.status.error is None and storm.status.mode == "replay"
    assert storm.status.outcomes["SAT-12"]["plan_hash"] == hashes["SAT-12"]
    assert storm.status.outcomes["SAT-19"]["rank_status"] == "infeasible"
    assert Path(storm.status.path).exists()  # this run is recorded too


def test_run_live_without_gemini_config_falls_back_to_replay(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    published = []
    director = StormDirector(provider=None, publish=lambda k, p: published.append((k, p)), transition=lambda s: s)
    model, mode = director.choose_model(live=True)
    assert mode == "replay" and type(model).__name__ == "ReplayModel"
    assert published and published[0][0] == "callout" and "replaying" in published[0][1].text


def test_run_live_uses_gemini_when_configured(monkeypatch):
    from backend.agent import gemini

    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setattr(gemini, "_make_client", lambda api_key: object())  # no network
    director = StormDirector(provider=None, publish=lambda k, p: None, transition=lambda s: s)
    model, mode = director.choose_model(live=True)
    assert mode == "live" and model.name == "gemini-3.5-flash-lite"
