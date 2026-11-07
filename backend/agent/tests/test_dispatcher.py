"""T06: the dispatcher validates tool args and calls the backend interfaces (mock backend here)."""
import json
import math

import pytest

import contracts as c
from backend.agent.dispatcher import MAX_TELEMETRY_POINTS, Dispatcher
from backend.agent.mock_backend import MOCK_SAT, MockBackend

AUX_OFF = [{"name": "LOAD_OFF", "args": {"load": "AUX"}}]
SHED = [{"name": "SET_PAYLOAD_DUTY", "args": {"duty": 0.2}}, {"name": "LOAD_OFF", "args": {"load": "AUX"}}]


@pytest.fixture
def d():
    return Dispatcher(MockBackend())


def call(d, name, **args):
    out = d.dispatch(name, args)
    json.dumps(out.result, allow_nan=False)  # every result is plain JSON for the model and the recorder
    return out.result


def test_get_fleet_status_returns_triage_and_alerts(d):
    r = call(d, "get_fleet_status")
    assert r["triage"][0]["sat_id"] == MOCK_SAT
    assert r["alerts"][0]["alert_id"].startswith("AL-")
    assert isinstance(r["t"], float)


def test_get_telemetry_is_downlinked_only_and_bounded(d):
    r = call(d, "get_telemetry", sat_id=MOCK_SAT)
    assert 0 < len(r["t"]) <= MAX_TELEMETRY_POINTS
    assert r["t"] == sorted(r["t"])
    for ch, values in r["channels"].items():
        assert len(values) == len(r["t"]), ch
        assert all(v is None or math.isfinite(v) for v in values)
    assert all(v is not None for v in r["channels"]["soc"])


def test_get_telemetry_rejects_bad_window(d):
    t = d.backend.now()
    assert "error" in call(d, "get_telemetry", sat_id=MOCK_SAT, t_from=t, t_to=t - 60)


def test_score_hypotheses(d):
    r = call(d, "score_hypotheses", sat_id=MOCK_SAT)
    assert set(r["scores"]) == set(c.HYPOTHESES)
    assert set(r["plausible"]) <= set(c.HYPOTHESES)
    assert r["labels"]["H2"] == "Stuck-on load"


def test_propose_plan_builds_a_contract_plan(d):
    r = call(d, "propose_plan", sat_id=MOCK_SAT, commands=AUX_OFF)
    assert r == {"plan_id": "P-1"}
    plan = d.plans["P-1"]
    assert isinstance(plan, c.Plan)
    assert plan.kind == "plan" and plan.sat_id == MOCK_SAT
    assert plan.uplink == d.backend.next_contact(MOCK_SAT, d.backend.now())
    assert plan.commands[0].t_exec == plan.uplink.aos  # default: execute at uplink AOS
    assert call(d, "propose_plan", sat_id=MOCK_SAT, commands=SHED) == {"plan_id": "P-2"}


def test_propose_plan_returns_hash_to_the_recorder(d):
    out = d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": AUX_OFF})
    assert out.plan_hash == c.plan_hash(d.plans["P-1"])


def test_propose_diagnostic_test_is_a_diagnostic_plan(d):
    assert call(d, "propose_diagnostic_test", sat_id=MOCK_SAT, commands=AUX_OFF) == {"plan_id": "P-1"}
    assert d.plans["P-1"].kind == "diagnostic"


def test_time_tagged_command_keeps_its_t_exec(d):
    aos = d.backend.next_contact(MOCK_SAT, d.backend.now()).aos
    cmd = [{"name": "SAFE_MODE", "t_exec": aos + 900}]
    call(d, "propose_plan", sat_id=MOCK_SAT, commands=cmd)
    assert d.plans["P-1"].commands[0].t_exec == aos + 900


@pytest.mark.parametrize(
    "commands",
    [
        [{"name": "REBOOT", "args": {}}],  # not on the allowlist
        [{"name": "LOAD_OFF", "args": {"load": "BUS"}}],  # essential load
        [{"name": "SET_PAYLOAD_DUTY", "args": {"duty": 1.5}}],  # out of range
        [{"name": "LOAD_OFF", "args": {}}],  # missing arg
        [{"name": "LOAD_OFF", "args": {"load": "AUX"}, "t_exec": float("nan")}],  # non-finite time
        [{"name": "LOAD_OFF", "args": {"load": "AUX"}, "t_exec": 0.0}],  # before uplink AOS
        [{"name": "LOAD_OFF", "args": {"load": "AUX"}, "when": "now"}],  # unknown field
        [],  # empty plan
        "LOAD_OFF AUX",  # not a list
    ],
)
def test_propose_plan_rejects_bad_commands(d, commands):
    r = call(d, "propose_plan", sat_id=MOCK_SAT, commands=commands)
    assert set(r) == {"error"}
    assert d.plans == {}


