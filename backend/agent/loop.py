"""The agent loop: model turn -> tool calls -> tool results -> next model turn, at most MAX_STEPS model turns.

Every model request/response and tool call/result goes to the recorder (CONTRACTS.md flight recorder kinds).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from .dispatcher import Dispatcher
from .interfaces import NullRecorder, Recorder
from .model import Message, Model, ToolCall, ToolResult
from .prompt import DEFAULT_GOAL, SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS

MAX_STEPS = 25  # model turns per run


@dataclass
class AgentRun:
    status: Literal["done", "step_cap"]
    steps: int  # model turns used
    final_text: str | None  # text of the last turn without tool calls; None if the step cap was hit
    messages: list[Message] = field(default_factory=list)

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [tc for m in self.messages if m.role == "model" for tc in m.tool_calls]


def run_agent(
    model: Model,
    dispatcher: Dispatcher,
    recorder: Recorder | None = None,
    *,
    goal: str = DEFAULT_GOAL,
    system: str = SYSTEM_PROMPT,
    max_steps: int = MAX_STEPS,
) -> AgentRun:
    rec = recorder if recorder is not None else NullRecorder()
    messages: list[Message] = [Message(role="user", text=goal)]

    for step in range(1, max_steps + 1):
        rec.record("model_request", {"step": step, "n_messages": len(messages)})
        started = time.perf_counter()
        response = model.generate(system, list(messages), TOOL_SCHEMAS)
        latency_ms = (time.perf_counter() - started) * 1000.0
        rec.record("model_response", response.to_payload(), tokens=response.tokens, latency_ms=latency_ms)
        messages.append(Message(role="model", text=response.text, tool_calls=response.tool_calls))

        if not response.tool_calls:
            return AgentRun(status="done", steps=step, final_text=response.text, messages=messages)

        results = []
        for call in response.tool_calls:
            rec.record("tool_call", {"id": call.id, "name": call.name, "args": call.args})
            outcome = dispatcher.dispatch(call.name, call.args)
            rec.record(
                "tool_result",
                {"id": call.id, "name": call.name, "result": outcome.result},
                plan_hash=outcome.plan_hash,
            )
            results.append(ToolResult(name=call.name, result=outcome.result, id=call.id))
        messages.append(Message(role="tool", tool_results=tuple(results)))

    return AgentRun(status="step_cap", steps=max_steps, final_text=None, messages=messages)
