"""T28: ElevenLabs voice for callouts, cached so it works offline; captions only when there is no clip or key.
No network: a fake HTTP client stands in for ElevenLabs."""
from fastapi.testclient import TestClient

from backend.api import voice as v
from backend.api.app import create_app
from backend.api.providers import MockProvider
from backend.api.voice import URL_PREFIX, Voice, cache_key, with_audio

SECRET = "sk-not-a-real-elevenlabs-key"


class FakeResponse:
    def __init__(self, status_code=200, content=b"ID3fake-mp3"):
        self.status_code, self.content = status_code, content


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response or FakeResponse(), error, []

    def post(self, url, params=None, headers=None, json=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "json": json})
        if self.error:
            raise self.error
        return self.response


def test_cached_clip_gives_an_audio_url_offline_and_without_a_key(tmp_path):
    text = "Flight, SAT-07 battery trending low, 42 minutes to critical"
    (tmp_path / f"{cache_key(text)}.mp3").write_bytes(b"ID3")
    voice = Voice(tmp_path, api_key="", offline=True)
    assert voice.audio_url(text) == f"{URL_PREFIX}{cache_key(text)}.mp3"


def test_no_clip_and_no_key_means_captions_only_without_errors(tmp_path):
    client = FakeClient()
    voice = Voice(tmp_path, api_key="", client=client, offline=False, background=False)
    assert voice.audio_url("SAT-12 requires attention") is None
    assert client.calls == [] and list(tmp_path.iterdir()) == []


def test_offline_never_calls_the_network_even_with_a_key(tmp_path):
    client = FakeClient()
    voice = Voice(tmp_path, api_key=SECRET, client=client, offline=True, background=False)
    assert voice.audio_url("SAT-12 requires attention") is None and client.calls == []


def test_missing_clip_is_synthesized_once_and_served_next_time(tmp_path):
    client = FakeClient()
    voice = Voice(tmp_path, api_key=SECRET, client=client, offline=False, background=False)
    text = "SAT-19 is infeasible; recommend SAFE_MODE."
    assert voice.audio_url(text) is None  # this callout goes out as a caption
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == v.API_URL.format(voice_id=v.VOICE_ID)
    assert call["headers"]["xi-api-key"] == SECRET and call["json"] == {"text": text, "model_id": v.MODEL_ID}
    assert voice.audio_url(text) == f"{URL_PREFIX}{cache_key(text)}.mp3"
    assert len(client.calls) == 1  # cached now


def test_tts_failures_degrade_to_captions_and_never_leak_the_key(tmp_path, caplog):
    for client in (FakeClient(FakeResponse(401, b"")), FakeClient(error=RuntimeError(f"boom {SECRET}"))):
        voice = Voice(tmp_path, api_key=SECRET, client=client, offline=False, background=False)
        assert voice.audio_url("SAT-07 requires attention") is None
        assert voice.failures == 1
    assert SECRET not in caplog.text and list(tmp_path.glob("*.mp3")) == []


def test_long_narration_is_captioned_only(tmp_path):
    client = FakeClient()
    voice = Voice(tmp_path, api_key=SECRET, client=client, offline=False, background=False)
    assert voice.audio_url("word " * 200) is None and client.calls == []


def test_callout_messages_carry_the_cached_audio_url(tmp_path):
    text = "SAT-07 requires attention"
    (tmp_path / f"{cache_key(text)}.mp3").write_bytes(b"ID3")
    voice = Voice(tmp_path, api_key="", offline=True)
    assert with_audio({"text": text, "priority": 2}, voice)["audio_url"] == f"/audio/{cache_key(text)}.mp3"
    assert "audio_url" not in with_audio({"text": "uncached", "priority": 2}, voice)
    app = create_app(provider=MockProvider(t0=1_800_000_000.0), voice=voice)
    with TestClient(app) as api, api.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "fleet_state"
        api.app.state.publish("callout", {"text": text, "priority": 2})
        message = ws.receive_json()
        while message["type"] != "callout":
            message = ws.receive_json()
        assert message["data"] == {"text": text, "priority": 2, "audio_url": f"/audio/{cache_key(text)}.mp3"}


def test_prewarm_without_a_key_is_a_no_op(monkeypatch, capsys):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert v.main(["prewarm"]) == 0
    assert "captions only" in capsys.readouterr().out
