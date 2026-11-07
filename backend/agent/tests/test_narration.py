"""T10: narration filter (digits only in placeholders and allowed ids) and placeholder renderer."""
import pytest

import contracts as c
from backend.agent.dispatcher import Dispatcher
from backend.agent.mock_backend import MOCK_SAT, MockBackend
from backend.agent.narration import format_value, narration_problems, render, safe_text

SHED = [{"name": "SET_PAYLOAD_DUTY", "args": {"duty": 0.2}}, {"name": "LOAD_OFF", "args": {"load": "AUX"}}]


@pytest.fixture
def d():
    d = Dispatcher(MockBackend())
    d.dispatch("get_fleet_status", {})  # registers AL-1
    pid = d.dispatch("propose_plan", {"sat_id": MOCK_SAT, "commands": SHED}).result["plan_id"]  # P-1
    d.dispatch("simulate", {"plan_id": pid})  # S-1
    d.dispatch("request_approval", {"plan_id": pid})  # AP-1
    return d


def test_clean_narration_passes_and_renders(d):
    text = ("SAT-07 likely has H2. P-1 passes with worst margin {S-1.worst_margin_soc}; "
            "time to critical was {AL-1.time_to_critical_s}. Approval AP-1 is pending.")
    assert narration_problems(text, d) == []
    out = render(text, d)
    assert "{" not in out
    assert "+4.0 % SOC" in out
    assert "4 h 30 min" in out


@pytest.mark.parametrize(
    "placeholder, expected",
    [
        ("{S-1.passes_all}", "yes"),
        ("{S-1.per_hyp.H2.min_soc_p5}", "24.0 %"),
        ("{S-1.per_hyp.H2.t_bat_max_c}", "24.0 °C"),
        ("{P-1.sat_id}", "SAT-07"),
        ("{P-1.commands.0.name}", "SET_PAYLOAD_DUTY"),
        ("{P-1.uplink.station_id}", "SVALBARD"),
        ("{P-1.uplink.aos}", "18:10 UTC"),
        ("{AP-1.tier}", "2"),
        ("{AL-1.severity}", "warning"),
        ("{S-1.plausible}", "H1, H2, H3"),
    ],
)
def test_placeholder_rendering(d, placeholder, expected):
    assert narration_problems(placeholder, d) == []
    assert render(placeholder, d) == expected


@pytest.mark.parametrize(
    "text, problem",
    [
        ("Plan P-80 is best.", "unknown id"),  # number smuggled as a plan id
        ("SAT-99 is draining.", "unknown id"),
        ("H7 is most likely.", "unknown id"),
        ("Approval AP-5 is pending.", "unknown id"),
        ("Margin {S-9.worst_margin_soc}.", "placeholder"),
        ("Margin {S-1.no_such_field}.", "placeholder"),
        ("Stats {S-1.per_hyp}.", "placeholder"),  # not a single value
        ("Satellite {SAT-07.soc}.", "placeholder"),  # SAT ids are not placeholder objects
    ],
)
def test_narration_rejects_unknown_ids_and_bad_placeholders(d, text, problem):
    problems = narration_problems(text, d)
    assert any(problem in p for p in problems), problems


def test_unicode_digits_are_digits(d):
    assert narration_problems("Margin is ٤ percent.", d)
    assert narration_problems("Margin is ４ percent.", d)


def test_safe_text(d):
    assert safe_text("P-1 passes with {S-1.worst_margin_soc}.", d) == "P-1 passes with +4.0 % SOC."
    assert safe_text("P-1 passes with 4 percent.", d) is None
    assert safe_text(None, d) is None


def test_rendered_values_are_the_backend_values(d):
    sim = d.sim_results["S-1"]
    assert render("{S-1.worst_margin_soc}", d) == format_value("worst_margin_soc", sim.worst_margin_soc)


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("worst_margin_soc", -0.04, "-4.0 % SOC"),
        ("soc", 0.623, "62.3 %"),
        ("pass_rate", 1.0, "100.0 %"),
        ("time_to_critical_s", 600.0, "10 min"),
        ("time_to_critical_s", 45.0, "45 s"),
        ("time_to_critical_s", None, "n/a"),
        ("max_el_deg", 42.0, "42°"),
        ("min_v_bus", 7.42, "7.42 V"),
        ("n_commands", 2, "2"),
        ("latency_ms", 812.4, "812 ms"),
        ("passes_all", False, "no"),
    ],
)
def test_format_value(field, value, expected):
    assert format_value(field, value) == expected


def test_contract_checker_agrees_on_digits(d):
    for text in ["P-1 is fine.", "Use 3 commands.", "{S-1.worst_margin_soc} margin"]:
        assert (c.narration_ok(text)) == (not any(p.startswith("bare digits") for p in narration_problems(text, d)))
