"""Fixes from B1's live runs on FleetBackend (board REQUEST 23:45Z):
(1) Gemini's narration was rejected for ids in braces ({P-2}, {H2}, {SVALBARD}) and fields the renderer did not
know ({P-2.n_commands}); (2) on an infeasible satellite it proposed plans until the step cap and wrote no summary."""
from types import SimpleNamespace

import contracts as c
from backend.agent import gemini
from backend.agent.dispatcher import MAX_FAILED_PLANS, Dispatcher
from backend.agent.interfaces import ListRecorder
from backend.agent.loop import FINAL_TURN, MAX_STEPS, run_agent
from backend.agent.mock_backend import MOCK_SAT, MockBackend
from backend.agent.model import ModelResponse, ToolCall
from backend.agent.narration import narration_problems, normalize, render
from backend.agent.tests.test_gemini import FakeClient, make, reply

AUX_OFF = [{"name": "LOAD_OFF", "args": {"load": "AUX"}}]
SHED = [{"name": "SET_PAYLOAD_DUTY", "args": {"duty": 0.2}}, {"name": "LOAD_OFF", "args": {"load": "AUX"}}]
SAFE = [{"name": "SAFE_MODE", "args": {}}]


def approved_dispatcher():
    d = Dispatcher(MockBackend())
    d.dispatch("get_fleet_status", {})  # AL-1
    d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": AUX_OFF})  # P-1
    d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": SHED})  # P-2
    d.dispatch("simulate", {"plan_id": "P-1"})  # S-1
    d.dispatch("simulate", {"plan_id": "P-2"})  # S-2
    d.dispatch("schedule_uplink", {"plan_id": "P-2"})
    d.dispatch("request_approval", {"plan_id": "P-2"})  # AP-1
    return d


# (1) narration -------------------------------------------------------------------------------------------------
B1_TEXT = (  # Gemini's first summary in runs/demo/live-single-stuck-load.jsonl, unchanged
    "The fleet triage queue identified a critical alert on {AL-1.sat_id} with reason {AL-1.reason_key}. Hypothesis "
    "scoring isolated {H2} as the plausible fault. Candidate recovery plan {P-2} was proposed with "
    "{P-2.n_commands} commands, successfully verified across all fault hypotheses with a worst-case state-of-charge "
    "margin of {S-2.worst_margin_soc}, and scheduled for uplink at station {SVALBARD}. Because this plan requires "
    "Tier {AP-1.tier} review, approval request {AP-1} has been submitted and is currently awaiting approval from "
    "the Flight Director."
)


def test_b1_live_summary_is_accepted_and_rendered():
    d = approved_dispatcher()
    text = normalize(B1_TEXT)
    assert narration_problems(text, d) == []
    out = render(text, d)
    assert "{" not in out
    assert f"alert on {MOCK_SAT} with reason soc_below_model" in out
    assert "isolated H2" in out and "plan P-2 was proposed with 2 commands" in out
    assert "station SVALBARD" in out and "Tier 2 review, approval request AP-1" in out


def test_unwrapping_braces_never_lets_a_number_through():
    d = approved_dispatcher()
    for bad in ("{42}", "margin {P-80}", "{SAT-99}", "{AP-7}"):
        assert narration_problems(normalize(bad), d), bad


def test_derived_fields_render():
    d = approved_dispatcher()
    assert render("{P-2.n_commands} via {P-2.station_id}", d) == "2 via SVALBARD"
    assert render("{AL-1.time_to_critical_min}", d) == "270 min"  # mock: 4.5 h to critical


def test_loop_accepts_braced_ids_and_stores_the_normalized_text():
    class Braces:
        def generate(self, system, messages, tools):
            return ModelResponse(text="{SAT-07} is draining; see {AL-1}.")

    d = Dispatcher(MockBackend())
    d.dispatch("get_fleet_status", {})
    run = run_agent(Braces(), d)
    assert run.status == "done" and run.final_text == "SAT-07 is draining; see AL-1."
    assert run.steps == 1


def test_reask_message_lists_usable_fields_and_the_no_braces_rule():
    class Once:
        def __init__(self):
            self.seen = []

        def generate(self, system, messages, tools):
            self.seen.append(messages[-1].text)
            return ModelResponse(text="SAT-07 has 3 problems." if len(self.seen) == 1 else "SAT-07 is draining.")

    m = Once()
    run_agent(m, Dispatcher(MockBackend()))
    feedback = m.seen[1]
    assert "never in braces" in feedback and "time_to_critical_min" in feedback and "n_commands" in feedback


