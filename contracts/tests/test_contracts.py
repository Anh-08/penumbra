import typing

import pytest
from pydantic import ValidationError

import contracts as c

T0 = 1_794_000_000.0


def contact(**kw):
    base = dict(sat_id="SAT-07", station_id="SVALBARD", aos=T0, los=T0 + 600, max_el_deg=42.0)
    return c.Contact(**{**base, **kw})


def plan(**kw):
    base = dict(
        plan_id="P-1", sat_id="SAT-07", kind="plan", uplink=contact(), created_by="agent",
        commands=[c.Command(t_exec=T0 + 60, name="LOAD_OFF", args={"load": "AUX"})],
    )
    return c.Plan(**{**base, **kw})


def literal_values(tp):
    return typing.get_args(tp)


def test_literals_match_constants():
    assert set(literal_values(c.LoadName)) == set(c.LOADS)
    assert literal_values(c.FaultKind) == c.FAULT_KINDS
    assert literal_values(c.Hypothesis) == c.HYPOTHESES
    assert literal_values(c.CommandName) == c.COMMAND_NAMES
    assert literal_values(c.StationId) == c.STATION_IDS
    assert literal_values(c.DemoState) == c.DEMO_STATES
    assert literal_values(c.RecorderKind) == c.RECORDER_KINDS
    assert set(c.HYPOTHESIS_LABELS) == set(c.HYPOTHESES)
    assert c.HYPOTHESIS_KIND["H2"] == "stuck_on_load"
    assert c.HYPOTHESIS_LABELS["H2"] == "Stuck-on load"


def test_ids():
    assert c.SAT_IDS[0] == "SAT-01" and c.SAT_IDS[-1] == "SAT-24" and len(c.SAT_IDS) == 24
    for bad in ("SAT-00", "SAT-25", "SAT-7", "ISS"):
        with pytest.raises(ValidationError):
            contact(sat_id=bad)
    with pytest.raises(ValidationError):
        contact(station_id="SVALBARD-2")


def test_command_allowlist():
    with pytest.raises(ValidationError):
        c.Command(t_exec=T0, name="REBOOT", args={})
    for load in c.ESSENTIAL_LOADS:
        with pytest.raises(ValidationError):
            c.Command(t_exec=T0, name="LOAD_OFF", args={"load": load})
        c.Command(t_exec=T0, name="LOAD_ON", args={"load": load})
    with pytest.raises(ValidationError):
        c.Command(t_exec=T0, name="SET_PAYLOAD_DUTY", args={"duty": 1.2})
    with pytest.raises(ValidationError):
        c.Command(t_exec=T0, name="SET_HEATER_SETPOINT", args={"setpoint_c": 25})
    with pytest.raises(ValidationError):
        c.Command(t_exec=T0, name="SAFE_MODE", args={"x": 1})
    c.Command(t_exec=T0, name="SET_HEATER_SETPOINT", args={"setpoint_c": -5})
    c.Command(t_exec=T0, name="SAFE_MODE")


def test_plan_rules():
    plan()
    with pytest.raises(ValidationError):
        plan(commands=[c.Command(t_exec=T0 - 1, name="SAFE_MODE")])
    with pytest.raises(ValidationError):
        plan(uplink=contact(sat_id="SAT-08"))
    with pytest.raises(ValidationError):
        contact(los=T0)


def test_plan_hash_changes_with_any_command():
    p = plan()
    h = p.plan_hash
    assert len(h) == 64 and h == c.plan_hash(plan())
    assert plan(plan_id="P-2", created_by="human").plan_hash == h  # not part of the hash
    variants = [
        [c.Command(t_exec=T0 + 61, name="LOAD_OFF", args={"load": "AUX"})],
        [c.Command(t_exec=T0 + 60, name="LOAD_OFF", args={"load": "HEATER"})],
        [c.Command(t_exec=T0 + 60, name="LOAD_ON", args={"load": "AUX"})],
        p.commands + [c.Command(t_exec=T0 + 70, name="SAFE_MODE")],
        [],
    ]
    for cmds in variants:
        assert plan(commands=cmds).plan_hash != h
    assert plan(kind="diagnostic").plan_hash != h
    assert plan(uplink=contact(los=T0 + 601)).plan_hash != h


