"""Gemini adapter for the agent loop (T17).

`GeminiModel` implements `model.Model` on top of the google-genai SDK:
- the model name comes from `GEMINI_MODEL` and must be a pinned version (no `-latest` alias);
- the key comes from `GEMINI_API_KEY` and is never logged or put in an error message;
- 429 and 5xx (and transport errors) are retried with exponential backoff and jitter; other errors raise;
- each response carries `tokens` (total token count), which the loop writes to the flight recorder.

The SDK's own retries are switched off, so this class owns the retry policy and tests can drive it with a fake
client. No test talks to the network.

Gemini 3 models sign function calls (`thought_signature`) and want the signed parts back in the next request.
`Message` keeps only text and tool calls, so the adapter remembers the raw content of each turn it produced and
sends that back verbatim. A model turn it did not produce (for example one that came from a replay) is rebuilt
from the `Message` with the documented placeholder signature.
"""
from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .model import Message, ModelResponse, ToolCall

RETRY_STATUS = frozenset({429}) | frozenset(range(500, 600))
MAX_ATTEMPTS = 5  # including the first call
BASE_DELAY_S = 1.0
MAX_DELAY_S = 20.0
TIMEOUT_MS = 60_000

# Placeholder for function-call parts that were not produced in this process (Gemini 3 thought signatures).
SKIP_SIGNATURE = b"skip_thought_signature_validator"

_ALIAS = re.compile(r"(^|[-/])latest$")
_SYNTHETIC_ID = re.compile(r"call-\d+-\d+")  # ids this adapter (or the stub) made up; Gemini never saw them


class GeminiConfigError(RuntimeError):
    """GEMINI_MODEL or GEMINI_API_KEY missing or not usable. The message never contains the key."""


class GeminiUnavailable(RuntimeError):
    """Gemini kept failing after every retry, or returned nothing usable."""

    def __init__(self, message: str, *, attempts: int, status: int | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.status = status


@dataclass(frozen=True)
class CallStats:
    """Bookkeeping for the last `generate` call."""
    attempts: int
    tokens: int | None
    prompt_tokens: int | None
    output_tokens: int | None


def pinned_model_name(name: str | None) -> str:
    name = (name or "").strip()
    if not name:
        raise GeminiConfigError("GEMINI_MODEL is not set; set it to a pinned Gemini model version")
    if _ALIAS.search(name):
        raise GeminiConfigError(f"GEMINI_MODEL={name!r} is an alias; pin a model version instead")
    return name


class GeminiModel:
    """`Model` backed by Gemini function calling. Construct with `from_env()` in the app."""

    live = True  # run_agent refuses to run a live model without a flight recorder

    def __init__(
        self,
        model: str,
        *,
        client: Any = None,
        api_key: str | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        base_delay_s: float = BASE_DELAY_S,
        max_delay_s: float = MAX_DELAY_S,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.name = pinned_model_name(model)
        if client is None:
            if not api_key:
                raise GeminiConfigError("GEMINI_API_KEY is not set")
            client = _make_client(api_key)
        self._client = client
        self.max_attempts = max(1, int(max_attempts))
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self._sleep = sleeper
        self._jitter = jitter
        self._raw: dict[tuple[str, ...], Any] = {}  # tool-call ids of a turn -> the Content Gemini returned
        self.last_call: CallStats | None = None

    @classmethod
    def from_env(cls, **kwargs: Any) -> "GeminiModel":
        return cls(os.environ.get("GEMINI_MODEL", ""), api_key=os.environ.get("GEMINI_API_KEY"), **kwargs)

    # Model protocol ------------------------------------------------------------------------------------------
    def generate(
        self, system: str, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]
    ) -> ModelResponse:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[_declaration(t) for t in tools])] if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = self._contents(messages)
        turn = 1 + sum(1 for m in messages if m.role == "model")

        status: int | None = None
        reason = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                raw = self._client.models.generate_content(model=self.name, contents=contents, config=config)
            except Exception as exc:  # noqa: BLE001 - classified below, re-raised if not retryable
                status = _status(exc)
                if not _retryable(exc, status):
                    raise
                reason = f"HTTP {status}" if status else type(exc).__name__
            else:
                parsed = self._parse(raw, turn, attempt)
                if parsed is not None:
                    return parsed
                status, reason = None, f"empty or malformed response ({_finish_reason(raw)})"
            if attempt < self.max_attempts:
                self._sleep(self.backoff_s(attempt))
        raise GeminiUnavailable(
            f"Gemini {self.name} failed after {self.max_attempts} attempts: {reason}",
            attempts=self.max_attempts,
            status=status,
        )

    def backoff_s(self, attempt: int) -> float:
        """Delay after failed attempt `attempt` (1-based): exponential, capped, with up to 50 % jitter."""
        base = min(self.max_delay_s, self.base_delay_s * 2 ** (attempt - 1))
        return base * (0.5 + 0.5 * self._jitter())

    # Conversion ----------------------------------------------------------------------------------------------
    def _contents(self, messages: Sequence[Message]) -> list[Any]:
        from google.genai import types

        out: list[Any] = []
        for m in messages:
            if m.role == "user":
                out.append(types.Content(role="user", parts=[types.Part(text=m.text or "")]))
            elif m.role == "model":
                cached = self._raw.get(_key(m.tool_calls, m.text))
                out.append(cached if cached is not None else _model_content(m))
            else:
                out.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    id=_wire_id(r.id), name=r.name, response=r.result
                                )
                            )
                            for r in m.tool_results
                        ],
                    )
                )
        return out

    def _parse(self, raw: Any, turn: int, attempt: int) -> ModelResponse | None:
        candidates = getattr(raw, "candidates", None) or []
        content = getattr(candidates[0], "content", None) if candidates else None
        parts = list(getattr(content, "parts", None) or [])
        texts: list[str] = []
        calls: list[ToolCall] = []
        for part in parts:
            fc = getattr(part, "function_call", None)
            if fc is not None and getattr(fc, "name", None):
                call_id = getattr(fc, "id", None) or f"call-{turn}-{len(calls) + 1}"
                calls.append(ToolCall(name=fc.name, args=_plain(getattr(fc, "args", None) or {}), id=call_id))
            elif getattr(part, "text", None) and not getattr(part, "thought", False):
                texts.append(part.text)
        if not calls and not texts:
            return None

        usage = getattr(raw, "usage_metadata", None)
        tokens = getattr(usage, "total_token_count", None)
        self.last_call = CallStats(
            attempts=attempt,
            tokens=tokens,
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
        )
        text = "".join(texts).strip() or None
        self._raw[_key(tuple(calls), text)] = content
        return ModelResponse(text=text, tool_calls=tuple(calls), tokens=tokens)


