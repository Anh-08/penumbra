"""T42 PR B: the judge's incident through the API. GET /api/incident/options, POST /api/incident (live only: a custom
incident has no recording), and the director working the chosen satellites. The model here is the scripted stub
(no network); the director is told it is live."""
import pytest
from fastapi.testclient import TestClient

from backend.agent.stub import StubModel
from backend.api import app as app_module
from backend.api.app import create_app
from backend.api.real_provider import RealProvider
from backend.api.storm import StormDirector
from backend.fleet.scenarios import FAULT_MENU, MAX_INCIDENTS
from contracts import SAT_IDS

NO_KEY = "live planning is unavailable on this server (no model key)"


def receive_until(ws, seen, stop, limit=6000):
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
    app = create_app(provider=RealProvider("single-stuck-load", 1))
    app.state.storm.runs_dir = tmp_path
    with TestClient(app) as client:
        yield client


@pytest.fixture
def live(monkeypatch):
    """The server has a model key (stubbed: the stub model stands in for Gemini, nothing goes out)."""
    monkeypatch.setattr(app_module, "live_available", lambda: True)
    monkeypatch.setattr(StormDirector, "choose_model", lambda self, live: (StubModel(), "live"))


def test_live_available_needs_a_key_and_not_offline(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("PENUMBRA_OFFLINE", raising=False)
    assert app_module.live_available() is False
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    assert app_module.live_available() is True
    monkeypatch.setenv("PENUMBRA_OFFLINE", "1")
    assert app_module.live_available() is False


def test_options_list_the_fleet_and_the_fault_menu(api, monkeypatch):
    body = api.get("/api/incident/options").json()
    assert body["sats"] == list(SAT_IDS)
    assert body["faults"] == [{"id": f["id"], "label": f["label"]} for f in FAULT_MENU]
    assert {f["id"] for f in body["faults"]} == {"array_degradation", "stuck_on_load", "capacity_fade", "sensor_bias"}
    assert body["max_incidents"] == MAX_INCIDENTS == 3
    assert body["live_available"] is False  # no key in this test
    monkeypatch.setattr(app_module, "live_available", lambda: True)
    assert api.get("/api/incident/options").json()["live_available"] is True


def test_incident_without_a_model_key_is_409_and_never_replays(api):
    response = api.post("/api/incident", json={"incidents": [{"sat_id": "SAT-07", "fault": "stuck_on_load"}]})
    assert response.status_code == 409 and response.json()["detail"] == NO_KEY
    assert not api.app.state.storm.busy


@pytest.mark.parametrize("incidents", [
    [],
    [{"sat_id": "SAT-07", "fault": "gremlins"}],
    [{"sat_id": "SAT-25", "fault": "stuck_on_load"}],
    [{"sat_id": "SAT-07", "fault": "stuck_on_load"}, {"sat_id": "SAT-07", "fault": "sensor_bias"}],
    [{"sat_id": s, "fault": "stuck_on_load"} for s in ("SAT-01", "SAT-02", "SAT-03", "SAT-04")],
    [{"sat_id": "SAT-07"}],
])
def test_bad_incidents_are_422(api, live, incidents):
    assert api.post("/api/incident", json={"incidents": incidents, "seed": 0}).status_code == 422
    assert not api.app.state.storm.busy


def test_a_custom_incident_never_falls_back_to_the_recording(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    director = StormDirector(provider=None, publish=lambda k, p: None, transition=lambda s: s)
    director._replay_ok = False  # what start(live, sats) sets for the judge's satellites
    with pytest.raises(RuntimeError, match="live planning is unavailable"):
        director.choose_model(live=True)


def test_one_incident_runs_live_to_reveal_with_an_approval_for_the_chosen_satellite(api, live):
    seen = []
    with api.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "fleet_state"
        response = api.post("/api/incident", json={"incidents": [{"sat_id": "SAT-07", "fault": "stuck_on_load"}],
                                                   "seed": 0})
        assert response.status_code == 200
        assert response.json() == {"started": True, "alert_count": 0, "mode": "live"}
        busy = api.post("/api/incident", json={"incidents": [{"sat_id": "SAT-03", "fault": "sensor_bias"}]})
        assert busy.status_code == 409 and busy.json()["detail"] == "incident is already running"

        receive_until(ws, seen, demo_state("approval"))
        states = [m["data"]["state"] for m in seen if m["type"] == "demo_state"]
        assert states == ["storm", "agent", "approval"]
        alerts = {m["data"]["sat_id"] for m in seen if m["type"] == "alert"}
        assert "SAT-07" in alerts
        tickets = [m["data"] for m in seen if m["type"] == "approval_request"]
        assert tickets and all(t["sat_id"] == "SAT-07" and t["tier"] == 2 for t in tickets)
        matrix = [m["data"] for m in seen if m["type"] == "matrix" and m["data"]["complete"]
                  and m["data"]["sat_id"] == "SAT-07"][-1]
        best = next(t for t in tickets if t["plan_id"] == matrix["results"][0]["plan_id"])

        executed = api.post("/api/approve", json={"approval_id": best["approval_id"], "plan_hash": best["plan_hash"]})
        assert executed.status_code == 200 and executed.json()["status"] == "executed"
        receive_until(ws, seen, demo_state("reveal"))
        states = [m["data"]["state"] for m in seen if m["type"] == "demo_state"]
        assert states[3:] == ["fastforward", "reveal"]

    reveal = api.post("/api/reveal", json={"sat_id": "SAT-07"})
    assert reveal.status_code == 200 and reveal.json()["truth"]["kind"] == "stuck_on_load"
    storm = api.app.state.storm
    assert storm.status.error is None and storm.status.mode == "live"
    assert set(storm.status.outcomes) == {"SAT-07"} and storm.status.outcomes["SAT-07"]["executed"] is True
    assert storm.status.run_id.startswith("incident-")
    assert api.app.state.provider.scenario_name == "custom"
