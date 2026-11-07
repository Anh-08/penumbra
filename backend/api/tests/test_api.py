import time

from fastapi.testclient import TestClient
from pydantic import ValidationError

from contracts import WS_MESSAGE_TYPES
from backend.api.app import create_app
from backend.api.clock import SimClock
from backend.api.messages import WS_SCHEMAS, validate_message
from backend.api.providers import MockProvider, select_provider


def client():
    return TestClient(create_app(provider=MockProvider(t0=1_800_000_000.0)))


def test_every_websocket_message_has_a_contract_schema():
    assert set(WS_SCHEMAS) == set(WS_MESSAGE_TYPES)
    samples = MockProvider(t0=1_800_000_000.0).sample_messages()
    assert set(samples) == set(WS_MESSAGE_TYPES)
    for kind, payload in samples.items():
        envelope = validate_message(kind, payload)
        assert envelope['type'] == kind
        assert envelope['data'] == WS_SCHEMAS[kind].model_validate(payload).model_dump(mode='json')
        try:
            validate_message(kind, {'unexpected': 'shape'})
        except ValidationError:
            pass
        else:
            raise AssertionError(f'{kind} accepted an invalid payload')


def test_all_rest_routes_exist_and_validate_request_bodies():
    with client() as api:
        schema = api.get('/openapi.json').json()
        for path, method in (
            ('/api/scenario', 'post'), ('/api/fault-storm', 'post'),
            ('/api/demo', 'post'), ('/api/approve', 'post'),
            ('/api/clock', 'post'), ('/api/bench', 'get'),
            ('/api/reveal', 'post'),
        ):
            assert method in schema['paths'][path]
        assert api.post('/api/scenario', json={'name': 'fault-storm', 'seed': 7}).status_code == 200
        assert api.post('/api/scenario', json={'name': 'invalid', 'seed': 7}).status_code == 422
        assert api.post('/api/fault-storm').status_code == 200
        assert api.post('/api/demo', json={'state': 'agent'}).json()['state'] == 'agent'
        assert api.post('/api/demo', json={'state': 'invalid'}).status_code == 422
        assert api.post('/api/approve', json={'approval_id': 'AP-1', 'plan_hash': 'bad'}).json()['refused'] is True
        assert api.post('/api/approve', json={'approval_id': 'invalid'}).status_code == 422
        assert api.get('/api/bench').status_code == 200
        assert api.post('/api/reveal', json={'sat_id': 'SAT-07'}).status_code == 409
        assert api.post('/api/reveal', json={'sat_id': 'invalid'}).status_code == 422


def test_clock_runs_at_requested_speed_and_rejects_out_of_range():
    with client() as api:
        assert api.post('/api/clock', json={'speed': 1}).json()['speed'] == 1
        assert api.post('/api/clock', json={'speed': 600}).json()['speed'] == 600
        assert api.post('/api/clock', json={'speed': 0}).status_code == 422
        assert api.post('/api/clock', json={'speed': 601}).status_code == 422


def test_websocket_streams_fleet_state_at_one_to_two_hertz():
    with client() as api:
        with api.websocket_connect('/ws') as ws:
            start = time.monotonic()
            first = ws.receive_json()
            second = ws.receive_json()
            elapsed = time.monotonic() - start
            assert first['type'] == second['type'] == 'fleet_state'
            assert len(first['data']['sats']) == 24
            assert 0.45 <= elapsed <= 1.1
            assert second['data']['t'] > first['data']['t']


def test_provider_import_switch_defaults_to_mock(monkeypatch):
    monkeypatch.delenv('PENUMBRA_API_PROVIDER', raising=False)
    assert isinstance(select_provider(), MockProvider)
    monkeypatch.setenv('PENUMBRA_API_PROVIDER', 'unknown')
    try:
        select_provider()
    except ValueError:
        pass
    else:
        raise AssertionError('unknown provider was accepted')


def test_clock_preserves_sim_time_when_speed_changes(monkeypatch):
    wall = [100.0]
    monkeypatch.setattr('backend.api.clock.time.monotonic', lambda: wall[0])
    clock = SimClock(1_800_000_000.0, speed=1)
    wall[0] += 10
    assert clock.now() == 1_800_000_010.0
    clock.set_speed(600)
    wall[0] += 2
    assert clock.now() == 1_800_001_210.0
    clock.set_speed(0)
    wall[0] += 20
    assert clock.now() == 1_800_001_210.0


def test_fault_storm_broadcasts_contract_valid_alerts_and_callouts():
    with client() as api:
        with api.websocket_connect('/ws') as ws:
            assert ws.receive_json()['type'] == 'fleet_state'
            assert api.post('/api/fault-storm').json()['alert_count'] == 3
            events = [ws.receive_json() for _ in range(6)]
            assert [event['type'] for event in events] == [
                'alert', 'callout', 'alert', 'callout', 'alert', 'callout',
            ]
            for event in events:
                validate_message(event['type'], event['data'])
