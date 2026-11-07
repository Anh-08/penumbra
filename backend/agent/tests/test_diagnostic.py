"""T20: diagnostic-test flow. The agent runs a diagnostic in the current pass, the result updates the hypothesis
scores before it plans, and a recorded run shows the stuck-on load confirmed by the diagnostic (mock backend,
SIMULATED data)."""
import json

import pytest

import contracts as c
from backend.agent.dispatcher import DIAGNOSTIC_STEP_S, Dispatcher
from backend.agent.interfaces import ListRecorder
from backend.agent.loop import run_agent
from backend.agent.mock_backend import DIAGNOSTIC_FLIGHT_RULE, MOCK_SAT, MockBackend
from backend.agent.safety import SafetyError
from backend.agent.session import record_run
from backend.agent.stub import DIAGNOSTIC, StubModel
from backend.recorder import ReplayModel, load_events

AUX_OFF = [{"name": "LOAD_OFF", "args": {"load": "AUX"}}]
SHED = [{"name": "SET_PAYLOAD_DUTY", "args": {"duty": 0.2}}, {"name": "LOAD_OFF", "args": {"load": "AUX"}}]


class InPassBackend(MockBackend):
    """The alert comes in the middle of SAT-07's pass: next_contact returns the contact in progress."""

    def __init__(self):
        super().__init__()
        self._t = self.t0 + 660.0  # pass is t0+600 .. t0+1080

    def next_contact(self, sat_id, t):
        return next((k for k in self._contacts if k.sat_id == sat_id and k.los > t), None)


class Tier2Backend(MockBackend):
    def tier(self, plan, sim):
        return 2, None


class ConfirmedBackend(MockBackend):
    """Only one hypothesis is plausible from the start: no diagnostic is needed."""

    def __init__(self):
        super().__init__()
        self._confirmed.add(MOCK_SAT)


class UnsafeDiagnosticBackend(MockBackend):
    def simulate(self, plan, hypotheses, monte_carlo):
        sim = super().simulate(plan, hypotheses, monte_carlo)
        return sim.model_copy(update={"passes_all": False}) if plan.kind == "diagnostic" else sim


def call(d, name, **args):
    out = d.dispatch(name, args)
    json.dumps(out.result, allow_nan=False)
    return out


def run_diagnostic(d, commands=DIAGNOSTIC):
    pid = call(d, "propose_diagnostic_test", sat_id=MOCK_SAT, commands=commands).result["plan_id"]
    assert call(d, "simulate", plan_id=pid).result["passes_all"]
    assert call(d, "schedule_uplink", plan_id=pid).result["scheduled"]
    return pid, call(d, "request_approval", plan_id=pid)


# Time-tagging inside the pass --------------------------------------------------------------------------------
def test_diagnostic_steps_run_one_dwell_apart_inside_the_next_pass():
    b = MockBackend()
    d = Dispatcher(b)
    pid = call(d, "propose_diagnostic_test", sat_id=MOCK_SAT, commands=DIAGNOSTIC).result["plan_id"]
    plan = d.plans[pid]
    aos = plan.uplink.aos
    assert plan.kind == "diagnostic"
    assert [cmd.t_exec for cmd in plan.commands] == [aos + (k + 1) * DIAGNOSTIC_STEP_S for k in range(len(DIAGNOSTIC))]
    assert plan.commands[-1].t_exec + DIAGNOSTIC_STEP_S <= plan.uplink.los


def test_diagnostic_during_the_current_pass_starts_after_now():
    b = InPassBackend()
    d = Dispatcher(b)
    pid = call(d, "propose_diagnostic_test", sat_id=MOCK_SAT, commands=DIAGNOSTIC).result["plan_id"]
    plan = d.plans[pid]
    assert plan.uplink.aos < b.now() < plan.uplink.los  # the pass is in progress
    assert plan.commands[0].t_exec == b.now() + DIAGNOSTIC_STEP_S
    assert plan.commands[-1].t_exec + DIAGNOSTIC_STEP_S <= plan.uplink.los


