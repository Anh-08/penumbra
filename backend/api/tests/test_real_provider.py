import json
from math import nan
from types import SimpleNamespace

from contracts import SAT_IDS
from backend.api.real_provider import fleet_state_from_backend


def test_fleet_state_uses_real_stack_orbit_latest_downlink_and_triage():
    class Orbit:
        def positions(self, t):
            return {sat_id: {"lat_deg": 1, "lon_deg": 2, "alt_km": 500, "illum": .5}
                    for sat_id in SAT_IDS}

        def sun_direction(self, t):
            return (1, 0, 0)

    class World:
        def latest(self, sat_id):
            return {"soc": .42}

    class Backend:
        scenario = SimpleNamespace(orbit=Orbit(), initial=SimpleNamespace(soc=[.75] * 24))
        world = World()

        def __init__(self):
            self.advanced = None

        def advance_to(self, t):
            self.advanced = t

        def triage(self):
            return [{"sat_id": "SAT-07", "status": "awaiting_approval"}]

    backend = Backend()
    state = fleet_state_from_backend(backend, 1_800_000_000.0, 600)
    assert backend.advanced == 1_800_000_000.0
    assert len(state.sats) == 24
    assert state.sats[6].sat_id == "SAT-07"
    assert state.sats[6].soc == .42
    assert state.sats[6].status == "awaiting_approval"
    assert state.sun_direction == (1, 0, 0)


def test_fleet_state_uses_simulated_initial_soc_before_first_downlink_so_json_is_valid():
    class Orbit:
        def positions(self, t):
            return {sat_id: {"lat_deg": 1, "lon_deg": 2, "alt_km": 500, "illum": 1}
                    for sat_id in SAT_IDS}

        def sun_direction(self, t):
            return (1, 0, 0)

    backend = SimpleNamespace(
        advance_to=lambda t: None,
        scenario=SimpleNamespace(orbit=Orbit(), initial=SimpleNamespace(soc=[.75] * 24)),
        world=SimpleNamespace(latest=lambda sat_id: {"soc": nan}),
        triage=lambda: [],
    )
    state = fleet_state_from_backend(backend, 1_800_000_000.0, 120)
    assert state.sats[0].soc == .75
    json.dumps(state.model_dump(mode="json"), allow_nan=False)
