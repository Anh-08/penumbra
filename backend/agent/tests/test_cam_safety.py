"""T31: collision-avoidance burns (CAM_BURN) in the agent's safety layer and dispatcher. A burn must lie inside its
backend-offered window and before tca - CAM_LEAD_S, a conjunction gets one approved burn, a CAM is never Tier 1,
and the five original safety rejections (test_safety.py) still hold."""
import pytest

import contracts as c
from backend.agent import safety
from backend.agent.dispatcher import Dispatcher
from backend.agent.mock_backend import MOCK_SAT, MockBackend
from backend.agent.narration import narration_problems, render
from backend.agent.safety import SafetyError

LEAD = c.CAM_LEAD_S


class ConjBackend(MockBackend):
    """Mock with one conjunction for SAT-07. W1 rides the first pass (battery low), W2 between passes, W3 late and
    ending exactly at tca - CAM_LEAD_S. Toy verifier: W1 drains the battery, W2 and W3 pass."""

    margins = {"W1": -0.05, "W2": 0.06, "W3": 0.02}

    def __init__(self, tier=(2, None)):
        super().__init__()
        t0 = self.t0
        self.tca = t0 + 6700 + LEAD
        self._tier = tier
        self.conj = [c.Conjunction(conj_id="CJ-1", sat_id=MOCK_SAT, object_id="DEB-01", tca=self.tca, miss_km=0.3,
                                   windows=[self.window("W1", t0 + 700, t0 + 1000, 0.02),
                                            self.window("W2", t0 + 3000, t0 + 3600, 0.05),
                                            self.window("W3", t0 + 6400, t0 + 6700, 0.12)])]

    @staticmethod
    def window(wid, a, b, dv):
        return c.ManeuverWindow(window_id=wid, t_start=a, t_end=b, dv_mps=dv, burn_s=30, power_w=40,
                                miss_after_km=2.0)

    def conjunctions(self):
        return list(self.conj)

    def tier(self, plan, sim):
        return self._tier

    def simulate(self, plan, hypotheses, monte_carlo):
        sim = super().simulate(plan, hypotheses, monte_carlo)
        burn = next((x for x in plan.commands if x.name == "CAM_BURN"), None)
        if burn is None:
            return sim
        m = self.margins[burn.args["window_id"]]
        return sim.model_copy(update={"passes_all": m >= 0, "worst_margin_soc": m})


def cam(window_id, t_exec=None, extra=()):
    burn = {"name": "CAM_BURN", "args": {"window_id": window_id}}
    if t_exec is not None:
        burn["t_exec"] = t_exec
    return [*extra, burn]


def propose(d, commands, tool="propose_plan"):
    return d.dispatch(tool, {"sat_id": MOCK_SAT, "commands": commands}).result


def approved(d, pid):
    d.dispatch("simulate", {"plan_id": pid})
    d.dispatch("schedule_uplink", {"plan_id": pid})
    ticket = d.dispatch("request_approval", {"plan_id": pid}).result
    assert d.approve(ticket["approval_id"], d.plans[pid].plan_hash).plan_id == pid
    return ticket


# get_conjunctions and narration ---------------------------------------------------------------------------------
def test_get_conjunctions_lists_the_windows_and_feeds_narration():
    d = Dispatcher(ConjBackend())
    r = d.dispatch("get_conjunctions", {"sat_id": MOCK_SAT}).result
    assert [cj["conj_id"] for cj in r["conjunctions"]] == ["CJ-1"]
    assert [w["window_id"] for w in r["conjunctions"][0]["windows"]] == ["W1", "W2", "W3"]
    assert d.dispatch("get_conjunctions", {"sat_id": "SAT-12"}).result["conjunctions"] == []
    text = "SAT-07 meets DEB-01 at {CJ-1.tca}, miss {CJ-1.miss_km}; CJ-1 window W2 passes."
    assert narration_problems(text, d) == []
    assert "miss 0.30 km" in render(text, d)
    assert narration_problems("SAT-07 meets DEB-02.", d)  # debris nobody reported is an unknown id


def test_a_backend_without_conjunctions_has_none_and_refuses_cam():
    d = Dispatcher(MockBackend())
    assert d.dispatch("get_conjunctions", {"sat_id": MOCK_SAT}).result["conjunctions"] == []
    assert "cam_no_conjunction" in propose(d, cam("W2"))["error"]


# a burn becomes a maneuver plan, at its window by default ---------------------------------------------------------
def test_cam_burn_makes_a_maneuver_plan_executing_at_the_window_start():
    b = ConjBackend()
    d = Dispatcher(b)
    pid = propose(d, cam("W2", extra=[{"name": "SET_PAYLOAD_DUTY", "args": {"duty": 0.2}}]))["plan_id"]
    plan = d.plans[pid]
    assert plan.kind == "maneuver"
    burn = next(x for x in plan.commands if x.name == "CAM_BURN")
    assert burn.t_exec == b.conj[0].window("W2").t_start
    assert d._schedule_requests.get(pid) is None
    d.dispatch("simulate", {"plan_id": pid})
    d.dispatch("schedule_uplink", {"plan_id": pid})
    assert d._schedule_requests[pid].deadline_t <= burn.t_exec  # uplinked before the burn


