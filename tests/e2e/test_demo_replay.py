"""Offline API smoke for the recorded fault-storm demo."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]


def _receive_until(ws, messages: list[dict], done, *, seconds: float = 160) -> None:
    deadline = time.monotonic() + seconds
    while not done(messages):
        messages.append(ws.receive_json())
        assert time.monotonic() < deadline, "fault-storm replay did not reach the expected state"


def _data(messages: list[dict], kind: str) -> list[dict]:
    return [message["data"] for message in messages if message["type"] == kind]


def test_fault_storm_replay_matrix_approval_and_execution(monkeypatch) -> None:
    monkeypatch.setenv("PENUMBRA_OFFLINE", "1")
    monkeypatch.setenv("PENUMBRA_REPLAY_PACE_MS", "0")
    monkeypatch.setenv("PENUMBRA_REPLAY", str(ROOT / "runs/demo/live-fault-storm.jsonl"))
    monkeypatch.delenv("PENUMBRA_API_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    from backend.api.app import create_app

    messages: list[dict] = []
    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws") as ws:
            started = client.post("/api/fault-storm", json={"live": False})
            assert started.status_code == 200
            assert started.json()["started"] is True

            def ready_for_approval(events: list[dict]) -> bool:
                return (
                    any(x["state"] == "approval" for x in _data(events, "demo_state"))
                    and any(x["sat_id"] == "SAT-07" and x["complete"] for x in _data(events, "matrix"))
                    and any(x["sat_id"] == "SAT-07" for x in _data(events, "approval_request"))
                    and any(x["sat_id"] == "SAT-12" and x["status"] == "auto_executed"
                            for x in _data(events, "execution"))
                )

            _receive_until(ws, messages, ready_for_approval)
            matrix_07 = next(
                x for x in reversed(_data(messages, "matrix"))
                if x["sat_id"] == "SAT-07" and x["complete"]
            )
            best = matrix_07["results"][0]
            tickets = {
                (x["sat_id"], x["plan_id"]): x
                for x in _data(messages, "approval_request")
            }
            ticket_07 = tickets[("SAT-07", best["plan_id"])]
            assert ticket_07["plan_hash"] == best["plan_hash"]

            approved = client.post("/api/approve", json={
                "approval_id": ticket_07["approval_id"],
                "plan_hash": ticket_07["plan_hash"],
            })
            assert approved.status_code == 200
            assert approved.json()["status"] == "executed"

            def revealed_with_tickets(events: list[dict]) -> bool:
                matrix_19 = [x for x in _data(events, "matrix") if x["sat_id"] == "SAT-19" and x["complete"]]
                ticket_keys = {
                    (x["sat_id"], x["plan_id"])
                    for x in _data(events, "approval_request")
                }
                return (
                    any(x["state"] == "reveal" for x in _data(events, "demo_state"))
                    and any(x["sat_id"] == "SAT-07" and x["status"] == "executed"
                            for x in _data(events, "execution"))
                    and bool(matrix_19)
                    and all(("SAT-19", result["plan_id"]) in ticket_keys for result in matrix_19[-1]["results"])
                )

            _receive_until(ws, messages, revealed_with_tickets)
            tickets.update({
                (x["sat_id"], x["plan_id"]): x
                for x in _data(messages, "approval_request")
            })
            matrix_19 = next(
                x for x in reversed(_data(messages, "matrix"))
                if x["sat_id"] == "SAT-19" and x["complete"]
            )
            red = matrix_19["results"][0]
            red_ticket = tickets[("SAT-19", red["plan_id"])]
            assert ("SAT-07", red["plan_id"]) in tickets  # IDs repeat across satellites.

            refused = client.post("/api/approve", json={
                "approval_id": red_ticket["approval_id"],
                "plan_hash": red_ticket["plan_hash"],
            })
            assert refused.status_code == 200
            assert refused.json()["refused"] is True
            assert refused.json()["reason_key"] == "failed_verification"
            assert refused.json()["failing_hypotheses"]
            _receive_until(
                ws, messages,
                lambda events: any(x["reason_key"] == "failed_verification"
                                   for x in _data(events, "approval_refused")),
            )
