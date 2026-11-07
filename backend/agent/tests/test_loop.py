"""T06: the scripted stub model runs Detect -> Diagnose -> Plan -> Verify -> Schedule -> Approve on mock data,
with a step cap of 25, and every trace event goes through the recorder interface."""
import json

import contracts as c
from backend.agent.dispatcher import Dispatcher
from backend.agent.interfaces import ListRecorder, NullRecorder
from backend.agent.loop import MAX_STEPS, run_agent
from backend.agent.mock_backend import MOCK_SAT, MockBackend
from backend.agent.model import Message, ModelResponse, ToolCall
from backend.agent.stub import StubModel
from backend.agent.tools import TOOL_SCHEMAS

# First tool of each phase, in the order the phases must happen.
PHASES = [
    ("detect", "get_fleet_status"),
    ("diagnose", "score_hypotheses"),
    ("plan", "propose_plan"),
    ("verify", "simulate"),
    ("schedule", "schedule_uplink"),
    ("approve", "request_approval"),
]


def stub_run(recorder=None):
    d = Dispatcher(MockBackend())
    run = run_agent(StubModel(), d, recorder)
    return run, d


def test_step_cap_is_25():
    assert MAX_STEPS == 25


def test_stub_runs_all_phases_in_order():
    run, d = stub_run()
    assert run.status == "done"
    assert run.steps <= MAX_STEPS
    # The diagnostic test (T20) is part of Diagnose; the phase order is checked on the recovery plan's calls.
    diagnostics = {pid for pid, p in d.plans.items() if p.kind == "diagnostic"}
    names = [tc.name for tc in run.tool_calls if tc.args.get("plan_id") not in diagnostics
             and tc.name != "propose_diagnostic_test"]
    firsts = [names.index(tool) for _, tool in PHASES]
    assert firsts == sorted(firsts), names


def test_stub_approves_the_plan_that_passed_verification():
    run, d = stub_run()
    tickets = [t for t in d.approvals.values() if d.plans[t.plan_id].kind == "plan"]
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket.sat_id == MOCK_SAT
    assert d.sims[ticket.plan_id].passes_all
    failing = [pid for pid, sim in d.sims.items() if not sim.passes_all]
    assert failing, "the stub should show one plan rejected by verification"
    assert ticket.plan_id not in failing


def test_stub_final_narration_follows_the_narration_rule():
    run, _ = stub_run()
    assert run.final_text
    assert c.narration_ok(run.final_text), c.bare_digits(run.final_text)
    assert c.placeholders(run.final_text)  # numbers come from the backend


def test_recorder_gets_every_trace_event():
    rec = ListRecorder(run_id="t06")
    run, _ = stub_run(rec)
    kinds = [e.kind for e in rec.events]
    assert kinds[:2] == ["model_request", "model_response"]
    assert kinds.count("model_request") == kinds.count("model_response") == run.steps
    assert kinds.count("tool_call") == kinds.count("tool_result") == len(run.tool_calls)
    assert [e.seq for e in rec.events] == list(range(len(rec.events)))
    for e in rec.events:
        c.RecorderEvent.model_validate(e.model_dump())
        json.dumps(e.payload, allow_nan=False)
    sims = [e for e in rec.events if e.kind == "tool_result" and e.payload["name"] == "simulate"]
    assert sims and all(e.plan_hash == e.payload["result"]["plan_hash"] for e in sims)
    latencies = [e.latency_ms for e in rec.events if e.kind == "model_response"]
    assert all(x is not None and x >= 0 for x in latencies)


def test_model_response_payload_round_trips_for_replay():
    rec = ListRecorder()
    stub_run(rec)
    for e in rec.events:
        if e.kind == "model_response":
            r = ModelResponse.from_payload(e.payload, tokens=e.tokens)
            assert r.to_payload() == e.payload


