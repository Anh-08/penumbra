import re
from pathlib import Path

import numpy as np
import pytest

from contracts import Alert, CalloutMsg, Contact
from backend.fleet import detect, scenarios
from backend.verify import constants as K

SATS = ("SAT-03", "SAT-09", "SAT-12")
ONSET = scenarios.T0 + 2 * 3600.0
STEP = 60.0


@pytest.fixture(scope="module")
def run():
    """single-stuck-load on three satellites (24 h horizon); detector polled every minute for 4.5 h."""
    sc = scenarios.build("single-stuck-load", seed=0, sat_ids=SATS)
    w = sc.world
    d = detect.Detector(sc.env, sc.contacts, w.telemetry, w.executed, sc.initial)
    msgs, active = [], {}
    for t in np.arange(sc.env.t[0], scenarios.T0 + 4.5 * 3600.0, STEP):
        w.advance_to(t)
        active[t] = d.detect(t)
        msgs += [(t, m) for m in d.drain_messages()]
    return sc, d, msgs, active


def first_pass_after(sc, sat, t):
    return min((c for c in sc.contacts if c.sat_id == sat and c.aos > t), key=lambda c: c.aos)


def test_alert_fires_at_first_contact_after_onset_and_not_before(run):
    sc, d, msgs, active = run
    alerts = [(t, m["data"]) for t, m in msgs if m["type"] == "alert"]
    assert [a.sat_id for _, a in alerts] == ["SAT-09"]
    t_alert, a = alerts[0]
    c = first_pass_after(sc, "SAT-09", ONSET)
    assert c.aos <= t_alert <= c.los + STEP
    # nothing at all (not even watch) before that pass
    assert all(not v for t, v in active.items() if t < c.aos)
    assert a.severity in ("warning", "critical")
    assert a.reason_key == "load_higher_than_commanded"


def test_one_alert_and_one_callout_with_rendered_text(run):
    sc, d, msgs, _ = run
    kinds = [m["type"] for _, m in msgs]
    assert kinds == ["alert", "callout"]
    alert, callout = msgs[0][1]["data"], msgs[1][1]["data"]
    assert isinstance(alert, Alert) and isinstance(callout, CalloutMsg)
    m = re.fullmatch(r"Flight, SAT-09 battery trending low, (\d+) minutes to critical", callout.text)
    assert m, callout.text
    assert int(m.group(1)) == round(alert.time_to_critical_s / 60.0)
    assert callout.priority in (1, 2)


def test_alert_fields(run):
    sc, d, msgs, _ = run
    t, m = msgs[0]
    a = m["data"]
    assert re.fullmatch(r"AL-\d+", a.alert_id)
    assert a.time_to_critical_s is not None and a.time_to_critical_s > 0
    assert a.next_contact_aos is not None and a.next_contact_aos + 600 >= t
    assert d.alert(a.alert_id).sat_id == "SAT-09"
    assert d.scores("SAT-09").top == "H2"


def test_healthy_satellites_stay_quiet(run):
    sc, d, msgs, active = run
    last = active[max(active)]
    assert {a.sat_id for a in last} == {"SAT-09"}
    for s in ("SAT-03", "SAT-12"):
        assert d.scores(s).top == "H0"


def test_time_to_critical_comes_from_the_worst_plausible_hypothesis(run):
    sc, d, msgs, _ = run
    t = sc.world.t_now
    tm = sc.world.telemetry("SAT-09")
    score = d.scores("SAT-09")
    cross = d._crossings("SAT-09", t, tm, score)
    assert "H2" in cross
    assert d.time_to_critical("SAT-09", t, tm, score) == pytest.approx(max(0.0, min(cross.values()) - t))


def test_detector_needs_only_telemetry(run):
    """Same alerts when fed recorded telemetry and the sent commands through plain functions: no world,
    no fault specs."""
    sc, _, msgs, _ = run
    recorded = {s: sc.world.telemetry(s) for s in SATS}
    sent = {s: sc.world.executed(s) for s in SATS}
    now = {"t": sc.env.t[0]}

    def telemetry(sat):
        tm = recorded[sat]
        sel = tm["t"] <= now["t"]
        return {k: v[sel] for k, v in tm.items()}

    d = detect.Detector(sc.env, sc.contacts, telemetry, lambda s: sent[s], sc.initial)
    out = []
    for t in np.arange(sc.env.t[0], scenarios.T0 + 4.5 * 3600.0, STEP):
        now["t"] = t
        d.detect(t)
        out += [(t, m) for m in d.drain_messages()]
    assert [(t, m["type"], m["data"].sat_id if m["type"] == "alert" else m["data"].text) for t, m in out] == \
           [(t, m["type"], m["data"].sat_id if m["type"] == "alert" else m["data"].text) for t, m in msgs]


def test_detector_source_never_touches_fault_specs():
    src = (Path(detect.__file__)).read_text(encoding="utf-8")
    for word in ("FaultSpec", "reveal", "EpsWorld", "faults", "scenarios"):
        assert word not in src, word


def test_severity_rules():
    d = detect.Detector.__new__(detect.Detector)
    t = 1000.0
    c = Contact(sat_id="SAT-01", station_id="TROLL", aos=t + 3600 * 5, los=t + 3600 * 5 + 600, max_el_deg=30)
    assert d._severity(None, False, t, c) is None
    assert d._severity(None, True, t, c) == "watch"
    assert d._severity(K.CRITICAL_TTC_S - 1, True, t, c) == "critical"
    assert d._severity(3600 * 5 + 100, True, t, c) == "critical"         # runs out before we can uplink
    assert d._severity(3600 * 8, True, t, c) == "warning"


def test_render_placeholders(run):
    sc, d, msgs, _ = run
    a = d.alert(msgs[0][1]["data"].alert_id)   # current numbers: the alert refreshes on every re-score
    assert d.render(f"{{{a.alert_id}.time_to_critical_min}}") == str(round(a.time_to_critical_s / 60))
    assert d.render("{AL-999.time_to_critical_min}") == "unknown"


def test_scenario_builds_full_fleet():
    sc = scenarios.build("single-stuck-load", seed=1, hours=2.0)
    assert len(sc.env.sat_ids) == 24 and sc.focus_sat == "SAT-09"
    assert all(c.los > c.aos for c in sc.contacts)
    assert sc.next_contact("SAT-09", scenarios.T0) is not None
    with pytest.raises(ValueError):
        scenarios.build("no-such-scenario")