# rejections ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("offset", [-60.0, 601.0])
def test_cam_burn_outside_its_window_is_rejected(offset):
    b = ConjBackend()
    d = Dispatcher(b)
    w2 = b.conj[0].window("W2")
    r = propose(d, cam("W2", t_exec=w2.t_start + offset))  # before the window opens, or after it closes
    assert "cam_outside_window" in r["error"] and d.plans == {}


def test_cam_burn_at_or_after_tca_minus_lead_is_rejected():
    b = ConjBackend()
    d = Dispatcher(b)
    w3 = b.conj[0].window("W3")
    assert w3.t_end == b.tca - LEAD
    assert "cam_too_late" in propose(d, cam("W3", t_exec=w3.t_end))["error"]
    assert "plan_id" in propose(d, cam("W3", t_exec=w3.t_end - 1.0))


def test_too_late_holds_even_when_backend_data_breaks_the_contract():
    b = ConjBackend()
    late = c.ManeuverWindow.model_construct(window_id="W1", t_start=b.tca - 2 * LEAD, t_end=b.tca - 600.0,
                                            dv_mps=0.01, burn_s=30, power_w=40, miss_after_km=2.0)
    b.conj = [c.Conjunction.model_construct(conj_id="CJ-1", sat_id=MOCK_SAT, object_id="DEB-01", tca=b.tca,
                                            miss_km=0.3, windows=[late])]
    d = Dispatcher(b)
    assert "cam_too_late" in propose(d, cam("W1", t_exec=b.tca - 900.0))["error"]


def test_a_second_cam_burn_for_the_same_conjunction_is_rejected():
    d = Dispatcher(ConjBackend())
    assert "cam_duplicate" in propose(d, cam("W2") + cam("W3"))["error"]  # two burns in one plan
    p2 = propose(d, cam("W2"))["plan_id"]
    p3 = propose(d, cam("W3"))["plan_id"]  # an alternative may be proposed and judged ...
    approved(d, p2)
    assert "cam_duplicate" in propose(d, cam("W3"))["error"]  # ... but not a new burn after one is approved
    d.dispatch("simulate", {"plan_id": p3})
    d.dispatch("schedule_uplink", {"plan_id": p3})
    ticket = d.operator_ticket(p3)
    refused = d.approve(ticket.approval_id, ticket.plan_hash)
    assert isinstance(refused, c.ApprovalRefused) and refused.reason_key == "cam_duplicate"


def test_a_failing_window_is_refused_with_its_hypotheses():
    d = Dispatcher(ConjBackend())
    pid = propose(d, cam("W1"))["plan_id"]
    assert d.dispatch("simulate", {"plan_id": pid}).result["passes_all"] is False
    ticket = d.operator_ticket(pid)
    refused = d.approve(ticket.approval_id, ticket.plan_hash)
    assert refused.reason_key == "failed_verification" and refused.failing_hypotheses


def test_cam_is_never_tier_1():
    b = ConjBackend(tier=(1, "FR-2"))
    d = Dispatcher(b)
    pid = propose(d, cam("W2"))["plan_id"]
    d.dispatch("simulate", {"plan_id": pid})
    d.dispatch("schedule_uplink", {"plan_id": pid})
    out = d.dispatch("request_approval", {"plan_id": pid})
    assert out.result["tier"] == 2 and out.result["status"] == "awaiting_approval" and out.events == ()
    assert b.executed == []  # nothing ran on its own: the Flight Director decides


@pytest.mark.parametrize("commands, key", [
    (cam("W4"), "bad_args"),
    ([{"name": "CAM_BURN", "args": {}}], "bad_args"),
    ([{"name": "CAM_BURN", "args": {"window_id": "W2", "dv_mps": 5.0}}], "bad_args"),  # the model never writes dv
])
def test_malformed_cam_burns_are_rejected(commands, key):
    assert key in propose(Dispatcher(ConjBackend()), commands)["error"]


def test_cam_burn_is_not_a_diagnostic():
    assert "maneuver" in propose(Dispatcher(ConjBackend()), cam("W1"), tool="propose_diagnostic_test")["error"]


def test_approval_rechecks_the_window_against_fresh_conjunction_data():
    b = ConjBackend()
    d = Dispatcher(b)
    pid = propose(d, cam("W2"))["plan_id"]
    d.dispatch("simulate", {"plan_id": pid})
    d.dispatch("schedule_uplink", {"plan_id": pid})
    ticket = d.dispatch("request_approval", {"plan_id": pid}).result
    t0 = b.t0  # the orbit update moves W2 later: the uplinked burn time is no longer inside it
    b.conj = [b.conj[0].model_copy(update={"windows": [b.window("W2", t0 + 3300, t0 + 3600, 0.05)]})]
    refused = d.approve(ticket["approval_id"], d.plans[pid].plan_hash)
    assert isinstance(refused, c.ApprovalRefused) and refused.reason_key == "cam_outside_window"


def test_check_maneuver_directly():
    b = ConjBackend()
    d = Dispatcher(b)
    pid = propose(d, cam("W2"))["plan_id"]
    assert safety.check_maneuver(d.plans[pid], b.conjunctions()).conj_id == "CJ-1"
    with pytest.raises(SafetyError) as exc:
        safety.check_maneuver(d.plans[pid], None)
    assert exc.value.reason_key == "cam_no_conjunction"
