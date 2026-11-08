"""T43 PR F: POST /api/voice-command with a fake Gemini client (no network)."""
import json

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.providers import MockProvider
from backend.api.voice import Voice
from backend.api.voice_command import (
    MAX_BYTES, UNAVAILABLE, VoiceCommand, fault_menu, interpret, max_incidents, parse_fault, parse_sats,
    system_prompt,
)

CLIP = b"\x1aE\xdf\xa3" + bytes(range(256)) * 8 + b"\r\n--not-a-boundary\r\n"  # webm magic + binary noise


class FakeModels:
    def __init__(self, reply="", error=None):
        self.reply, self.error, self.calls = reply, error, []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return type("Response", (), {"text": self.reply})()


class FakeClient:
    def __init__(self, reply="", error=None):
        self.models = FakeModels(reply, error)


def api(tmp_path, voice_command):
    app = create_app(provider=MockProvider(t0=1_800_000_000.0), voice=Voice(cache_dir=tmp_path, offline=True),
                     voice_command=voice_command)
    return TestClient(app)


def post(client, data=CLIP, mime="audio/webm;codecs=opus", field="audio"):
    return client.post("/api/voice-command", files={field: ("clip.webm", data, mime)})


def test_model_json_is_validated_against_ids_and_menu(tmp_path):
    reply = json.dumps({"transcript": "stuck load on satellite seven and twelve",
                        "sats": ["SAT-07", "sat-12", "SAT-99", "SAT-07"], "fault": "stuck_on_load"})
    fake = FakeClient(reply)
    with api(tmp_path, VoiceCommand(client=fake, model="gemini-test")) as client:
        r = post(client)
    assert r.status_code == 200
    assert r.json() == {"transcript": "stuck load on satellite seven and twelve", "sats": ["SAT-07", "SAT-12"],
                        "fault": "stuck_on_load", "notes": "Heard 2 satellites and a fault"}
    call = fake.models.calls[0]
    assert call["model"] == "gemini-test"
    part = call["contents"][0].parts[0]
    assert part.inline_data.data == CLIP and part.inline_data.mime_type == "audio/webm"
    prompt = call["config"].system_instruction
    assert "SAT-01" in prompt and "SAT-24" in prompt and all(f["id"] in prompt for f in fault_menu())
    assert call["config"].response_mime_type == "application/json"


def test_unknown_fault_and_no_sats_fall_back_to_the_transcript(tmp_path):
    reply = json.dumps({"transcript": "battery capacity fade on sat nineteen", "sats": [], "fault": "meteor"})
    with api(tmp_path, VoiceCommand(client=FakeClient(reply), model="gemini-test")) as client:
        body = post(client, mime="audio/ogg").json()
    assert (body["sats"], body["fault"]) == (["SAT-19"], "capacity_fade")


def test_non_json_reply_and_nothing_recognised():
    assert interpret("```json\n{\"transcript\": \"solar panels on sat 3\", \"sats\": null}\n```").sats == ["SAT-03"]
    out = interpret("I could not hear anything useful")
    assert (out.sats, out.fault, out.notes) == ([], None, "Nothing recognised, pick on the picker")
    assert interpret(json.dumps({"transcript": "satellite seventeen"})).notes == \
        "Heard 1 satellite. No fault recognised, pick one"
    assert interpret(json.dumps({"transcript": "sensor bias", "sats": ["SAT-4", "5", 6, "SAT-08"]})).sats == \
        ["SAT-04", "SAT-05", "SAT-06"]


@pytest.mark.parametrize("utterance, sats, fault", [
    ("stuck load on satellite seven and twelve", ["SAT-07", "SAT-12"], "stuck_on_load"),
    ("solar array degradation on SAT 3", ["SAT-03"], "array_degradation"),
    ("battery capacity fade on satellites nineteen, twenty one and twenty-four", ["SAT-19", "SAT-21", "SAT-24"],
     "capacity_fade"),
    ("SOC sensor bias on sat 07", ["SAT-07"], "sensor_bias"),
    ("two satellites, five and eleven, have a stuck aux load", ["SAT-05", "SAT-11"], "stuck_on_load"),
    ("panels degrading on satellite 30 and satellite 8", ["SAT-08"], "array_degradation"),
    ("capacity fading on satellites 1 2 3 4", ["SAT-01", "SAT-02", "SAT-03"], "capacity_fade"),
    ("the reading is biased on satellite zero nine", ["SAT-09"], "sensor_bias"),
    ("satellite seventeen", ["SAT-17"], None),
    ("something is wrong", [], None),
    ("a stuck load drains the battery on seven, seven and seven", ["SAT-07"], "stuck_on_load"),
])
def test_fallback_parser(utterance, sats, fault):
    assert parse_sats(utterance) == sats
    assert parse_fault(utterance) == fault


def test_503_without_key_or_offline(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for vc in (VoiceCommand(model="gemini-test", api_key=""), VoiceCommand(model="gemini-test", api_key="k",
                                                                           offline=True)):
        assert not vc.available
        with api(tmp_path, vc) as client:
            r = post(client)
        assert (r.status_code, r.json()["detail"]) == (503, UNAVAILABLE)


def test_size_type_and_field_limits(tmp_path):
    fake = FakeClient(json.dumps({"transcript": "x"}))
    with api(tmp_path, VoiceCommand(client=fake, model="gemini-test")) as client:
        assert post(client, data=b"\0" * (MAX_BYTES + 1)).status_code == 413
        assert post(client, data=b"\0" * (MAX_BYTES + 200_000)).status_code == 413
        assert post(client, mime="audio/wav").status_code == 415
        assert post(client, mime="text/plain").status_code == 415
        assert post(client, field="clip").status_code == 400
        assert post(client, data=b"").status_code == 400
        assert client.post("/api/voice-command", content=CLIP, headers={"content-type": "audio/webm"}).status_code == 400
    assert fake.models.calls == []


def test_model_failure_is_502_without_the_key_or_audio(tmp_path):
    vc = VoiceCommand(client=FakeClient(error=RuntimeError("429 quota for key sk-secret")), model="gemini-test",
                      api_key="sk-secret")
    with api(tmp_path, vc) as client:
        r = post(client)
    assert r.status_code == 502 and r.json()["detail"] == "voice command failed (RuntimeError)"
    assert "sk-secret" not in r.text


def test_prompt_lists_menu_and_limit():
    prompt = system_prompt()
    assert "up to 3 satellites" in prompt and "SAT-07" in prompt and "null" in prompt


def test_limit_and_menu_follow_the_scenario_module(tmp_path, monkeypatch):
    from backend.fleet import scenarios

    monkeypatch.setattr(scenarios, "MAX_INCIDENTS", 2, raising=False)
    monkeypatch.setattr(scenarios, "FAULT_MENU", [{"id": "stuck_on_load", "label": "Stuck-on load (AUX)",
                                                   "kind": "stuck_on_load", "params": {}}], raising=False)
    assert max_incidents() == 2 and [f["id"] for f in fault_menu()] == ["stuck_on_load"]
    reply = json.dumps({"transcript": "solar array on satellites one two three", "sats": [], "fault": "array_degradation"})
    fake = FakeClient(reply)
    with api(tmp_path, VoiceCommand(client=fake, model="gemini-test")) as client:
        body = post(client).json()
    assert body["sats"] == ["SAT-01", "SAT-02"] and body["fault"] is None
    assert "up to 2 satellites" in fake.models.calls[0]["config"].system_instruction
