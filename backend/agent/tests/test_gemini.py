"""T17: Gemini adapter. Model from GEMINI_MODEL (pinned), retries with backoff on 429/5xx, tokens and latency in
the recorder, system prompt rules, every live run recorded. A fake client stands in for the SDK: no network."""
import json

import httpx
import pytest
from google.genai import errors, types

from backend.agent import gemini
from backend.agent.dispatcher import Dispatcher
from backend.agent.gemini import SKIP_SIGNATURE, GeminiConfigError, GeminiModel, GeminiUnavailable
from backend.agent.interfaces import ListRecorder, NullRecorder
from backend.agent.loop import run_agent
from backend.agent.mock_backend import MockBackend
from backend.agent.model import Message, ModelResponse, ToolCall, ToolResult
from backend.agent.prompt import SYSTEM_PROMPT
from backend.agent.stub import StubModel
from backend.agent.tools import TOOL_SCHEMAS

PINNED = "gemini-3.6-flash"


# Fakes -----------------------------------------------------------------------------------------------------------
def reply(*, text=None, calls=(), tokens=100, signature=None, thought=None, call_ids=None):
    parts = []
    if thought:
        parts.append(types.Part(text=thought, thought=True))
    for i, (name, args) in enumerate(calls):
        cid = call_ids[i] if call_ids else None
        parts.append(types.Part(function_call=types.FunctionCall(id=cid, name=name, args=args),
                                thought_signature=signature))
    if text:
        parts.append(types.Part(text=text))
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts), finish_reason="STOP")],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            total_token_count=tokens, prompt_token_count=tokens - 10, candidates_token_count=10
        ),
    )


def http_error(code):
    body = {"error": {"code": code, "message": "fake", "status": "FAKE"}}
    return errors.ClientError(code, body) if code < 500 else errors.ServerError(code, body)


class FakeModels:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def generate_content(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        if not self.script:
            raise AssertionError("fake Gemini script exhausted")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClient:
    def __init__(self, script):
        self.models = FakeModels(script)


def make(script, **kw):
    sleeps = []
    kw.setdefault("jitter", lambda: 1.0)
    model = GeminiModel(PINNED, client=FakeClient(script), sleeper=sleeps.append, **kw)
    return model, sleeps


def ask(model, messages=None):
    return model.generate(SYSTEM_PROMPT, messages or [Message(role="user", text="go")], TOOL_SCHEMAS)


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch):
    """The developer shell may hold a real key: make sure no test can build a real client."""
    def refuse(api_key):
        raise AssertionError("a test tried to build a real Gemini client")
    monkeypatch.setattr(gemini, "_make_client", refuse)


# Model name and secrets ------------------------------------------------------------------------------------------
def test_model_comes_from_gemini_model_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", PINNED)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    m = GeminiModel.from_env(client=FakeClient([reply(text="ok")]))
    assert m.name == PINNED
    ask(m)
    assert m._client.models.requests[0]["model"] == PINNED


def test_missing_model_env_is_a_config_error(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    with pytest.raises(GeminiConfigError, match="GEMINI_MODEL"):
        GeminiModel.from_env(client=FakeClient([]))


@pytest.mark.parametrize("alias", ["gemini-flash-latest", "models/gemini-latest", "latest"])
def test_alias_model_names_are_rejected(alias):
    with pytest.raises(GeminiConfigError, match="pin"):
        GeminiModel(alias, client=FakeClient([]))


def test_missing_key_is_a_config_error_and_errors_never_contain_the_key(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", PINNED)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(GeminiConfigError, match="GEMINI_API_KEY"):
        GeminiModel.from_env()
    secret = "AIza-secret-value-123"
    with pytest.raises(GeminiConfigError) as exc:
        GeminiModel("gemini-latest", api_key=secret)
    assert secret not in str(exc.value)


# Retries ---------------------------------------------------------------------------------------------------------
def test_retries_429_and_5xx_with_exponential_backoff():
    m, sleeps = make([http_error(429), http_error(503), http_error(500), reply(text="done")])
    out = ask(m)
    assert out.text == "done"
    assert len(m._client.models.requests) == 4
    assert sleeps == [1.0, 2.0, 4.0]
    assert m.last_call.attempts == 4


def test_backoff_is_capped_and_jittered():
    m, _ = make([], base_delay_s=1.0, max_delay_s=5.0, jitter=lambda: 0.0)
    assert [m.backoff_s(a) for a in (1, 2, 3, 4, 5)] == [0.5, 1.0, 2.0, 2.5, 2.5]
    m, _ = make([], base_delay_s=1.0, max_delay_s=5.0, jitter=lambda: 1.0)
    assert m.backoff_s(10) == 5.0


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_client_errors_other_than_429_are_not_retried(code):
    m, sleeps = make([http_error(code), reply(text="never")])
    with pytest.raises(errors.ClientError):
        ask(m)
    assert len(m._client.models.requests) == 1
    assert sleeps == []


def test_gives_up_after_max_attempts():
    m, sleeps = make([http_error(503)] * 3, max_attempts=3)
    with pytest.raises(GeminiUnavailable) as exc:
        ask(m)
    assert exc.value.attempts == 3 and exc.value.status == 503
    assert len(sleeps) == 2


def test_transport_errors_are_retried():
    m, sleeps = make([httpx.ConnectError("down"), httpx.ReadTimeout("slow"), reply(text="back")])
    assert ask(m).text == "back"
    assert len(sleeps) == 2


def test_empty_response_is_retried():
    empty = types.GenerateContentResponse(candidates=[])
    malformed = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[]),
                                    finish_reason="MALFORMED_FUNCTION_CALL")]
    )
    m, sleeps = make([empty, malformed, reply(text="ok")])
    assert ask(m).text == "ok"
    assert len(sleeps) == 2


