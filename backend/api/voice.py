"""Voice callouts (T28): ElevenLabs speech for `callout` messages, served from a cache that works offline.

- The cache is `frontend/public/audio/<key>.mp3`, so the frontend (dev server and build) serves it as a static
  file at `/audio/<key>.mp3`; no API endpoint is involved and the replay-only server needs no key.
- `audio_url(text)` returns that URL when the clip is cached. If it is not and ELEVENLABS_API_KEY is set (and
  PENUMBRA_OFFLINE is not), the clip is synthesized in the background for the next time; this callout goes out
  as a caption only. Without a key everything degrades to captions, silently.
- `python -m backend.api.voice prewarm` plays the recorded storm headless, collects every callout text and fills
  the cache (needs the key once; commit the clips only if they are small).
The key is read from the environment only and never logged.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO / "frontend" / "public" / "audio"
URL_PREFIX = "/audio/"
API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")  # a premade voice; not a secret
MODEL_ID = "eleven_flash_v2_5"
TIMEOUT_S = 20.0
MAX_CHARS = 600  # longer narration is captioned only

log = logging.getLogger(__name__)
_KEY_RE = re.compile(r"[0-9a-f]{24}")


def cache_key(text: str) -> str:
    return hashlib.sha256(f"{VOICE_ID}|{MODEL_ID}|{text.strip()}".encode()).hexdigest()[:24]


class Voice:
    def __init__(self, cache_dir: str | Path = CACHE_DIR, api_key: str | None = None, client: Any = None,
                 offline: bool | None = None, background: bool = True) -> None:
        self.cache_dir = Path(cache_dir)
        self._api_key = api_key if api_key is not None else os.environ.get("ELEVENLABS_API_KEY", "")
        self._client = client
        self.offline = (os.environ.get("PENUMBRA_OFFLINE") == "1") if offline is None else offline
        self.background = background
        self._inflight: set[str] = set()
        self._lock = threading.Lock()
        self.failures = 0

    @property
    def can_synthesize(self) -> bool:
        return bool(self._api_key) and not self.offline

    def path(self, text: str) -> Path:
        return self.cache_dir / f"{cache_key(text)}.mp3"

    def audio_url(self, text: str) -> str | None:
        """URL of the cached clip, else None (and a background synthesis when a key is available)."""
        if not text or not text.strip():
            return None
        if self.path(text).is_file():
            return f"{URL_PREFIX}{cache_key(text)}.mp3"
        if self.can_synthesize and len(text) <= MAX_CHARS:
            key = cache_key(text)
            with self._lock:
                if key in self._inflight:
                    return None
                self._inflight.add(key)
            if self.background:
                threading.Thread(target=self._synthesize_once, args=(text, key), daemon=True).start()
            else:
                self._synthesize_once(text, key)
        return None

    def synthesize(self, text: str) -> bool:
        """Fetch one clip into the cache. Never raises; False if it could not be made."""
        if not self.can_synthesize or len(text) > MAX_CHARS:
            return False
        try:
            import httpx

            client = self._client or httpx.Client(timeout=TIMEOUT_S)
            response = client.post(
                API_URL.format(voice_id=VOICE_ID),
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": self._api_key, "accept": "audio/mpeg"},
                json={"text": text.strip(), "model_id": MODEL_ID},
            )
            if response.status_code != 200 or not response.content:
                raise RuntimeError(f"HTTP {response.status_code}")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            target = self.path(text)
            tmp = target.with_suffix(".part")
            tmp.write_bytes(response.content)
            tmp.replace(target)
            return True
        except Exception as exc:  # noqa: BLE001 - voice is optional; captions always work
            self.failures += 1
            if self.failures == 1:
                log.warning("voice callout unavailable (%s); captions only", type(exc).__name__)
            return False

    def _synthesize_once(self, text: str, key: str) -> None:
        try:
            self.synthesize(text)
        finally:
            with self._lock:
                self._inflight.discard(key)


def with_audio(payload: Any, voice: Voice | None) -> Any:
    """The callout payload with audio_url filled from the cache when it has none yet."""
    if voice is None:
        return payload
    data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
    if not data.get("audio_url"):
        url = voice.audio_url(data.get("text", ""))
        if url:
            data["audio_url"] = url
    return data


def storm_callouts() -> list[str]:
    """Every callout text the recorded storm produces (replay, pace 0)."""
    os.environ["PENUMBRA_REPLAY_PACE_MS"] = "0"
    from .real_provider import RealProvider
    from .storm import STORM_SEED, StormDirector

    texts: list[str] = []
    provider = RealProvider("single-stuck-load", 1)
    provider.start_storm("fault-storm", STORM_SEED)
    director = None

    def publish(kind: str, payload: Any) -> None:
        if kind == "callout":
            data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
            texts.append(data["text"])

    def transition(state: str) -> None:
        if state == "approval":  # approve the best plan at once, as the judge would
            threading.Thread(target=approve_best, daemon=True).start()

    def approve_best() -> None:
        pending = director.pending
        matrix = provider.backend.matrix(pending.sat_id, True)
        ticket = provider.ticket(pending.dispatcher, matrix.results[0].plan_id)
        result = provider.approve(ticket.approval_id, ticket.plan_hash)
        director.approved(result.sat_id)

    import tempfile

    with tempfile.TemporaryDirectory() as runs:
        director = StormDirector(provider, publish, transition, runs_dir=runs)
        director.start(live=False)
        director.join()
    return list(dict.fromkeys(texts))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] != ["prewarm"]:
        print("usage: python -m backend.api.voice prewarm")
        return 2
    voice = Voice(background=False)
    if not voice.can_synthesize:
        print("ELEVENLABS_API_KEY is not set (or PENUMBRA_OFFLINE=1): nothing to synthesize; captions only.")
        return 0
    texts = storm_callouts()
    made = sum(1 for t in texts if voice.path(t).is_file() or voice.synthesize(t))
    size = sum(p.stat().st_size for p in voice.cache_dir.glob("*.mp3"))
    print(f"{made}/{len(texts)} callouts cached in {voice.cache_dir} ({size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
