"""CP-SAT uplink scheduling with a deterministic greedy fallback."""

from collections import defaultdict
import logging
import math
import time
from typing import Mapping, Sequence

from contracts import Assignment, Contact, ScheduleRequest, ScheduleResult

try:
    from ortools.sat.python import cp_model
except ImportError:  # A small offline deployment can still use the fallback.
    cp_model = None


LOGGER = logging.getLogger(__name__)
PRIORITY_SCALE = 1000


class UplinkScheduler:
    """Assign one contact slot per plan without overlapping station uplinks."""

    def __init__(self, contacts: Sequence[Contact], *, time_limit_s: float = 7.0) -> None:
        if not 5.0 <= time_limit_s <= 10.0:
            raise ValueError("CP-SAT time limit must be between 5 and 10 seconds")
        self.contacts = list(contacts)
        self.time_limit_s = time_limit_s
        self._by_sat = defaultdict(list)
        for contact in self.contacts:
            self._by_sat[contact.sat_id].append(contact)

    @staticmethod
    def _requests(requests: Sequence[ScheduleRequest | Mapping]) -> list[ScheduleRequest]:
        parsed = [
            request if isinstance(request, ScheduleRequest) else ScheduleRequest.model_validate(request)
            for request in requests
        ]
        if len({request.plan_id for request in parsed}) != len(parsed):
            raise ValueError("plan_id values must be unique")
        for request in parsed:
            if (
                not math.isfinite(request.priority)
                or request.priority < 0
                or not math.isfinite(request.deadline_t)
                or not math.isfinite(request.duration_s)
                or request.duration_s <= 0
            ):
                raise ValueError("priority, deadline, and duration must be finite and valid")
        return parsed

    @staticmethod
    def _bounds(request: ScheduleRequest, contact: Contact) -> tuple[int, int, int] | None:
        duration = math.ceil(request.duration_s)
        earliest = math.ceil(contact.aos)
        latest = math.floor(min(contact.los, request.deadline_t)) - duration
        return (earliest, latest, duration) if earliest <= latest else None

    def schedule(self, requests: Sequence[ScheduleRequest | Mapping]) -> ScheduleResult:
        """Maximize scheduled priority, respecting deadlines and station use."""
        start_time = time.perf_counter()
        parsed = self._requests(requests)
        if cp_model is not None:
            result = self._cpsat(parsed, start_time)
            if result is not None:
                return result
        return self._greedy(parsed, start_time)

    def _finish(
        self,
        requests: list[ScheduleRequest],
        assignments: list[Assignment],
        solver: str,
        optimal: bool,
        start_time: float,
    ) -> ScheduleResult:
        assigned = {assignment.plan_id for assignment in assignments}
        assignments.sort(key=lambda item: (item.start_t, item.station_id, item.plan_id))
        result = ScheduleResult(
            assignments=assignments,
            unscheduled=[request.plan_id for request in requests if request.plan_id not in assigned],
            solver=solver,
            optimal=optimal,
            solve_ms=(time.perf_counter() - start_time) * 1000.0,
        )
        LOGGER.info(
            "Uplink schedule: requests=%d stations=%d solver=%s optimal=%s solve_ms=%.1f",
            len(requests), len({contact.station_id for contact in self.contacts}),
            solver, optimal, result.solve_ms,
        )
        return result

    def _cpsat(self, requests: list[ScheduleRequest], start_time: float) -> ScheduleResult | None:
        model = cp_model.CpModel()
        by_station = defaultdict(list)
        candidates = []
        objective = []
        for request in requests:
            choices = []
            weight = max(1, round(request.priority * PRIORITY_SCALE))
            for index, contact in enumerate(self._by_sat[request.sat_id]):
                bounds = self._bounds(request, contact)
                if bounds is None:
                    continue
                earliest, latest, duration = bounds
                name = f"{request.plan_id}_{index}"
                present = model.NewBoolVar(f"use_{name}")
                start = model.NewIntVar(earliest, latest, f"start_{name}")
                end = model.NewIntVar(earliest + duration, latest + duration, f"end_{name}")
                interval = model.NewOptionalIntervalVar(start, duration, end, present, f"slot_{name}")
                by_station[contact.station_id].append(interval)
                choices.append(present)
                candidates.append((request, contact, present, start, duration))
                objective.append(weight * present)
            model.Add(sum(choices) <= 1)
        for intervals in by_station.values():
            model.AddNoOverlap(intervals)
        model.Maximize(sum(objective))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_s
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        assignments = [
            Assignment(
                plan_id=request.plan_id,
                station_id=contact.station_id,
                start_t=float(solver.Value(start)),
                end_t=float(solver.Value(start) + duration),
            )
            for request, contact, present, start, duration in candidates
            if solver.Value(present)
        ]
        return self._finish(requests, assignments, "cpsat", status == cp_model.OPTIMAL, start_time)

    def _greedy(self, requests: list[ScheduleRequest], start_time: float) -> ScheduleResult:
        by_station = defaultdict(list)
        assignments = []
        for request in sorted(requests, key=lambda item: (-item.priority, item.deadline_t, item.plan_id)):
            options = []
            for contact in self._by_sat[request.sat_id]:
                bounds = self._bounds(request, contact)
                if bounds is None:
                    continue
                earliest, latest, duration = bounds
                slot = earliest
                for busy in sorted(by_station[contact.station_id], key=lambda item: item.start_t):
                    if slot + duration <= busy.start_t:
                        break
                    if slot < busy.end_t:
                        slot = int(busy.end_t)
                if slot <= latest:
                    options.append((slot, contact.station_id, duration))
            if not options:
                continue
            slot, station_id, duration = min(options)
            assignment = Assignment(
                plan_id=request.plan_id,
                station_id=station_id,
                start_t=float(slot),
                end_t=float(slot + duration),
            )
            by_station[station_id].append(assignment)
            assignments.append(assignment)
        return self._finish(requests, assignments, "greedy", False, start_time)