def test_runs_are_deterministic():
    r1, d1 = stub_run()
    r2, d2 = stub_run()
    assert [(t.name, t.args) for t in r1.tool_calls] == [(t.name, t.args) for t in r2.tool_calls]
    assert {k: c.plan_hash(p) for k, p in d1.plans.items()} == {k: c.plan_hash(p) for k, p in d2.plans.items()}
    assert r1.final_text == r2.final_text


def test_null_recorder_is_the_default():
    assert isinstance(NullRecorder().record("tool_call", {}), type(None))
    run, _ = stub_run(None)
    assert run.status == "done"


class LoopingModel:
    """Never stops calling tools."""

    def __init__(self):
        self.calls = 0

    def generate(self, system, messages, tools):
        self.calls += 1
        return ModelResponse(tool_calls=(ToolCall(name="get_fleet_status", args={}, id=f"c{self.calls}"),))


def test_step_cap_stops_a_looping_model():
    model = LoopingModel()
    run = run_agent(model, Dispatcher(MockBackend()))
    assert run.status == "step_cap"
    assert model.calls == run.steps == MAX_STEPS
    assert run.final_text is None


class RecordingModel:
    """Captures what the loop sends, then stops."""

    def __init__(self):
        self.seen = None

    def generate(self, system, messages, tools):
        self.seen = (system, list(messages), list(tools))
        return ModelResponse(text="Nothing to do.")


def test_loop_sends_system_prompt_goal_and_tool_schemas():
    model = RecordingModel()
    run = run_agent(model, Dispatcher(MockBackend()), goal="Check the fleet.")
    system, messages, tools = model.seen
    assert system and "tool" in system.lower()
    assert messages == [Message(role="user", text="Check the fleet.")]
    assert tools == TOOL_SCHEMAS
    assert (run.status, run.steps, run.final_text) == ("done", 1, "Nothing to do.")


def test_tool_errors_go_back_to_the_model():
    class BadThenStop:
        def __init__(self):
            self.n = 0

        def generate(self, system, messages, tools):
            self.n += 1
            if self.n == 1:
                return ModelResponse(tool_calls=(ToolCall(name="simulate", args={"plan_id": "P-404"}, id="x"),))
            return ModelResponse(text="Stopped.")

    run = run_agent(BadThenStop(), Dispatcher(MockBackend()))
    tool_turn = run.messages[2]
    assert tool_turn.role == "tool"
    assert "error" in tool_turn.tool_results[0].result
    assert tool_turn.tool_results[0].id == "x"


# ---------------------------------------------------------------- T10: narration rule in the loop
def test_stub_narration_is_rendered_from_backend_values():
    run, d = stub_run()
    assert run.rendered_text and "{" not in run.rendered_text
    assert "% SOC" in run.rendered_text
    assert not run.narration_rejected


class TextModel:
    """Answers with the given texts in order (no tool calls)."""

    def __init__(self, *texts):
        self.texts = list(texts)
        self.seen = []

    def generate(self, system, messages, tools):
        self.seen.append(list(messages))
        return ModelResponse(text=self.texts.pop(0))


def test_bare_digit_narration_is_re_asked_then_accepted():
    model = TextModel("SOC drops to 18 percent.", "SOC drops below the limit on SAT-07.")
    rec = ListRecorder()
    run = run_agent(model, Dispatcher(MockBackend()), rec)
    assert (run.status, run.steps) == ("done", 2)
    assert run.final_text == run.rendered_text == "SOC drops below the limit on SAT-07."
    feedback = model.seen[1][-1]
    assert feedback.role == "user" and "narration rule" in feedback.text and "bare digits" in feedback.text
    assert [e.payload["narration_retry"] for e in rec.events if e.kind == "model_request"] == [0, 1]


def test_narration_rejected_after_two_retries():
    model = TextModel("Use 3 commands.", "Use 3 commands.", "P-80 is best.", "never asked")
    run = run_agent(model, Dispatcher(MockBackend()))
    assert run.status == "done"
    assert run.steps == 1 + c.MAX_NARRATION_RETRIES == 3
    assert run.final_text is None and run.rendered_text is None
    assert run.narration_rejected
    assert model.texts == ["never asked"]
