import json
from math import nan
from threading import RLock
from types import SimpleNamespace

from contracts import ApprovalRefused, SAT_IDS
from backend.api.real_provider import RealProvider, fleet_state_from_backend


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


def test_fleet_state_marks_soc_unknown_before_first_downlink_so_json_is_valid():
    class Orbit:
        def positions(self, t):
            return {sat_id: {"lat_deg": 1, "lon_deg": 2, "alt_km": 500, "illum": 1}
                    for sat_id in SAT_IDS}

        def sun_direction(self, t):
            return (1, 0, 0)

    backend = SimpleNamespace(
        advance_to=lambda t: None,
        scenario=SimpleNamespace(orbit=Orbit()),
        world=SimpleNamespace(latest=lambda sat_id: {"soc": nan}),
        triage=lambda: [],
    )
    state = fleet_state_from_backend(backend, 1_800_000_000.0, 120)
    assert state.sats[0].soc is None
    json.dumps(state.model_dump(mode="json"), allow_nan=False)


def test_real_provider_uses_dispatcher_approval_then_execution_and_gates_reveal():
    calls = []

    class Dispatcher:
        def operator_ticket(self, plan_id):
            return {'plan_id': plan_id, 'approval_id': 'AP-1'}

        def approve(self, approval_id, plan_hash):
            calls.append(('approve', approval_id, plan_hash))
            return SimpleNamespace(plan_id='P-1', sat_id='SAT-07')

        def execute(self, approval_id):
            calls.append(('execute', approval_id))

    provider = RealProvider.__new__(RealProvider)
    provider._lock = RLock()
    provider.dispatcher = Dispatcher()
    provider.backend = SimpleNamespace(now=lambda: 1_800_000_000.0)
    provider.executed = set()
    assert provider.reveal('SAT-07') is None
    assert provider.operator_ticket('P-1')['approval_id'] == 'AP-1'
    result = provider.approve('AP-1', 'verified-hash')
    assert result.status == 'executed'
    assert calls == [('approve', 'AP-1', 'verified-hash'), ('execute', 'AP-1')]
    assert 'SAT-07' in provider.executed


def test_real_provider_preserves_dispatcher_physics_refusal():
    class Dispatcher:
        def approve(self, approval_id, plan_hash):
            return ApprovalRefused(reason_key='failed_verification', failing_hypotheses=['H2'])

        def execute(self, approval_id):
            raise AssertionError('failed plan executed')

    provider = RealProvider.__new__(RealProvider)
    provider._lock = RLock()
    provider.dispatcher = Dispatcher()
    provider.executed = set()
    result = provider.approve('AP-2', 'red-hash')
    assert result.refused is True
    assert result.failing_hypotheses == ['H2']
    assert provider.executed == set()
