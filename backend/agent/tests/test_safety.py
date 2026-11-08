"""T10 safety layer, including the CLAUDE.md §8 agent tests. Each of these is rejected:
a command that is not on the allowlist; LOAD_OFF on an essential load; an uplink outside a contact window;
an approval with a stale hash; narration with a bare digit outside an allowed token."""
import pytest

import contracts as c
from backend.agent import safety
from backend.agent.dispatcher import Dispatcher
from backend.agent.interfaces import ListRecorder
from backend.agent.mock_backend import MOCK_SAT, MockBackend
from backend.agent.narration import narration_problems

AUX_OFF = [{"name": "LOAD_OFF", "args": {"load": "AUX"}}]
SHED = [{"name": "SET_PAYLOAD_DUTY", "args": {"duty": 0.2}}, {"name": "LOAD_OFF", "args": {"load": "AUX"}}]


class ClockBackend(MockBackend):
    """Mock backend whose clock the test can move."""

    def __init__(self):
        super().__init__()
        self.t = self.t0

    def now(self):
        return self.t


@pytest.fixture
def b():
    return ClockBackend()


@pytest.fixture
def d(b):
    return Dispatcher(b)


def window(b):
    return b.next_contact(MOCK_SAT, b.now())


def verified_ticket(d, commands=SHED):
    pid = d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": commands}).result["plan_id"]
    sim = d.dispatch("simulate", {"plan_id": pid}).result
    d.dispatch("schedule_uplink", {"plan_id": pid})
    ticket = d.dispatch("request_approval", {"plan_id": pid}).result
    return pid, sim, ticket


# ---------------------------------------------------------------- §8: allowlist
@pytest.mark.parametrize("name", ["REBOOT", "load_off", "FORMAT_DISK", "", None])
def test_s8_command_not_on_allowlist_is_rejected(d, b, name):
    with pytest.raises(safety.SafetyError) as e:
        safety.parse_command({"name": name, "args": {}}, default_t_exec=window(b).aos)
    assert e.value.reason_key == "not_allowlisted"
    r = d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": [{"name": name, "args": {}}]}).result
    assert r["error"].startswith("not_allowlisted")
    assert d.plans == {}


def test_s8_unvalidated_command_object_is_still_rejected(b):
    """Defense in depth: a Command built without validation never passes the safety layer."""
    bad = c.Command.model_construct(t_exec=window(b).aos, name="REBOOT", args={})
    with pytest.raises(safety.SafetyError) as e:
        safety.check_command(bad)
    assert e.value.reason_key == "not_allowlisted"


# ---------------------------------------------------------------- §8: essential loads
@pytest.mark.parametrize("load", c.ESSENTIAL_LOADS)
def test_s8_load_off_on_essential_load_is_rejected(d, b, load):
    cmd = {"name": "LOAD_OFF", "args": {"load": load}}
    with pytest.raises(safety.SafetyError) as e:
        safety.parse_command(cmd, default_t_exec=window(b).aos)
    assert e.value.reason_key == "essential_load"
    bypass = c.Command.model_construct(t_exec=window(b).aos, name="LOAD_OFF", args={"load": load})
    with pytest.raises(safety.SafetyError) as e:
        safety.check_command(bypass)
    assert e.value.reason_key == "essential_load"
    r = d.dispatch("propose_diagnostic_test", {"sat_id": MOCK_SAT, "commands": [cmd]}).result
    assert r["error"].startswith("essential_load")


@pytest.mark.parametrize("load", c.ESSENTIAL_LOADS)
def test_load_on_essential_load_is_fine(b, load):
    safety.parse_command({"name": "LOAD_ON", "args": {"load": load}}, default_t_exec=window(b).aos)


@pytest.mark.parametrize(
    "cmd, reason",
    [
        ({"name": "SET_PAYLOAD_DUTY", "args": {"duty": 1.2}}, "bad_args"),
        ({"name": "SET_HEATER_SETPOINT", "args": {"setpoint_c": 40}}, "bad_args"),
        ({"name": "LOAD_OFF", "args": {"load": "AUX", "extra": 1}}, "bad_args"),
        ({"name": "LOAD_OFF", "args": {"load": "AUX"}, "t_exec": float("inf")}, "bad_time"),
        ({"name": "LOAD_OFF", "args": {"load": "AUX"}, "t_exec": "soon"}, "bad_time"),
        ({"name": "SAFE_MODE", "args": {}, "priority": "high"}, "bad_args"),
    ],
)
def test_bad_args_and_times_are_rejected(b, cmd, reason):
    with pytest.raises(safety.SafetyError) as e:
        safety.parse_command(cmd, default_t_exec=window(b).aos)
    assert e.value.reason_key == reason


