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
        scenario = SimpleNamespace(orbit=Orbit())
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
