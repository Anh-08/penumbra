"""Voice command for the incident picker (T43 PR F): POST /api/voice-command.

The browser posts one push-to-talk clip (multipart field `audio`, webm/ogg opus from MediaRecorder, at most 1 MB,
about 15 s). One `generateContent` call to GEMINI_MODEL gets the audio inline and a system prompt that lists the
satellite ids and the fault menu, and returns strict JSON {transcript, sats, fault}. The answer is validated
(satellite ids that exist, at most MAX_INCIDENTS; a fault id from the menu or null), and anything the model left
empty is filled by a deterministic parser over the transcript (digits and number words -> SAT-xx, keywords ->
fault). Nothing starts by itself: the picker shows the result and the operator presses START INCIDENT.

- 503 `voice command unavailable (no model key)` when GEMINI_API_KEY is unset or PENUMBRA_OFFLINE=1.
- 413 over 1 MB, 415 for anything but audio/webm or audio/ogg, 400 without an `audio` field, 502 if the model call
  fails. The audio and the key are never logged or echoed.
- The multipart body is parsed here (one file field) so the API needs no extra dependency (python-multipart).
- The fault menu is `scenarios.FAULT_MENU` (T42 PR A) when it exists, else the same four ids.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel

from contracts import SAT_IDS

MAX_BYTES = 1_048_576
MAX_INCIDENTS = 3
ALLOWED_TYPES = ("audio/webm", "audio/ogg")
UNAVAILABLE = "voice command unavailable (no model key)"

_FALLBACK_MENU = (
    {"id": "array_degradation", "label": "Solar array degradation"},
    {"id": "stuck_on_load", "label": "Stuck-on load (AUX)"},
    {"id": "capacity_fade", "label": "Battery capacity fade"},
    {"id": "sensor_bias", "label": "SOC sensor bias"},
)
# Spoken keywords per fault (a word matches when it starts with one, so "panels", "loads", "biased" count).
KEYWORDS = {
    "array_degradation": ("array", "solar", "panel", "degrad"),
    "stuck_on_load": ("stuck", "load", "aux"),
    "capacity_fade": ("capacity", "fade", "fading", "faded", "batter"),
    "sensor_bias": ("sensor", "bias", "reading", "soc"),
}
_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
_NUMBER_WORDS = {**_UNITS, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
                 "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20}
_ZERO_WORDS = {"zero", "oh"}


class VoiceCommandResponse(BaseModel):
    transcript: str
    sats: list[str]
    fault: str | None
    notes: str


def fault_menu() -> list[dict[str, str]]:
    """[{id, label}] from scenarios.FAULT_MENU when T42 PR A is in, else the storm's four faults."""
    try:
        from backend.fleet import scenarios

        menu = getattr(scenarios, "FAULT_MENU", None)
    except Exception:  # noqa: BLE001 - the voice command must not depend on the scenario module importing
        menu = None
    if menu:
        return [{"id": str(f["id"]), "label": str(f.get("label", f["id"]))} for f in menu]
    return [dict(f) for f in _FALLBACK_MENU]


# Deterministic parser ---------------------------------------------------------------------------------------
def parse_sats(text: str, max_incidents: int = MAX_INCIDENTS) -> list[str]:
    """Satellite ids said in `text`, in order, at most `max_incidents`.

    Digits and number words 1-24 ("seven", "twenty one", "zero nine", "SAT-07"). When the text names satellites
    ("satellite", "sat"), only numbers from there on count, so "two satellites, five and eleven" is SAT-05, SAT-11.
    """
    tokens = re.findall(r"[a-z]+|\d+", text.lower())
    cue = next((i for i, tok in enumerate(tokens) if tok.startswith("sat")), None)
    if cue is not None:
        tokens = tokens[cue:]
    found: list[str] = []
    i = 0
    while i < len(tokens):
        tok, n = tokens[i], None
        if tok.isdigit():
            n = int(tok)
        elif tok == "twenty" and i + 1 < len(tokens) and tokens[i + 1] in ("one", "two", "three", "four"):
            n, i = 20 + _UNITS[tokens[i + 1]], i + 1
        elif tok in _ZERO_WORDS and i + 1 < len(tokens) and tokens[i + 1] in _UNITS:
            n, i = _UNITS[tokens[i + 1]], i + 1
        elif tok in _NUMBER_WORDS:
            n = _NUMBER_WORDS[tok]
        i += 1
        if n is not None and 1 <= n <= 24:
            sat = f"SAT-{n:02d}"
            if sat in SAT_IDS and sat not in found:
                found.append(sat)
    return found[:max_incidents]


def parse_fault(text: str, menu_ids: list[str] | None = None) -> str | None:
    """The fault whose keywords occur most often in `text` (ties: the one said first); None if no keyword."""
    ids = menu_ids if menu_ids is not None else [f["id"] for f in fault_menu()]
    words = re.findall(r"[a-z]+", text.lower())
    best: tuple[int, int, str] | None = None
    for fault in ids:
        hits = [i for i, w in enumerate(words) if any(w.startswith(k) for k in KEYWORDS.get(fault, ()))]
        if hits and (best is None or (-len(hits), hits[0]) < (best[0], best[1])):
            best = (-len(hits), hits[0], fault)
    return best[2] if best else None


def _sat_id(value: Any) -> str | None:
    m = re.fullmatch(r"\s*(?:sat[-_ ]?)?(\d{1,2})\s*", str(value), flags=re.IGNORECASE)
    return f"SAT-{int(m.group(1)):02d}" if m else None