def test_sdk_client_has_its_own_retries_switched_off(monkeypatch):
    monkeypatch.undo()  # use the real _make_client; building a client makes no network call
    client = gemini._make_client("not-a-real-key")
    assert client._api_client._http_options.retry_options.attempts == 1


# Request and response conversion ---------------------------------------------------------------------------------
def test_request_carries_system_prompt_all_tools_and_disables_auto_calling():
    m, _ = make([reply(text="ok")])
    ask(m)
    config = m._client.models.requests[0]["config"]
    assert config.system_instruction == SYSTEM_PROMPT
    names = [d.name for d in config.tools[0].function_declarations]
    assert names == [t["name"] for t in TOOL_SCHEMAS]
    assert config.tools[0].function_declarations[0].parameters_json_schema == TOOL_SCHEMAS[0]["parameters"]
    assert config.automatic_function_calling.disable is True


def test_response_parsing_ids_args_tokens_and_no_thought_text():
    m, _ = make([reply(calls=[("get_fleet_status", {}), ("get_telemetry", {"sat_id": "SAT-07"})],
                       thought="private reasoning with 3 digits", tokens=321)])
    out = ask(m)
    assert out.text is None
    assert [(c.name, c.args, c.id) for c in out.tool_calls] == [
        ("get_fleet_status", {}, "call-1-1"),
        ("get_telemetry", {"sat_id": "SAT-07"}, "call-1-2"),
    ]
    assert out.tokens == 321
    assert (m.last_call.prompt_tokens, m.last_call.output_tokens) == (311, 10)
    json.dumps(out.to_payload())  # recorder-ready


def test_gemini_issued_call_ids_are_kept():
    m, _ = make([reply(calls=[("get_fleet_status", {})], call_ids=["abc123"])])
    assert ask(m).tool_calls[0].id == "abc123"


def test_history_reuses_signed_turns_and_sends_tool_results_as_function_responses():
    first = reply(calls=[("get_fleet_status", {})], signature=b"real-signature")
    m, _ = make([first, reply(text="done")])
    goal = Message(role="user", text="go")
    r1 = ask(m, [goal])
    history = [
        goal,
        Message(role="model", text=r1.text, tool_calls=r1.tool_calls),
        Message(role="tool", tool_results=(ToolResult(name="get_fleet_status", result={"triage": []},
                                                      id=r1.tool_calls[0].id),)),
    ]
    ask(m, history)
    contents = m._client.models.requests[1]["contents"]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1].parts[0].thought_signature == b"real-signature"  # sent back verbatim
    fr = contents[2].parts[0].function_response
    assert (fr.name, fr.response, fr.id) == ("get_fleet_status", {"triage": []}, None)  # synthetic id not sent


def test_turns_from_elsewhere_are_rebuilt_with_placeholder_signature():
    m, _ = make([reply(text="done")])
    history = [
        Message(role="user", text="go"),
        Message(role="model", text="checking", tool_calls=(ToolCall("get_fleet_status", {}, id="g-77"),)),
        Message(role="tool", tool_results=(ToolResult("get_fleet_status", {"triage": []}, id="g-77"),)),
    ]
    ask(m, history)
    model_turn = m._client.models.requests[0]["contents"][1]
    assert model_turn.parts[0].text == "checking"
    call = model_turn.parts[1]
    assert (call.function_call.name, call.function_call.id, call.thought_signature) == (
        "get_fleet_status", "g-77", SKIP_SIGNATURE)


# System prompt ---------------------------------------------------------------------------------------------------
def test_system_prompt_states_the_three_rules():
    p = SYSTEM_PROMPT.lower()
    assert "orchestrate tools" in p
    assert "never compute physics" in p
    assert "numbers only through placeholders" in p


# Loop integration: tokens and latency, live runs must be recorded -----------------------------------------------
def stub_script():
    """The StubModel's decisions on the mock backend, as Gemini responses (no ids, like the Gemini API)."""
    rec = ListRecorder()
    run_agent(StubModel(), Dispatcher(MockBackend()), rec)
    script = []
    for i, e in enumerate(x for x in rec.events if x.kind == "model_response"):
        r = ModelResponse.from_payload(e.payload)
        script.append(reply(text=r.text, calls=[(c.name, c.args) for c in r.tool_calls],
                            tokens=1000 + i, signature=b"sig-%d" % i))
    return script


def test_live_run_records_tokens_latency_and_model_name():
    m, _ = make(stub_script())
    rec = ListRecorder()
    run = run_agent(m, Dispatcher(MockBackend()), rec)
    assert run.status == "done" and run.final_text and run.rendered_text
    assert [c.name for c in run.tool_calls][-1] == "request_approval"
    responses = [e for e in rec.events if e.kind == "model_response"]
    assert [e.tokens for e in responses] == [1000 + i for i in range(len(responses))]
    assert all(e.latency_ms is not None and e.latency_ms >= 0 for e in responses)
    assert all(e.payload["model"] == PINNED for e in rec.events if e.kind == "model_request")


@pytest.mark.parametrize("recorder", [None, NullRecorder()])
def test_live_model_refuses_to_run_unrecorded(recorder):
    m, _ = make([reply(text="never")])
    with pytest.raises(ValueError, match="recorded"):
        run_agent(m, Dispatcher(MockBackend()), recorder)
    assert m._client.models.requests == []