def test_plan_commands_in_a_pass_in_progress_default_to_now_not_aos():
    b = InPassBackend()
    d = Dispatcher(b)
    pid = call(d, "propose_plan", sat_id=MOCK_SAT, commands=AUX_OFF).result["plan_id"]
    assert d.plans[pid].commands[0].t_exec == b.now()


def test_diagnostic_that_does_not_fit_the_pass_is_refused():
    d = Dispatcher(MockBackend())  # pass is 480 s long: lead + 8 steps + dwell = 600 s
    steps = (AUX_OFF + [{"name": "LOAD_ON", "args": {"load": "AUX"}}]) * 4
    r = call(d, "propose_diagnostic_test", sat_id=MOCK_SAT, commands=steps).result
    assert "fewer steps" in r["error"]
    assert d.plans == {}


def test_explicit_t_exec_in_the_past_is_refused():
    b = InPassBackend()
    d = Dispatcher(b)
    past = [{"name": "LOAD_OFF", "args": {"load": "AUX"}, "t_exec": b.now() - 30.0}]
    for tool in ("propose_diagnostic_test", "propose_plan"):
        assert "before the current time" in call(d, tool, sat_id=MOCK_SAT, commands=past).result["error"]


def test_diagnostic_uplink_deadline_is_its_first_step():
    d = Dispatcher(MockBackend())
    pid = call(d, "propose_diagnostic_test", sat_id=MOCK_SAT, commands=DIAGNOSTIC).result["plan_id"]
    call(d, "simulate", plan_id=pid)
    assert call(d, "schedule_uplink", plan_id=pid).result["scheduled"]
    first = d.plans[pid].commands[0].t_exec
    assert d._schedule_requests[pid].deadline_t == first
    assert d.assignments[pid].end_t <= first


# Execution and the re-score gate -----------------------------------------------------------------------------
def test_tier1_diagnostic_executes_at_once_and_is_recorded():
    b = MockBackend()
    d = Dispatcher(b)
    pid, out = run_diagnostic(d)
    r = out.result
    assert r["tier"] == 1 and r["status"] == "auto_executed" and r["flight_rule"] == DIAGNOSTIC_FLIGHT_RULE
    assert [p.plan_id for p in b.executed] == [pid]
    assert b.now() == r["t_done"] == d.plans[pid].commands[-1].t_exec + DIAGNOSTIC_STEP_S
    kind, payload = out.events[0]
    assert kind == "execution"
    assert payload == {"approval_id": r["approval_id"], "plan_id": pid, "sat_id": MOCK_SAT, "kind": "diagnostic",
                       "tier": 1, "flight_rule": DIAGNOSTIC_FLIGHT_RULE, "t_done": r["t_done"]}
    assert out.plan_hash == d.plans[pid].plan_hash


def test_plans_wait_for_the_rescore_after_a_diagnostic():
    d = Dispatcher(MockBackend())
    early = call(d, "propose_plan", sat_id=MOCK_SAT, commands=SHED).result["plan_id"]  # before the test
    before = call(d, "score_hypotheses", sat_id=MOCK_SAT).result
    assert len(before["plausible"]) > 1
    pid, _ = run_diagnostic(d)

    refused = call(d, "propose_plan", sat_id=MOCK_SAT, commands=SHED).result
    assert "score_hypotheses" in refused["error"] and pid in refused["error"]
    call(d, "simulate", plan_id=early)
    assert "score_hypotheses" in call(d, "request_approval", plan_id=early).result["error"]

    after = call(d, "score_hypotheses", sat_id=MOCK_SAT).result
    assert after["plausible"] == ["H2"] and max(after["scores"], key=after["scores"].get) == "H2"
    assert "plan_id" in call(d, "propose_plan", sat_id=MOCK_SAT, commands=SHED).result


