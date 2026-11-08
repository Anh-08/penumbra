"""Fleet-backed API adapter. The factory and approval methods are wired after owner handoff."""
from __future__ import annotations

from typing import Any

from contracts import FleetStateMsg, SAT_IDS


def fleet_state_from_backend(backend: Any, t: float, speed: float) -> FleetStateMsg:
    """Advance the real stack before collecting orbit, downlink, and triage state."""
    backend.advance_to(t)
    orbit = backend.scenario.orbit
    positions = orbit.positions(t)
    statuses = {row["sat_id"]: row["status"] for row in backend.triage()}
    sats = []
    for sat_id in SAT_IDS:
        position = positions[sat_id]
        sats.append({
            "sat_id": sat_id,
            "lat_deg": position["lat_deg"],
            "lon_deg": position["lon_deg"],
            "alt_km": position["alt_km"],
            "illum": position["illum"],
            "soc": backend.world.latest(sat_id)["soc"],
            "status": statuses.get(sat_id, "nominal"),
        })
    return FleetStateMsg.model_validate({
        "t": t, "speed": speed,
        "sun_direction": orbit.sun_direction(t),
        "sats": sats,
    })
