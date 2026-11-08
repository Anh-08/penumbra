"""One provider switch keeps the API mock-backed until the real modules merge."""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from importlib import import_module
from typing import Any

from contracts import (
    HYPOTHESIS_LABELS,
    SAT_IDS,
    Alert,
    ApprovalRefused,
    FleetStateMsg,
)


class MockProvider:
    """Deterministic local data for UI and API development; all telemetry is simulated."""

    def __init__(self, t0: float | None = None):
        self.t0 = t0 if t0 is not None else datetime.now(timezone.utc).timestamp()
        self.scenario_name = "single-stuck-load"
        self.seed = 1
        self.executed: set[str] = set()

    @staticmethod
    def sun_direction(t: float) -> tuple[float, float, float]:
        angle = 2 * math.pi * (t % 86164) / 86164
        season = 2 * math.pi * t / (365.2422 * 86400)
        declination = math.radians(23.44) * math.sin(season)
        return (
            math.cos(declination) * math.cos(angle),
            math.cos(declination) * math.sin(angle),
            math.sin(declination),
        )

    def fleet_state(self, t: float, speed: float) -> FleetStateMsg:
        sun = self.sun_direction(t)
        sats = []
        for index, sat_id in enumerate(SAT_IDS):
            plane, slot = divmod(index, 4)
            orbit_angle = 2 * math.pi * (t / (5700 + 11 * plane) + slot / 4 + plane / 24)
            inclination = math.radians(97.2 + plane * .18)
            raan = plane * math.pi / 3
            x0, y0 = math.cos(orbit_angle), math.sin(orbit_angle) * math.cos(inclination)
            z = math.sin(orbit_angle) * math.sin(inclination)
            x1 = x0 * math.cos(raan) - y0 * math.sin(raan)
            y1 = x0 * math.sin(raan) + y0 * math.cos(raan)
            spin = 2 * math.pi * t / 86164
            x = x1 * math.cos(spin) + y1 * math.sin(spin)
            y = -x1 * math.sin(spin) + y1 * math.cos(spin)
            toward_sun = x * sun[0] + y * sun[1] + z * sun[2]
            radius = 6371 + 500 + 4 * plane
            shadow_axis = radius * math.sqrt(max(0.0, 1 - toward_sun * toward_sun))
            illum = 1.0 if toward_sun >= 0 else min(1.0, max(0.0, (shadow_axis - 6371 + 75) / 150))
            sats.append({
                "sat_id": sat_id,
                "lat_deg": math.degrees(math.asin(z)),
                "lon_deg": (math.degrees(math.atan2(y, x)) + 540) % 360 - 180,
                "alt_km": 500 + 4 * plane,
                "illum": illum,
                "soc": min(1.0, max(0.0, .74 + .08 * math.sin(index * 1.7 + t / 4600))),
                "status": "ECLIPSE" if illum < .05 else "PENUMBRA" if illum < .95 else "NOMINAL",
            })
        return FleetStateMsg.model_validate({
            "t": t, "speed": speed, "sun_direction": sun, "sats": sats,
        })

    def set_scenario(self, name: str, seed: int) -> None:
        self.scenario_name = name
        self.seed = seed
        self.executed.clear()

    def storm_alerts(self, t: float) -> list[Alert]:
        return [
            Alert(alert_id="AL-1", sat_id="SAT-12", severity="warning", time_to_critical_s=5400,
                  next_contact_aos=t + 400, reason_key="array_power_low"),
            Alert(alert_id="AL-2", sat_id="SAT-07", severity="critical", time_to_critical_s=2500,
                  next_contact_aos=t + 700, reason_key="unexpected_load"),
            Alert(alert_id="AL-3", sat_id="SAT-19", severity="critical", time_to_critical_s=1800,
                  next_contact_aos=t + 900, reason_key="soc_declining"),
        ]

    def approve(self, approval_id: str, plan_hash: str) -> ApprovalRefused:
        # Mock plans have no verified SimResult. Physics veto remains in force.
        return ApprovalRefused(reason_key="no_verified_plan", failing_hypotheses=["H2"])

    def reveal(self, sat_id: str) -> dict[str, Any] | None:
        # Truth is inaccessible before an execution. A real world adapter can supply it later.
        if sat_id not in self.executed:
            return None
        return self.sample_messages()["reveal"]

    def sample_messages(self) -> dict[str, Any]:
        """Representative, contract-valid payloads for schema tests and mocked events."""
        t = self.t0
        result = {
            "sim_id": "S-1", "plan_id": "P-1", "plan_hash": "sample-hash",
            "per_hyp": {"H2": {
                "pass_rate": .83, "min_soc_p5": .28, "min_v_bus": 27.2,
                "t_bat_min_c": 4.0, "t_bat_max_c": 18.0, "payload_uptime": .7,
            }},
            "plausible": ["H2"], "passes_all": False, "worst_margin_soc": -.02,
            "payload_uptime_worst": .7, "n_commands": 1,
        }
        return {
            "fleet_state": self.fleet_state(t, 120).model_dump(mode="json"),
            "demo_state": {"state": "attract", "speed": 120},
            "alert": self.storm_alerts(t)[0].model_dump(mode="json"),
            "callout": {"text": "SAT-12 array power is low", "priority": 2},
            "trace_event": {"ts": t, "run_id": "mock", "seq": 1, "kind": "tool_call", "payload": {"tool": "get_fleet_status"}},
            "hypotheses": {"sat_id": "SAT-07", "scores": {"H2": .78},
                           "labels": HYPOTHESIS_LABELS, "plausible": ["H2"]},
            "matrix": {"sat_id": "SAT-07", "results": [result], "complete": False},
            "trajectory": {"t": [t, t + 10], "soc_p5": [.3, .29], "soc_p50": [.4, .39], "soc_p95": [.5, .49]},
            "schedule": {"assignments": [{"plan_id": "P-1", "station_id": "SVALBARD",
                                           "start_t": t + 300, "end_t": t + 340}],
                         "unscheduled": [], "solver": "greedy", "optimal": False, "solve_ms": 1.2},
            "approval_request": {"approval_id": "AP-1", "plan_id": "P-1", "plan_hash": "sample-hash",
                                 "sat_id": "SAT-07", "tier": 2, "sim_id": "S-1"},
            "approval_refused": {"approval_id": "AP-1", "reason_key": "failed_verification",
                                 "failing_hypotheses": ["H2"]},
            "execution": {"plan_id": "P-1", "sat_id": "SAT-07", "status": "executed", "t": t},
            "reveal": {"sat_id": "SAT-07",
                       "truth": {"kind": "stuck_on_load", "target": "SAT-07", "params": {"load": "AUX"}, "onset_t": t},
                       "diagnosed": {"H": "H2", "label": "Stuck-on load", "posterior": .78},
                       "truth_soc": {"t": [t, t + 10], "soc": [.5, .49]}},
        }


def select_provider() -> MockProvider | Any:
    """FleetBackend is the production feed; the mock remains an explicit test option."""
    mode = os.getenv("PENUMBRA_API_PROVIDER", "real").lower()
    if mode == "mock":
        return MockProvider()
    if mode == "real":
        module = import_module("backend.api.real_provider")
        return module.RealProvider()
    raise ValueError(f"unknown PENUMBRA_API_PROVIDER={mode!r}")