# Helpers --------------------------------------------------------------------------------------------------------
def _make_client(api_key: str) -> Any:
    from google import genai
    from google.genai import types

    # attempts=1: no SDK-level retries, GeminiModel.generate owns the retry policy.
    http = types.HttpOptions(timeout=TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1))
    return genai.Client(api_key=api_key, http_options=http)


def _declaration(tool: Mapping[str, Any]) -> Any:
    from google.genai import types

    return types.FunctionDeclaration(
        name=tool["name"],
        description=tool.get("description", ""),
        parameters_json_schema=tool.get("parameters") or {"type": "object", "properties": {}},
    )


def _model_content(m: Message) -> Any:
    from google.genai import types

    parts = [types.Part(text=m.text)] if m.text else []
    parts += [
        types.Part(
            function_call=types.FunctionCall(id=_wire_id(tc.id), name=tc.name, args=tc.args),
            thought_signature=SKIP_SIGNATURE,
        )
        for tc in m.tool_calls
    ]
    return types.Content(role="model", parts=parts)


def _wire_id(call_id: str) -> str | None:
    """Send back only ids that Gemini issued itself."""
    return None if not call_id or _SYNTHETIC_ID.fullmatch(call_id) else call_id


def _key(calls: Sequence[ToolCall], text: str | None) -> tuple[str, ...]:
    """Identifies a model turn: its tool-call ids, or its text when it has no calls."""
    return tuple(tc.id for tc in calls) if calls else ("text", text or "")


def _plain(obj: Any) -> Any:
    """Gemini args can come back as proto-like mappings; hand the loop plain dicts and lists."""
    if isinstance(obj, Mapping):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def _status(exc: Exception) -> int | None:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def _retryable(exc: Exception, status: int | None) -> bool:
    if status is not None:
        return status in RETRY_STATUS
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a pinned dependency
        return False
    return isinstance(exc, httpx.TransportError)  # timeouts, dropped connections


def _finish_reason(raw: Any) -> str:
    candidates = getattr(raw, "candidates", None) or []
    reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    return str(getattr(reason, "value", reason) or "no candidates")
