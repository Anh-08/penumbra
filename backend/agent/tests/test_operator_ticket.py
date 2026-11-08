"""Operator tickets for A2's T21: every plan in the matrix, red ones included, gets an approval_id for the Flight
Director's button. Pressing it on a red plan is refused with the failing hypotheses (physics has veto); the agent's
own request_approval still refuses failed plans."""
import pytest

import contracts as c
from backend.agent.dispatcher import Dispatcher
from backend.agent.interfaces import ListRecorder
from backend.agent.mock_backend import MOCK_SAT, MockBackend
from backend.agent.safety import SafetyError

AUX_OFF = [{"name": "LOAD_OFF", "args": {"load": "AUX"}}]  # fails under H2 (the AUX relay is stuck on)
SHED = [{"name": "SET_PAYLOAD_DUTY", "args": {"duty": 0.2}}, {"name": "LOAD_OFF", "args": {"load": "AUX"}}]


def simulated(d, commands):
    pid = d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": commands}).result["plan_id"]
    d.dispatch("simulate", {"plan_id": pid})
    return pid


def test_red_plan_gets_a_ticket_and_approve_refuses_it_with_failing_hypotheses():
    d = Dispatcher(MockBackend())
    pid = simulated(d, AUX_OFF)
    assert "error" in d.dispatch("request_approval", {"plan_id": pid}).result  # the agent may never ask
    ticket = d.operator_ticket(pid)
    assert (ticket.plan_id, ticket.tier, ticket.sat_id) == (pid, 2, MOCK_SAT)
    assert ticket.plan_hash == d.plans[pid].plan_hash and ticket.sim_id == d.sims[pid].sim_id
    rec = ListRecorder()
    refused = d.approve(ticket.approval_id, ticket.plan_hash, rec)
    assert isinstance(refused, c.ApprovalRefused)
    assert refused.reason_key == "failed_verification" and refused.failing_hypotheses == ["H2"]
    assert [e.kind for e in rec.events] == ["approval_refused"]
    assert rec.events[0].payload["approval_id"] == ticket.approval_id
    with pytest.raises(SafetyError):
        d.execute(ticket.approval_id)  # nothing was approved, nothing runs


def test_green_plan_reuses_the_agents_ticket():
    d = Dispatcher(MockBackend())
    pid = simulated(d, SHED)
    d.dispatch("schedule_uplink", {"plan_id": pid})
    agent = d.dispatch("request_approval", {"plan_id": pid}).result
    assert d.operator_ticket(pid).approval_id == agent["approval_id"]


def test_operator_ticket_on_an_unbooked_green_plan_needs_the_uplink_first():
    d = Dispatcher(MockBackend())
    pid = simulated(d, SHED)
    ticket = d.operator_ticket(pid)
    refused = d.approve(ticket.approval_id, ticket.plan_hash)
    assert isinstance(refused, c.ApprovalRefused) and refused.reason_key == "not_scheduled"
    d.dispatch("schedule_uplink", {"plan_id": pid})
    plan = d.approve(ticket.approval_id, ticket.plan_hash)
    assert plan.plan_id == pid
    assert d.execute(ticket.approval_id)["plan_id"] == pid


def test_a_new_simulation_gets_a_new_ticket():
    d = Dispatcher(MockBackend())
    pid = simulated(d, AUX_OFF)
    first = d.operator_ticket(pid)
    assert d.operator_ticket(pid).approval_id == first.approval_id  # same sim: same ticket
    d.dispatch("simulate", {"plan_id": pid})
    second = d.operator_ticket(pid)
    assert second.approval_id != first.approval_id and second.sim_id == d.sims[pid].sim_id
    assert d.lookup(second.approval_id) == second  # narration can refer to it


def test_unknown_or_unsimulated_plans_have_no_ticket():
    d = Dispatcher(MockBackend())
    with pytest.raises(SafetyError) as exc:
        d.operator_ticket("P-9")
    assert exc.value.reason_key == "unknown_plan"
    pid = d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": SHED}).result["plan_id"]
    with pytest.raises(SafetyError) as exc:
        d.operator_ticket(pid)
    assert exc.value.reason_key == "not_verified"


def test_tickets_coexist_until_one_recovery_plan_is_approved():
    d = Dispatcher(MockBackend())
    red, green, other = simulated(d, AUX_OFF), simulated(d, SHED), simulated(d, SHED)
    for pid in (green, other):
        d.dispatch("schedule_uplink", {"plan_id": pid})
    tickets = {pid: d.operator_ticket(pid) for pid in (red, green, other)}
    assert len({t.approval_id for t in tickets.values()}) == 3  # one per plan, all open at once
    assert d.approve(tickets[green].approval_id, tickets[green].plan_hash).plan_id == green
    second = d.approve(tickets[other].approval_id, tickets[other].plan_hash)
    assert isinstance(second, c.ApprovalRefused) and second.reason_key == "superseded"
    veto = d.approve(tickets[red].approval_id, tickets[red].plan_hash)
    assert veto.reason_key == "failed_verification" and veto.failing_hypotheses == ["H2"]  # red still shows why


def test_an_executed_diagnostic_does_not_supersede_the_recovery_plan():
    from backend.agent.stub import DIAGNOSTIC
    d = Dispatcher(MockBackend())
    diag = d.dispatch("propose_diagnostic_test", {"sat_id": MOCK_SAT, "commands": DIAGNOSTIC}).result["plan_id"]
    d.dispatch("simulate", {"plan_id": diag})
    d.dispatch("schedule_uplink", {"plan_id": diag})
    assert d.dispatch("request_approval", {"plan_id": diag}).result["status"] == "auto_executed"
    d.dispatch("score_hypotheses", {"sat_id": MOCK_SAT})
    pid = simulated(d, SHED)
    d.dispatch("schedule_uplink", {"plan_id": pid})
    ticket = d.operator_ticket(pid)
    assert d.approve(ticket.approval_id, ticket.plan_hash).plan_id == pid