def test_bad_tool_calls_return_errors(d):
    assert "error" in call(d, "launch_missiles")
    assert "error" in call(d, "score_hypotheses")  # missing required arg
    assert "error" in call(d, "score_hypotheses", sat_id=MOCK_SAT, extra=1)  # unknown arg
    assert "error" in call(d, "score_hypotheses", sat_id="SAT-99")
    assert "error" in call(d, "simulate", plan_id="P-404")
    assert "error" in d.dispatch("score_hypotheses", "SAT-07").result  # args not an object
    assert "triage" in d.dispatch("get_fleet_status", None).result  # no-arg call may send None


def test_simulate_stores_latest_result(d):
    call(d, "propose_plan", sat_id=MOCK_SAT, commands=SHED)
    r = call(d, "simulate", plan_id="P-1")
    sim = c.SimResult.model_validate(r)
    assert sim.plan_id == "P-1" and sim.plan_hash == c.plan_hash(d.plans["P-1"])
    assert d.sims["P-1"] == sim
    assert set(sim.per_hyp) == set(c.HYPOTHESES)  # default "all"


def test_simulate_accepts_hypothesis_subset_and_float_monte_carlo(d):
    call(d, "propose_plan", sat_id=MOCK_SAT, commands=SHED)
    r = call(d, "simulate", plan_id="P-1", hypotheses=["H0", "H2"], monte_carlo=50.0)
    assert set(r["per_hyp"]) == {"H0", "H2"}
    assert "error" in call(d, "simulate", plan_id="P-1", hypotheses=["H9"])
    assert "error" in call(d, "simulate", plan_id="P-1", monte_carlo=0)
    assert "error" in call(d, "simulate", plan_id="P-1", monte_carlo=2.5)
    assert "error" in call(d, "simulate", plan_id="P-1", monte_carlo=float("inf"))
    assert "error" in call(d, "simulate", plan_id="P-1", monte_carlo=float("nan"))


def test_schedule_uplink_assigns_a_station(d):
    call(d, "propose_plan", sat_id=MOCK_SAT, commands=SHED)
    call(d, "simulate", plan_id="P-1")
    r = call(d, "schedule_uplink", plan_id="P-1")
    assert r["scheduled"] is True
    assert r["station_id"] in c.STATION_IDS
    assert r["start_t"] < r["end_t"]
    assert isinstance(d.last_schedule, c.ScheduleResult)


def test_schedule_uplink_needs_a_passing_simulation(d):
    call(d, "propose_plan", sat_id=MOCK_SAT, commands=AUX_OFF)
    assert "error" in call(d, "schedule_uplink", plan_id="P-1")  # not simulated yet
    call(d, "simulate", plan_id="P-1")  # fails under H2 (AUX is stuck on)
    assert "error" in call(d, "schedule_uplink", plan_id="P-1")


def test_request_approval_only_for_verified_plans(d):
    call(d, "propose_plan", sat_id=MOCK_SAT, commands=AUX_OFF)
    assert "error" in call(d, "request_approval", plan_id="P-1")  # not simulated
    assert call(d, "simulate", plan_id="P-1")["passes_all"] is False
    assert "error" in call(d, "request_approval", plan_id="P-1")  # failed verification
    assert d.approvals == {}

    call(d, "propose_plan", sat_id=MOCK_SAT, commands=SHED)
    sim = call(d, "simulate", plan_id="P-2")
    assert sim["passes_all"] is True
    assert "schedule_uplink" in call(d, "request_approval", plan_id="P-2")["error"]  # no uplink slot yet
    call(d, "schedule_uplink", plan_id="P-2")
    r = call(d, "request_approval", plan_id="P-2")
    assert r == {"approval_id": "AP-1", "plan_id": "P-2", "tier": 2, "status": "awaiting_approval"}
    ticket = d.approvals["AP-1"]
    assert isinstance(ticket, c.ApprovalRequestMsg)
    assert (ticket.plan_id, ticket.plan_hash, ticket.sim_id) == ("P-2", sim["plan_hash"], sim["sim_id"])