# (2) infeasible search and the step cap -------------------------------------------------------------------------
def test_plan_search_stops_after_the_failed_plan_budget():
    d = Dispatcher(MockBackend())  # under H2 shedding AUX alone always fails
    for k in range(MAX_FAILED_PLANS):
        pid = d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": AUX_OFF}).result["plan_id"]
        sim = d.dispatch("simulate", {"plan_id": pid}).result
        assert sim["passes_all"] is False
    assert "infeasible" in sim["hint"] and "SAFE_MODE" in sim["hint"]
    refused = d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": SHED}).result
    assert "stop searching" in refused["error"]
    assert "plan_id" in d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": SAFE}).result


class Infeasible(MockBackend):
    def rank(self, sat_id):
        return SimpleNamespace(status="infeasible", ordered=[])


def test_simulate_reports_the_backend_rank_status():
    d = Dispatcher(Infeasible())
    pid = d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": SAFE}).result["plan_id"]
    sim = d.dispatch("simulate", {"plan_id": pid}).result
    assert sim["rank_status"] == "infeasible" and "recommend SAFE_MODE" in sim["hint"]
    feasible = Dispatcher(MockBackend()).dispatch  # no rank(): no extra keys
    pid = feasible("propose_plan", {"sat_id": MOCK_SAT, "commands": SHED}).result["plan_id"]
    assert "rank_status" not in feasible("simulate", {"plan_id": pid}).result


class Searcher:
    """Keeps calling tools; writes a summary only when no tools are offered (like Gemini on the final turn)."""

    def __init__(self):
        self.tools_seen = []

    def generate(self, system, messages, tools):
        self.tools_seen.append(len(tools))
        if not tools:
            return ModelResponse(text="SAT-07: no recovery plan passed. The situation is infeasible; recommend "
                                      "SAFE_MODE.")
        return ModelResponse(tool_calls=(ToolCall("get_fleet_status", {}, id=f"c{len(self.tools_seen)}"),))


def test_step_cap_ends_with_a_summary_turn_without_tools():
    m = Searcher()
    rec = ListRecorder()
    run = run_agent(m, Dispatcher(MockBackend()), rec)
    assert run.status == "step_cap" and run.steps == MAX_STEPS
    assert m.tools_seen[-1] == 0 and all(n > 0 for n in m.tools_seen[:-1])
    assert run.messages[-2].text == FINAL_TURN
    assert run.rendered_text and "infeasible" in run.rendered_text
    last_request = [e for e in rec.events if e.kind == "model_request"][-1]
    assert last_request.payload["final_turn"] is True
    assert c.narration_ok(run.final_text)


def test_gemini_final_turn_keeps_declarations_but_forbids_calls(monkeypatch):
    monkeypatch.setattr(gemini, "_make_client", lambda api_key: FakeClient([]))
    m, _ = make([reply(calls=[("get_fleet_status", {})]), reply(text="summary")])
    from backend.agent.model import Message, ToolResult
    from backend.agent.prompt import SYSTEM_PROMPT
    from backend.agent.tools import TOOL_SCHEMAS

    first = m.generate(SYSTEM_PROMPT, [Message(role="user", text="go")], TOOL_SCHEMAS)
    history = [Message(role="user", text="go"), Message(role="model", tool_calls=first.tool_calls),
               Message(role="tool", tool_results=(ToolResult("get_fleet_status", {}, id=first.tool_calls[0].id),)),
               Message(role="user", text=FINAL_TURN)]
    m.generate(SYSTEM_PROMPT, history, ())
    config = m._client.models.requests[1]["config"]
    assert config.tools and len(config.tools[0].function_declarations) == len(TOOL_SCHEMAS)
    assert config.tool_config.function_calling_config.mode.value == "NONE"


# (3) units: B1's live conjunction run rendered "0.30 km km" -----------------------------------------------------
def test_a_unit_written_after_a_placeholder_is_not_doubled():
    from backend.agent.tests.test_cam_safety import ConjBackend

    d = Dispatcher(ConjBackend())
    d.dispatch("get_conjunctions", {"sat_id": MOCK_SAT})
    d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": SHED})  # P-1
    d.dispatch("simulate", {"plan_id": "P-1"})  # S-1
    assert render("miss {CJ-1.miss_km} km", d) == "miss 0.30 km"
    assert render("miss {CJ-1.miss_km}km.", d) == "miss 0.30 km."
    assert render("miss {CJ-1.miss_km}, so", d) == "miss 0.30 km, so"
    margin = render("{S-1.worst_margin_soc}", d)
    assert margin.endswith("% SOC")
    assert render("margin {S-1.worst_margin_soc} SOC left", d) == f"margin {margin} left"
    assert render("margin {S-1.worst_margin_soc} % SOC left", d) == f"margin {margin} left"
    assert render("at {CJ-1.tca} UTC sharp", d).endswith(" UTC sharp") and "UTC UTC" not in render("at {CJ-1.tca} UTC", d)
    assert render("{CJ-1.object_id} s", d) == "DEB-01 s"  # no digit unit in the value: nothing to drop