def test_tier2_diagnostic_waits_for_the_flight_director():
    b = Tier2Backend()
    d = Dispatcher(b)
    pid, out = run_diagnostic(d)
    assert out.result["status"] == "awaiting_approval" and out.events == ()
    assert b.executed == []
    assert "plan_id" in call(d, "propose_plan", sat_id=MOCK_SAT, commands=SHED).result  # nothing ran yet


def test_execute_after_approval_runs_once_and_records():
    b = Tier2Backend()
    d = Dispatcher(b)
    pid, out = run_diagnostic(d)
    ap = out.result["approval_id"]
    with pytest.raises(SafetyError) as exc:
        d.execute(ap)
    assert exc.value.reason_key == "not_approved"
    plan = d.approve(ap, d.plans[pid].plan_hash)
    assert plan.plan_id == pid
    rec = ListRecorder()
    payload = d.execute(ap, rec)
    assert payload["tier"] == 2 and payload["kind"] == "diagnostic" and payload["flight_rule"] is None
    assert [e.kind for e in rec.events] == ["execution"]
    assert rec.events[0].plan_hash == plan.plan_hash
    with pytest.raises(SafetyError) as exc:
        d.execute(ap)
    assert exc.value.reason_key == "already_executed"
    assert "score_hypotheses" in call(d, "propose_plan", sat_id=MOCK_SAT, commands=SHED).result["error"]


# Stub behaviour ----------------------------------------------------------------------------------------------
def tool_names(run):
    return [tc.name for tc in run.tool_calls]


def test_stub_skips_the_diagnostic_when_one_hypothesis_is_plausible():
    run = run_agent(StubModel(), Dispatcher(ConfirmedBackend()))
    assert "propose_diagnostic_test" not in tool_names(run)
    assert run.final_text and "Diagnostic" not in run.final_text


def test_stub_plans_without_the_test_if_the_diagnostic_fails_verification():
    b = UnsafeDiagnosticBackend()
    run = run_agent(StubModel(), Dispatcher(b))
    names = tool_names(run)
    assert "propose_diagnostic_test" in names and b.executed == []
    assert names.count("request_approval") == 1 and run.final_text


def test_stub_stops_when_the_diagnostic_needs_a_human():
    run = run_agent(StubModel(), Dispatcher(Tier2Backend()))
    assert "propose_plan" not in tool_names(run)
    assert "waits for the Flight Director" in run.final_text
    assert c.narration_ok(run.final_text)


# The recorded run --------------------------------------------------------------------------------------------
def test_recorded_run_shows_stuck_on_load_confirmed_by_the_diagnostic(tmp_path):
    rec = record_run(StubModel(), Dispatcher(MockBackend()), run_id="t20-diagnostic", runs_dir=tmp_path)
    events = load_events(rec.path)
    results = [(i, e.payload["name"], e.payload["result"]) for i, e in enumerate(events) if e.kind == "tool_result"]
    scores = [(i, r) for i, n, r in results if n == "score_hypotheses"]
    assert len(scores) == 2
    (i_before, before), (i_after, after) = scores
    assert len(before["plausible"]) > 1  # not sure yet: array, stuck load or capacity

    executions = [(i, e.payload) for i, e in enumerate(events) if e.kind == "execution"]
    assert len(executions) == 1
    i_exec, ex = executions[0]
    assert ex["kind"] == "diagnostic" and ex["sat_id"] == MOCK_SAT and ex["flight_rule"] == DIAGNOSTIC_FLIGHT_RULE
    assert i_before < i_exec < i_after

    assert after["plausible"] == ["H2"] and after["labels"]["H2"] == "Stuck-on load"
    assert after["scores"]["H2"] > before["scores"]["H2"]
    first_plan = min(i for i, n, _ in results if n == "propose_plan")
    assert i_after < first_plan  # the plan is built on the updated scores
    assert rec.run.final_text and "H2" in rec.run.final_text and "Diagnostic" in rec.run.final_text

    # it replays to the same result
    replay = run_agent(ReplayModel(rec.path, pace_ms=0), Dispatcher(MockBackend()))
    assert replay.rendered_text == rec.run.rendered_text
