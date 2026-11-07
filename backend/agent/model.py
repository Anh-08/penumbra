"""Provider-neutral model interface used by the agent loop.

Every model (StubModel here, Gemini in T17, the recorder's ReplayModel in T13) implements `Model.generate`.
The loop records each response as a `model_response` event whose payload is `ModelResponse.to_payload()`;
replay rebuilds it with `ModelResponse.from_payload(event.payload, tokens=event.tokens)`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    id: str = ""


@dataclass(frozen=True)
class ToolResult:
    name: str
    result: dict[str, Any]
    id: str = ""


@dataclass(frozen=True)
class ModelResponse:
    """One model turn: optional free text plus zero or more tool calls. No tool calls = the agent is done."""
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tokens: int | None = None  # total tokens for this request, if the provider reports it

    def to_payload(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_calls": [{"id": tc.id, "name": tc.name, "args": tc.args} for tc in self.tool_calls],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], tokens: int | None = None) -> "ModelResponse":
        calls = tuple(
            ToolCall(name=tc["name"], args=dict(tc.get("args") or {}), id=tc.get("id", ""))
            for tc in payload.get("tool_calls") or ()
        )
        return cls(text=payload.get("text"), tool_calls=calls, tokens=tokens)


@dataclass(frozen=True)
class Message:
    """Conversation history entry.

    user  : `text` (the goal)
    model : `text` and/or `tool_calls`
    tool  : `tool_results`, one per tool call of the previous model turn, same ids
    """
    role: Literal["user", "model", "tool"]
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()


class Model(Protocol):
    def generate(
        self, system: str, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]
    ) -> ModelResponse:
        """Return the next turn. `tools` are the JSON-schema function declarations from tools.TOOL_SCHEMAS."""
        ...