# ---------------------------------------------------------------- §8: uplink inside a contact window
def plan_with_uplink(uplink, t_exec=None):
    cmd = c.Command(t_exec=uplink.aos if t_exec is None else t_exec, name="LOAD_OFF", args={"load": "AUX"})
    return c.Plan(plan_id="P-9", sat_id=uplink.sat_id, kind="plan", uplink=uplink, commands=[cmd], created_by="test")


@pytest.mark.parametrize(
    "shift",
    [
        {"aos": -120.0, "los": 0.0},  # starts before AOS
        {"aos": 0.0, "los": 120.0},  # ends after LOS
        {"aos": 3000.0, "los": 3300.0},  # nowhere near a contact
    ],
)
def test_s8_uplink_outside_contact_window_is_rejected(b, shift):
    w = window(b)
    bad = w.model_copy(update={"aos": w.aos + shift["aos"], "los": w.los + shift["los"]})
    with pytest.raises(safety.SafetyError) as e:
        safety.check_plan(plan_with_uplink(bad), b.contacts())
    assert e.value.reason_key == "uplink_outside_contact"


def test_s8_uplink_at_wrong_station_is_rejected(b):
    w = window(b)
    other = "TROLL" if w.station_id != "TROLL" else "FAIRBANKS"
    with pytest.raises(safety.SafetyError) as e:
        safety.check_plan(plan_with_uplink(w.model_copy(update={"station_id": other})), b.contacts())
    assert e.value.reason_key == "uplink_outside_contact"


def test_uplink_inside_contact_window_passes(b):
    w = window(b)
    inner = w.model_copy(update={"aos": w.aos + 60, "los": w.los - 60})
    safety.check_plan(plan_with_uplink(w), b.contacts())
    safety.check_plan(plan_with_uplink(inner), b.contacts())


def test_s8_dispatcher_rejects_backend_uplink_outside_contacts(b):
    class BadOrbit(ClockBackend):
        def next_contact(self, sat_id, t):
            w = super().next_contact(sat_id, t)
            return w.model_copy(update={"aos": w.aos - 300, "los": w.aos - 60})

    d = Dispatcher(BadOrbit())
    r = d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": AUX_OFF}).result
    assert r["error"].startswith("uplink_outside_contact")
    assert d.plans == {}


def test_uplink_already_closed_is_rejected(b):
    w = window(b)
    with pytest.raises(safety.SafetyError) as e:
        safety.check_plan(plan_with_uplink(w), b.contacts(), now=w.los)
    assert e.value.reason_key == "uplink_closed"


def test_assignment_outside_contact_is_rejected(b):
    w = window(b)
    ok = c.Assignment(plan_id="P-1", station_id=w.station_id, start_t=w.aos, end_t=w.aos + 60)
    safety.check_assignment(ok, MOCK_SAT, b.contacts())
    late = ok.model_copy(update={"start_t": w.los - 30, "end_t": w.los + 30})
    with pytest.raises(safety.SafetyError) as e:
        safety.check_assignment(late, MOCK_SAT, b.contacts())
    assert e.value.reason_key == "uplink_outside_contact"


# ---------------------------------------------------------------- canonical plan hash
def test_plan_hash_is_canonical(b):
    w = window(b)
    p1 = c.Plan(plan_id="P-1", sat_id=MOCK_SAT, kind="plan", uplink=w, created_by="agent",
                commands=[c.Command(t_exec=w.aos, name="SET_PAYLOAD_DUTY", args={"duty": 1})])
    p2 = c.Plan(plan_id="P-7", sat_id=MOCK_SAT, kind="plan", uplink=w, created_by="human",
                commands=[c.Command(t_exec=int(w.aos), name="SET_PAYLOAD_DUTY", args={"duty": 1.0})])
    assert safety.plan_hash(p1) == safety.plan_hash(p2)  # ids, author and 1 vs 1.0 don't matter
    p3 = p1.model_copy(update={"commands": [c.Command(t_exec=w.aos, name="SET_PAYLOAD_DUTY", args={"duty": 0.9})]})
    p4 = p1.model_copy(update={"kind": "diagnostic"})
    p5 = p1.model_copy(update={"commands": [c.Command(t_exec=w.aos + 10, name="SET_PAYLOAD_DUTY", args={"duty": 1})]})
    hashes = {safety.plan_hash(p) for p in (p1, p3, p4, p5)}
    assert len(hashes) == 4
    assert all(len(h) == 64 for h in hashes)


