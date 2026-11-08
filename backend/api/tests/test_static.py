"""T32: the API can serve the built frontend from the same origin (deploy/), without shadowing /api or /ws."""
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.providers import MockProvider


def test_static_frontend_is_served_next_to_the_api(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<!doctype html><title>Penumbra</title>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("PENUMBRA_STATIC_DIR", str(tmp_path))
    client = TestClient(create_app(MockProvider()))
    assert "Penumbra" in client.get("/").text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/bench").status_code == 200          # API routes still win
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"]


def test_no_static_mount_without_the_env(monkeypatch):
    monkeypatch.delenv("PENUMBRA_STATIC_DIR", raising=False)
    client = TestClient(create_app(MockProvider()))
    assert client.get("/").status_code == 404
