"""T26: fleet-mode agent on the SIMULATED fault storm. It works the triage queue for three satellites one at a
time in triage order, schedules every uplink, handles a station conflict, separates Tier 1 auto-execution (the
trace shows the flight rule id) from Tier 2 approval, says infeasible and recommends SAFE_MODE when only SAFE_MODE
passes, and produces a recorded run that replays."""
import contracts as c
from backend.agent.dispatcher import Dispatcher
from backend.agent.fleet import run_fleet
from backend.agent.interfaces import ListRecorder
from backend.agent.loop import MAX_STEPS
from backend.agent.mock_fleet import STORM_SATS, MockStormBackend
from backend.agent.session import record_run
from backend.agent.stub import StubModel
from backend.recorder import ReplayModel, load_events

SHED = [{"name": "SET_PAYLOAD_DUTY", "args": {"duty": 0.2}}, {"name": "LOAD_OFF", "args": {"load": "AUX"}}]


def storm(backend=None):
    b = backend or MockStormBackend()
    d = Dispatcher(b)
    rec = ListRecorder(run_id="t26")
    run = run_fleet(StubModel(), d, rec)
    return run, d, b, rec


def sat_of(d, call):
    if "sat_id" in call.args:
        return call.args["sat_id"]
    return d.plans[call.args["plan_id"]].sat_id


def results(rec, name):
    return [e.payload["result"] for e in rec.events if e.kind == "tool_result" and e.payload["name"] == name]


# Queue order and scheduling ------------------------------------------------------------------------------------
def test_works_three_satellites_one_at_a_time_in_triage_order():
    run, d, b, rec = storm()
    assert run.status == "done"
    assert run.queue == list(STORM_SATS) == [e.sat_id for e in b.triage()]
    assert [sat for sat, _ in run.runs] == list(STORM_SATS)
    for sat, r in run.runs:
        assert r.status == "done" and r.steps <= MAX_STEPS
        assert {sat_of(d, tc) for tc in r.tool_calls if tc.name != "get_fleet_status"} == {sat}
        assert r.final_text.startswith(sat)
    first = rec.events[0]
    assert (first.kind, first.payload["name"], first.payload["id"]) == ("tool_call", "get_fleet_status", "fleet-1")


def test_every_satellite_gets_a_scheduled_uplink():
    run, d, b, rec = storm()
    scheduled = {d.plans[r["plan_id"]].sat_id for r in results(rec, "schedule_uplink") if r["scheduled"]}
    assert scheduled == set(STORM_SATS)
    for ticket in d.approvals.values():
        assert ticket.plan_id in d.assignments  # no approval without a booked uplink


def test_scheduler_weight_follows_triage_urgency():
    run, d, b, rec = storm()
    by_sat = {r.sat_id: r.priority for r in d._schedule_requests.values()}
    assert by_sat["SAT-19"] > by_sat["SAT-07"]  # rank 1 outweighs rank 2 (the scheduler maximizes weight)


# Station conflict ----------------------------------------------------------------------------------------------
def test_station_conflict_moves_the_less_urgent_satellite_to_its_next_contact():
    run, d, b, rec = storm()
    sat12 = [r for r in results(rec, "schedule_uplink") if d.plans[r["plan_id"]].sat_id == "SAT-12"]
    assert [r["scheduled"] for r in sat12] == [False, True]
    assert "propose the same commands again" in sat12[0]["reason"]
    first, moved = d.plans[sat12[0]["plan_id"]], d.plans[sat12[1]["plan_id"]]
    assert (first.uplink.station_id, moved.uplink.station_id) == ("SVALBARD", "TROLL")
    assert moved.uplink.aos > first.uplink.los
    assert [c.model_dump() for c in moved.commands] != [] and moved.commands[0].name == first.commands[0].name
    sat07 = [r for r in results(rec, "schedule_uplink") if d.plans[r["plan_id"]].sat_id == "SAT-07"]
    assert sat07[-1]["station_id"] == "SVALBARD"  # the more urgent satellite keeps the shared contact
    assert d.bumped == set()


# Tier 1 / Tier 2 / infeasible ----------------------------------------------------------------------------------
def test_tier1_auto_executes_with_the_flight_rule_in_the_trace():
    run, d, b, rec = storm()
    executions = [e for e in rec.events if e.kind == "execution"]
    assert len(executions) == 1
    ex = executions[0].payload
    assert ex["sat_id"] == "SAT-12" and ex["tier"] == 1 and ex["flight_rule"] == "FR-3" and ex["kind"] == "plan"
    ticket = next(r for r in results(rec, "request_approval") if r.get("status") == "auto_executed")
    assert ticket["flight_rule"] == "FR-3" and ticket["plan_id"] == ex["plan_id"]
    assert "FR-3" in dict(run.runs)["SAT-12"].final_text
    assert [p.sat_id for p in b.executed] == ["SAT-12"]