def test_plan_hash_depends_on_command_order(b):
    w = window(b)
    a = c.Command(t_exec=w.aos, name="LOAD_OFF", args={"load": "AUX"})
    s = c.Command(t_exec=w.aos, name="SET_PAYLOAD_DUTY", args={"duty": 0.2})
    base = dict(plan_id="P-1", sat_id=MOCK_SAT, kind="plan", uplink=w, created_by="agent")
    assert safety.plan_hash(c.Plan(**base, commands=[a, s])) != safety.plan_hash(c.Plan(**base, commands=[s, a]))


# ---------------------------------------------------------------- §8: stale-hash approval
def test_s8_approval_with_stale_hash_is_refused(d):
    pid, sim, ticket = verified_ticket(d)
    rec = ListRecorder()
    stale = "0" * 64
    out = d.approve(ticket["approval_id"], stale, recorder=rec)
    assert isinstance(out, c.ApprovalRefused)
    assert out.reason_key == "stale_hash"
    assert rec.events[-1].kind == "approval_refused"
    assert rec.events[-1].payload["reason_key"] == "stale_hash"


def test_s8_approval_with_hash_of_another_plan_is_refused(d):
    _, sim_a, ticket_a = verified_ticket(d)
    _, sim_b, _ = verified_ticket(d, commands=[{"name": "SET_PAYLOAD_DUTY", "args": {"duty": 0.1}},
                                               {"name": "LOAD_OFF", "args": {"load": "AUX"}}])
    out = d.approve(ticket_a["approval_id"], sim_b["plan_hash"])
    assert isinstance(out, c.ApprovalRefused) and out.reason_key == "stale_hash"


def test_approval_with_current_hash_executes_once(d):
    pid, sim, ticket = verified_ticket(d)
    rec = ListRecorder()
    out = d.approve(ticket["approval_id"], sim["plan_hash"], recorder=rec)
    assert out == d.plans[pid]
    assert rec.events[-1].kind == "approval" and rec.events[-1].plan_hash == sim["plan_hash"]
    again = d.approve(ticket["approval_id"], sim["plan_hash"])
    assert isinstance(again, c.ApprovalRefused) and again.reason_key == "already_decided"


def test_approval_refused_after_failed_resimulation(d, b):
    pid, sim, ticket = verified_ticket(d)
    failing = c.SimResult.model_validate(
        {**sim, "sim_id": "S-99", "passes_all": False,
         "per_hyp": {**sim["per_hyp"], "H2": {**sim["per_hyp"]["H2"], "pass_rate": 0.4}}}
    )
    d.record_sim(failing)  # e.g. re-simulated after new telemetry
    out = d.approve(ticket["approval_id"], sim["plan_hash"])
    assert isinstance(out, c.ApprovalRefused)
    assert out.reason_key == "failed_verification"
    assert out.failing_hypotheses == ["H2"]


def test_approval_refused_after_uplink_closed(d, b):
    pid, sim, ticket = verified_ticket(d)
    b.t = d.plans[pid].uplink.los + 1
    out = d.approve(ticket["approval_id"], sim["plan_hash"])
    assert isinstance(out, c.ApprovalRefused) and out.reason_key == "uplink_closed"


def test_unknown_approval_is_refused(d):
    out = d.approve("AP-404", "0" * 64)
    assert isinstance(out, c.ApprovalRefused) and out.reason_key == "unknown_approval"


def test_failing_hypotheses_lists_plausible_failures(d):
    pid = d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": AUX_OFF}).result["plan_id"]
    sim = c.SimResult.model_validate(d.dispatch("simulate", {"plan_id": pid}).result)
    assert not sim.passes_all
    assert safety.failing_hypotheses(sim) == ["H2"]


# ---------------------------------------------------------------- §8: narration
@pytest.mark.parametrize(
    "text",
    [
        "SOC will drop to 18 percent.",
        "Margin is 0.04 under H2.",
        "SAT-07 needs 3 commands.",
        "Uplink at 18:10 UTC.",
        "SAT-07 margin {S-1.worst_margin_soc}, about 4%.",
        "Two plans, P-1 and P-2, took 2 tries.",
    ],
)
def test_s8_narration_with_bare_digit_is_rejected(d, text):
    verified_ticket(d)
    problems = narration_problems(text, d)
    assert problems and any(p.startswith("bare digits") for p in problems)