def test_int_and_float_args_hash_the_same():
    a = plan(commands=[c.Command(t_exec=T0 + 60, name="SET_PAYLOAD_DUTY", args={"duty": 1})])
    b = plan(commands=[c.Command(t_exec=T0 + 60.0, name="SET_PAYLOAD_DUTY", args={"duty": 1.0})])
    assert a.plan_hash == b.plan_hash


def test_frozen_and_strict_extra():
    p = plan()
    with pytest.raises(ValidationError):
        p.sat_id = "SAT-01"
    with pytest.raises(ValidationError):
        c.Alert(alert_id="AL-1", sat_id="SAT-07", severity="warning", time_to_critical_s=None,
                next_contact_aos=None, reason_key="soc_trend", extra=1)


def test_simresult_roundtrip():
    stats = dict(pass_rate=1.0, min_soc_p5=0.4, min_v_bus=7.1, t_bat_min_c=5.0, t_bat_max_c=25.0,
                 payload_uptime=0.8)
    r = c.SimResult(sim_id="S-1", plan_id="P-1", plan_hash=plan().plan_hash,
                    per_hyp={"H0": stats, "H2": stats}, plausible=["H0", "H2"], passes_all=True,
                    worst_margin_soc=0.1, payload_uptime_worst=0.8, n_commands=1)
    assert c.SimResult.model_validate_json(r.model_dump_json()) == r
    with pytest.raises(ValidationError):
        c.SimResult(**{**r.model_dump(), "plausible": ["H9"]})


def test_misc_models():
    c.RankResult(ordered=["P-1", "P-2"], status="infeasible")
    c.TriageEntry(sat_id="SAT-19", severity="critical", time_to_critical_s=3600.0, next_contact_aos=T0,
                  priority=1, status="safe_mode_recommended")
    c.ScheduleResult(assignments=[c.Assignment(plan_id="P-1", station_id="TROLL", start_t=T0, end_t=T0 + 30)],
                     unscheduled=["P-2"], solver="cpsat", optimal=True, solve_ms=12.5)
    c.RecorderEvent(ts=T0, run_id="demo", seq=0, kind="tool_call", payload={"tool": "simulate"})
    c.ExecutionMsg(plan_id="P-1", sat_id="SAT-12", status="auto_executed", t=T0, flight_rule="FR-1")
    c.RevealMsg(sat_id="SAT-07",
                truth=c.FaultSpec(kind="stuck_on_load", target="SAT-07", params={"load": "AUX"}, onset_t=T0),
                diagnosed=c.Diagnosis(H="H2", label="Stuck-on load", posterior=0.9),
                truth_soc=c.TruthSoc(t=[T0], soc=[0.7]))
    c.FaultSpec(kind="beta_season", target="FLEET", onset_t=T0)
    assert c.ApprovalRefused(reason_key="failed_verification", failing_hypotheses=["H3"]).refused is True
    with pytest.raises(ValidationError):
        c.SatPosition(lat_deg=0, lon_deg=0, alt_km=500, illum=1.5)


@pytest.mark.parametrize("text", [
    "SAT-07 needs plan P-3; sim S-12 shows margin {S-12.worst_margin_soc}.",
    "Approval AP-2 for alert AL-4 under FR-1; H2 is most likely (T10).",
    "Worst case {S-3.per_hyp.H2.min_soc_p5}.",
    "No numbers here.",
])
def test_narration_ok(text):
    assert c.narration_ok(text)


@pytest.mark.parametrize("text", [
    "SOC drops to 18% in 40 minutes.",
    "SAT-7 is low",
    "Margin is 0.12 on SAT-07",
    "{X-1.field} 5",
    "H12 is unknown",
])
def test_narration_rejects_bare_digits(text):
    assert not c.narration_ok(text)
    assert c.bare_digits(text)


def test_placeholders():
    assert c.placeholders("a {S-1.passes_all} b {P-2.n_commands}") == ["S-1.passes_all", "P-2.n_commands"]