def test_tier2_waits_for_the_flight_director():
    run, d, b, rec = storm()
    tickets = [t for t in d.approvals.values() if t.sat_id == "SAT-07"]
    assert len(tickets) == 1 and tickets[0].tier == 2
    assert not any(p.sat_id == "SAT-07" for p in b.executed)
    assert "waiting for the Flight Director" in dict(run.runs)["SAT-07"].final_text
    assert {t.sat_id: t.status for t in b.triage()}["SAT-07"] == "awaiting_approval"


def test_infeasible_satellite_gets_a_safe_mode_recommendation():
    run, d, b, rec = storm()
    text = dict(run.runs)["SAT-19"].final_text
    assert "infeasible" in text and "SAFE_MODE" in text
    ticket = next(t for t in d.approvals.values() if t.sat_id == "SAT-19")
    assert [cmd.name for cmd in d.plans[ticket.plan_id].commands] == ["SAFE_MODE"]
    assert ticket.tier == 2  # SAFE_MODE is recommended to the human, never auto-executed
    others = [p for p in d.plans.values() if p.sat_id == "SAT-19" and p.plan_id != ticket.plan_id]
    assert others and all(not d.sims[p.plan_id].passes_all for p in others)
    assert {t.sat_id: t.status for t in b.triage()}["SAT-19"] == "safe_mode_recommended"


def test_fleet_narration_follows_the_rule():
    run, d, b, rec = storm()
    for _, r in run.runs:
        assert c.narration_ok(r.final_text), c.bare_digits(r.final_text)
    assert run.rendered_text and "{" not in run.rendered_text


# Bumped uplinks ------------------------------------------------------------------------------------------------
class SwappedUrgency(MockStormBackend):
    """Queue order SAT-19, SAT-07, SAT-12, but SAT-12 is more urgent than SAT-07 for the scheduler."""

    def triage(self):
        rank = {"SAT-19": 1.0, "SAT-07": 3.0, "SAT-12": 2.0}
        return [e.model_copy(update={"priority": rank[e.sat_id]}) for e in super().triage()]


def test_a_bumped_satellite_gets_one_more_turn_and_moves_to_another_contact():
    run, d, b, rec = storm(SwappedUrgency())
    assert [sat for sat, _ in run.runs] == ["SAT-19", "SAT-07", "SAT-12", "SAT-07"]
    bumped = [r for r in results(rec, "schedule_uplink") if r.get("bumped")]
    assert bumped and d.plans[bumped[0]["bumped"][0]].sat_id == "SAT-07"
    old = next(t for t in d.approvals.values() if t.sat_id == "SAT-07")
    refused = d.approve(old.approval_id, old.plan_hash)
    assert isinstance(refused, c.ApprovalRefused) and refused.reason_key == "uplink_closed"
    new = [t for t in d.approvals.values() if t.sat_id == "SAT-07"][-1]
    assert new.plan_id in d.assignments and d.assignments[new.plan_id].station_id == "FAIRBANKS"
    assert run.status == "done"


def test_an_executed_plan_keeps_its_booked_slot():
    b = MockStormBackend()
    d = Dispatcher(b)
    run_fleet(StubModel(), d, ListRecorder())
    done = next(p for p in d._executed_plans)
    assert done not in d._schedule_requests  # not re-solved
    slot = d.assignments[done]
    # a new request that the scheduler would put on the same station and time does not get it
    pid = d.dispatch("propose_plan", {"sat_id": "SAT-12", "commands": SHED}).result["plan_id"]
    assert d.plans[pid].uplink.station_id == slot.station_id
    d.dispatch("simulate", {"plan_id": pid})
    r = d.dispatch("schedule_uplink", {"plan_id": pid}).result
    assert r["scheduled"] is False
    assert d.assignments[done] == slot


# Recorded run --------------------------------------------------------------------------------------------------
def test_fleet_run_is_recorded_and_replays(tmp_path):
    rec = record_run(StubModel(), Dispatcher(MockStormBackend()), run_id="t26-storm", runs_dir=tmp_path, fleet=True)
    events = load_events(rec.path)
    kinds = [e.kind for e in events]
    assert kinds[0] == "tool_call" and kinds.count("execution") == 1
    assert [e.seq for e in events] == list(range(len(events)))
    goals = [e for e in events if e.kind == "model_request" and e.payload["step"] == 1]
    assert len(goals) == 3
    replay = run_fleet(ReplayModel(rec.path, pace_ms=0), Dispatcher(MockStormBackend()), ListRecorder())
    assert replay.rendered_text == rec.run.rendered_text
    assert [(tc.name, tc.args) for tc in replay.tool_calls] == [(tc.name, tc.args) for tc in rec.run.tool_calls]
