"""Triage (CONTRACTS.md): which satellite needs the Flight Director first.

Order: severity (critical, warning, watch), then slack = time to critical minus the time until the next
contact (how long is left after the next chance to uplink a fix), then sat id. A satellite with no
crossing has infinite slack. priority is the 1-based rank. status comes from the backend's bookkeeping.
"""
from __future__ import annotations

import math

from contracts import Alert, TriageEntry

from .detect import SEVERITY_RANK


def slack_s(alert: Alert, now: float) -> float:
    if alert.time_to_critical_s is None:
        return math.inf
    wait = max(0.0, alert.next_contact_aos - now) if alert.next_contact_aos is not None else math.inf
    return alert.time_to_critical_s - wait


def triage(alerts: list[Alert], statuses: dict[str, str], now: float) -> list[TriageEntry]:
    ranked = sorted(alerts, key=lambda a: (-SEVERITY_RANK[a.severity], slack_s(a, now), a.sat_id))
    return [TriageEntry(sat_id=a.sat_id, severity=a.severity, time_to_critical_s=a.time_to_critical_s,
                        next_contact_aos=a.next_contact_aos, priority=float(i + 1),
                        status=statuses.get(a.sat_id, "queued"))
            for i, a in enumerate(ranked)]