def notes_for(sats: list[str], fault: str | None) -> str:
    n = len(sats)
    heard = f"{n} satellite{'s' if n != 1 else ''}"
    if sats and fault:
        return f"Heard {heard} and a fault"
    if sats:
        return f"Heard {heard}. No fault recognised, pick one"
    if fault:
        return "Heard a fault. No satellite recognised, pick one"
    return "Nothing recognised, pick on the picker"


def interpret(raw: str | None, max_incidents: int = MAX_INCIDENTS) -> VoiceCommandResponse:
    """Validate the model's JSON against the ids and the menu; fill what is missing from the transcript."""
    menu_ids = [f["id"] for f in fault_menu()]
    text = (raw or "").strip()
    try:
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except ValueError:
        data = {"transcript": text[:300]}
    transcript = str(data.get("transcript") or "").strip()
    sats: list[str] = []
    for value in data.get("sats") or []:
        sat = _sat_id(value)
        if sat in SAT_IDS and sat not in sats:
            sats.append(sat)
    sats = sats[:max_incidents] or parse_sats(transcript, max_incidents)
    fault = data.get("fault") if data.get("fault") in menu_ids else None
    fault = fault or parse_fault(transcript, menu_ids)
    return VoiceCommandResponse(transcript=transcript, sats=sats, fault=fault, notes=notes_for(sats, fault))


# Model call ---------------------------------------------------------------------------------------------------
def system_prompt(max_incidents: int = MAX_INCIDENTS) -> str:
    faults = "\n".join(f'- "{f["id"]}": {f["label"]} (spoken as: {", ".join(KEYWORDS.get(f["id"], ()))})'
                       for f in fault_menu())
    return (
        "You turn one short spoken command from a satellite operator into JSON. The operator names up to "
        f"{max_incidents} satellites and one fault to inject into a simulation.\n"
        f"Satellite ids: {SAT_IDS[0]} to {SAT_IDS[-1]} (\"satellite seven\" or \"sat oh seven\" is SAT-07).\n"
        f"Faults (use the id):\n{faults}\n"
        'Reply with JSON only: {"transcript": "<what was said, verbatim>", "sats": ["SAT-07"], '
        '"fault": "<fault id or null>"}. Use null for the fault and [] for sats when nothing matches. '
        "Never invent satellites or faults."
    )


class VoiceCommand:
    """Gemini client holder. A test passes `client` (anything with .models.generate_content)."""

    def __init__(self, client: Any = None, model: str | None = None, api_key: str | None = None,
                 offline: bool | None = None) -> None:
        self._client = client
        self.model = model if model is not None else os.environ.get("GEMINI_MODEL", "")
        self._api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        self.offline = (os.environ.get("PENUMBRA_OFFLINE") == "1") if offline is None else offline

    @property
    def available(self) -> bool:
        if self._client is not None:
            return True
        return bool(self._api_key) and bool(self.model) and not self.offline

    def _get_client(self) -> Any:
        if self._client is None:
            from backend.agent.gemini import _make_client, pinned_model_name

            self.model = pinned_model_name(self.model)
            self._client = _make_client(self._api_key)
        return self._client

    def transcribe(self, audio: bytes, mime: str) -> VoiceCommandResponse:
        from google.genai import types

        client = self._get_client()
        response = client.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user", parts=[
                types.Part.from_bytes(data=audio, mime_type=mime),
                types.Part.from_text(text="Transcribe this command and return the JSON."),
            ])],
            config=types.GenerateContentConfig(system_instruction=system_prompt(), temperature=0.0,
                                               response_mime_type="application/json"),
        )
        return interpret(getattr(response, "text", None))


# HTTP ---------------------------------------------------------------------------------------------------------
def audio_part(body: bytes, content_type: str) -> tuple[str, bytes] | None:
    """(mime, bytes) of the multipart field `audio`, or None if there is none."""
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m or not content_type.lower().startswith("multipart/form-data"):
        return None
    boundary = b"--" + m.group(1).encode("latin-1")
    for part in body.split(boundary)[1:]:
        if part.startswith(b"--"):
            break
        head, sep, data = part.removeprefix(b"\r\n").partition(b"\r\n\r\n")
        if not sep:
            continue
        headers = {}
        for line in head.decode("latin-1").split("\r\n"):
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        name = re.search(r'\bname="([^"]*)"', headers.get("content-disposition", ""))
        if name and name.group(1) == "audio":
            return headers.get("content-type", "application/octet-stream"), data.removesuffix(b"\r\n")
    return None


async def handle(request: Request, voice_command: VoiceCommand) -> VoiceCommandResponse:
    if not voice_command.available:
        raise HTTPException(status_code=503, detail=UNAVAILABLE)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES + 65_536:
        raise HTTPException(status_code=413, detail="audio clip is larger than 1 MB")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BYTES + 65_536:
            raise HTTPException(status_code=413, detail="audio clip is larger than 1 MB")
    part = audio_part(bytes(body), request.headers.get("content-type", ""))
    if part is None:
        raise HTTPException(status_code=400, detail="multipart field 'audio' is missing")
    mime, audio = part
    base = mime.split(";")[0].strip().lower()
    if base not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail="audio must be audio/webm or audio/ogg")
    if len(audio) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="audio clip is larger than 1 MB")
    if not audio:
        raise HTTPException(status_code=400, detail="audio clip is empty")
    try:
        return await asyncio.to_thread(voice_command.transcribe, audio, base)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - say what failed, never the key or the audio
        raise HTTPException(status_code=502, detail=f"voice command failed ({type(exc).__name__})") from None
