"""Uplink scheduler optimality, feasibility, and fallback tests."""

from itertools import permutations
import math

import pytest
from contracts import Contact, ScheduleRequest

from backend.sched import UplinkScheduler
import backend.sched.scheduler as scheduler_module


CONTACTS = [
    Contact(sat_id="SAT-01", station_id="SVALBARD", aos=0, los=120, max_el_deg=60),
    Contact(sat_id="SAT-02", station_id="SVALBARD", aos=0, los=120, max_el_deg=60),
    Contact(sat_id="SAT-03", station_id="SVALBARD", aos=0, los=120, max_el_deg=60),
    Contact(sat_id="SAT-04", station_id="FAIRBANKS", aos=0, los=120, max_el_deg=60),
    Contact(sat_id="SAT-05", station_id="FAIRBANKS", aos=30, los=150, max_el_deg=60),
    Contact(sat_id="SAT-06", station_id="TROLL", aos=0, los=120, max_el_deg=60),
]
REQUESTS = [
    ScheduleRequest(plan_id="P-1", sat_id="SAT-01", priority=10, deadline_t=120, duration_s=120),
    ScheduleRequest(plan_id="P-2", sat_id="SAT-02", priority=6, deadline_t=120, duration_s=60),
    ScheduleRequest(plan_id="P-3", sat_id="SAT-03", priority=6, deadline_t=120, duration_s=60),
    ScheduleRequest(plan_id="P-4", sat_id="SAT-04", priority=3, deadline_t=120, duration_s=120),
    ScheduleRequest(plan_id="P-5", sat_id="SAT-05", priority=4, deadline_t=150, duration_s=60),
    ScheduleRequest(plan_id="P-6", sat_id="SAT-06", priority=2, deadline_t=120, duration_s=60),
]


def _brute_force_score(requests, contacts):
    """Enumerate every ordered request subset for each independent station."""
    contact_by_sat = {contact.sat_id: contact for contact in contacts}
    total = 0
    for station_id in {contact.station_id for contact in contacts}:
        station_requests = [r for r in requests if contact_by_sat[r.sat_id].station_id == station_id]
        best = 0
        for count in range(len(station_requests) + 1):
            for ordered in permutations(station_requests, count):
                cursor = 0
                for request in ordered:
                    contact = contact_by_sat[request.sat_id]
                    start = max(cursor, math.ceil(contact.aos))
                    end = start + math.ceil(request.duration_s)
                    if end > min(math.floor(contact.los), math.floor(request.deadline_t)):
                        break
                    cursor = end
                else:
                    best = max(best, sum(request.priority for request in ordered))
        total += best
    return total


def _assert_feasible(result, requests, contacts):
    request_by_plan = {request.plan_id: request for request in requests}
    contacts_by_pair = {(contact.sat_id, contact.station_id): contact for contact in contacts}
    by_station = {}
    for assignment in result.assignments:
        request = request_by_plan[assignment.plan_id]
        contact = contacts_by_pair[request.sat_id, assignment.station_id]
        assert contact.aos <= assignment.start_t < assignment.end_t <= contact.los
        assert assignment.end_t <= request.deadline_t
        assert assignment.end_t - assignment.start_t >= request.duration_s
        by_station.setdefault(assignment.station_id, []).append(assignment)
    for assignments in by_station.values():
        ordered = sorted(assignments, key=lambda assignment: assignment.start_t)
        assert all(left.end_t <= right.start_t for left, right in zip(ordered, ordered[1:]))
    assert set(result.unscheduled) == set(request_by_plan) - {
        assignment.plan_id for assignment in result.assignments
    }


@pytest.mark.parametrize("count", range(1, 7))
def test_cpsat_objective_equals_brute_force_for_up_to_six_requests(count):
    requests = REQUESTS[:count]
    scheduler = UplinkScheduler(CONTACTS)
    result = scheduler.schedule(requests)
    _assert_feasible(result, requests, CONTACTS)

    assert result.solver == "cpsat"
    assert result.optimal is True
    assert 5 <= scheduler.time_limit_s <= 10
    assert result.solve_ms >= 0
    chosen = {assignment.plan_id for assignment in result.assignments}
    score = sum(request.priority for request in requests if request.plan_id in chosen)
    assert score == _brute_force_score(requests, CONTACTS)
    if count == 6:
        assert score == 18  # two medium uplinks beat the one long high-priority uplink


def test_greedy_fallback_respects_windows_and_deadlines(monkeypatch):
    monkeypatch.setattr(scheduler_module, "cp_model", None)
    result = UplinkScheduler(CONTACTS).schedule(REQUESTS)
    _assert_feasible(result, REQUESTS, CONTACTS)
    assert result.solver == "greedy"
    assert result.optimal is False
    assert result.solve_ms >= 0


def test_expired_dict_request_is_unscheduled():
    request = {
        "plan_id": "P-7", "sat_id": "SAT-01", "priority": 5,
        "deadline_t": 30, "duration_s": 60,
    }
    result = UplinkScheduler(CONTACTS).schedule([request])
    assert result.assignments == []
    assert result.unscheduled == ["P-7"]


def test_now_never_books_a_past_slot_in_cpsat_or_greedy(monkeypatch):
    request = ScheduleRequest(
        plan_id="P-8", sat_id="SAT-01", priority=5,
        deadline_t=120, duration_s=60,
    )
    scheduler = UplinkScheduler([CONTACTS[0]])
    for fallback in (False, True):
        if fallback:
            monkeypatch.setattr(scheduler_module, "cp_model", None)
        result = scheduler.schedule([request], now=50.25)
        assert len(result.assignments) == 1
        assert result.assignments[0].start_t >= 50.25
        assert result.assignments[0].end_t <= 120
        assert scheduler.schedule([request], now=60.25).unscheduled == ["P-8"]
