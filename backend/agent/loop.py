"""The agent loop: model turn -> tool calls -> tool results -> next model turn, at most MAX_STEPS model turns.

Every model request/response and tool call/result goes to the recorder (CONTRACTS.md flight recorder kinds).
The final text must pass the narration rule; if not, the model is re-asked at most MAX_NARRATION_RETRIES times,
after which the run ends with no narration and the UI shows tool results only.

A live model (`model.live` is true, e.g. GeminiModel) must run with a real recorder: every live run is recorded.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from contracts import MAX_NARRATION_RETRIES

from .dispatcher import Dispatcher
from .interfaces import NullRecorder, Recorder
from .model import Message, Model, ToolCall, ToolResult
from .narration import narration_problems, render
from .prompt import DEFAULT_GOAL, SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS

MAX_STEPS = 25  # model turns per run, narration re-asks included


@dataclass
class AgentRun:
    status: Literal["done", "step_cap"]
    steps: int  # model turns used
    final_text: str | None  # accepted final text with placeholders; None if rejected or the step cap was hit
    rendered_text: str | None = None  # final_text with placeholders filled in by the backend: show this one
    narration_rejected: bool = False  # True if the model kept breaking the narration rule
    messages: list[Message] = field(default_factory=list)

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [tc for m in self.messages if m.role == "model" for tc in m.tool_calls]


def narration_feedback(problems: list[str]) -> str:
    return (
        "Your summary breaks the narration rule (" + "; ".join(problems) + "). Rewrite it without any digits: "
        "refer to numbers only through placeholders such as {S-1.worst_margin_soc}, and only to ids that exist."
    )


def run_agent(
    model: Model,
    dispatcher: Dispatcher,
    recorder: Recorder | None = None,
    *,
    goal: str = DEFAULT_GOAL,
    system: str = SYSTEM_PROMPT,
    max_steps: int = MAX_STEPS,
) -> AgentRun:
    if getattr(model, "live", False) and (recorder is None or isinstance(recorder, NullRecorder)):
        raise ValueError("a live model run must be recorded: pass a flight recorder (see session.record_run)")
    rec = recorder if recorder is not None else NullRecorder()
    model_name = getattr(model, "name", type(model).__name__)
    messages: list[Message] = [Message(role="user", text=goal)]
    retries = 0

    for step in range(1, max_steps + 1):
        rec.record(
            "model_request",
            {"step": step, "n_messages": len(messages), "narration_retry": retries, "model": model_name},
        )
        started = time.perf_counter()
        response = model.generate(system, list(messages), TOOL_SCHEMAS)
        latency_ms = (time.perf_counter() - started) * 1000.0
        rec.record("model_response", response.to_payload(), tokens=response.tokens, latency_ms=latency_ms)
        messages.append(Message(role="model", text=response.text, tool_calls=response.tool_calls))

        if not response.tool_calls:
            problems = narration_problems(response.text or "", dispatcher)
            if not problems:
                text = response.text or ""
                return AgentRun(status="done", steps=step, final_text=text, rendered_text=render(text, dispatcher),
                                messages=messages)
            if retries >= MAX_NARRATION_RETRIES:
                return AgentRun(status="done", steps=step, final_text=None, narration_rejected=True,
                                messages=messages)
            retries += 1
            messages.append(Message(role="user", text=narration_feedback(problems)))
            continue

        results = []
        for call in response.tool_calls:
            rec.record("tool_call", {"id": call.id, "name": call.name, "args": call.args})
            outcome = dispatcher.dispatch(call.name, call.args)
            rec.record(
                "tool_result",
                {"id": call.id, "name": call.name, "result": outcome.result},
                plan_hash=outcome.plan_hash,
            )
            for kind, payload in outcome.events:  # e.g. the execution of a Tier 1 plan
                rec.record(kind, payload, plan_hash=outcome.plan_hash)
            results.append(ToolResult(name=call.name, result=outcome.result, id=call.id))
        messages.append(Message(role="tool", tool_results=tuple(results)))

    return AgentRun(status="step_cap", steps=max_steps, final_text=None, messages=messages)
