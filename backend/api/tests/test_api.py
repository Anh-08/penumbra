import json
import time
from threading import Event

from fastapi.testclient import TestClient
from pydantic import ValidationError

from contracts import ExecutionMsg, WS_MESSAGE_TYPES
from backend.api.app import create_app
from backend.api.clock import SimClock
from backend.api.messages import WS_SCHEMAS, validate_message
from backend.api.providers import MockProvider, select_provider
from backend.api.real_provider import RealProvider


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


def test_trace_telemetry_nonfinite_channels_become_json_null():
    sample = MockProvider(t0=1_800_000_000.0).sample_messages()['trace_event']
    sample['kind'] = 'tool_result'
    sample['payload'] = {'name': 'get_telemetry', 'result': {
        'sat_id': 'SAT-07', 't': [100, 110], 'channels': {'soc': [.48, float('nan')]},
    }}
    message = validate_message('trace_event', sample)
    assert message['data']['payload']['result']['channels']['soc'] == [.48, None]
    json.dumps(message, allow_nan=False)


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


def test_approval_and_reveal_publish_contract_messages():
    class ExecutingProvider(MockProvider):
        def approve(self, approval_id, plan_hash):
            self.executed.add('SAT-07')
            return ExecutionMsg(plan_id='P-1', sat_id='SAT-07', status='executed', t=self.t0)

    with TestClient(create_app(provider=ExecutingProvider(t0=1_800_000_000.0))) as api:
        with api.websocket_connect('/ws') as ws:
            assert ws.receive_json()['type'] == 'fleet_state'
            response = api.post('/api/approve', json={'approval_id': 'AP-1', 'plan_hash': 'verified-hash'})
            assert response.status_code == 200
            assert response.json()['status'] == 'executed'
            assert ws.receive_json()['type'] == 'execution'
            revealed = api.post('/api/reveal', json={'sat_id': 'SAT-07'})
            assert revealed.status_code == 200
            assert revealed.json()['truth']['kind'] == 'stuck_on_load'
            assert ws.receive_json()['type'] == 'reveal'


def test_slow_scenario_build_returns_without_blocking_api_startup():
    release = Event()

    class SlowProvider(MockProvider):
        background_scenario = True

        def set_scenario(self, name, seed):
            assert release.wait(2)
            super().set_scenario(name, seed)

    with TestClient(create_app(provider=SlowProvider(t0=1_800_000_000.0))) as api:
        start = time.monotonic()
        response = api.post('/api/scenario', json={'name': 'fault-storm', 'seed': 7})
        assert response.json()['status'] == 'building'
        assert time.monotonic() - start < .5
        assert api.post('/api/fault-storm').status_code == 503
        release.set()
        for _ in range(20):
            if api.app.state.scenario_task.done():
                break
            time.sleep(.01)
        assert api.app.state.scenario_task.done()
        assert api.post('/api/fault-storm').status_code == 200


def test_complete_matrix_emits_one_operator_ticket_per_simulated_plan():
    class TicketProvider(MockProvider):
        trajectory_calls = []

        def operator_ticket(self, plan_id):
            return {
                'approval_id': f'AP-{plan_id[2:]}', 'plan_id': plan_id,
                'plan_hash': 'sample-hash', 'sat_id': 'SAT-07', 'tier': 2, 'sim_id': 'S-1',
            }

        def trajectory(self, plan_id, hypothesis):
            self.trajectory_calls.append((plan_id, hypothesis))
            return self.sample_messages()['trajectory']

    app = create_app(provider=TicketProvider(t0=1_800_000_000.0))
    with TestClient(app) as api:
        with api.websocket_connect('/ws') as ws:
            assert ws.receive_json()['type'] == 'fleet_state'
            matrix = api.app.state.provider.sample_messages()['matrix']
            first = matrix['results'][0]
            first['per_hyp']['H3'] = {**first['per_hyp']['H2'], 'min_soc_p5': .11}
            first['plausible'].append('H3')
            matrix['complete'] = True
            api.app.state.publish('matrix', matrix)
            assert ws.receive_json()['type'] == 'matrix'
            ticket = ws.receive_json()
            assert ticket['type'] == 'approval_request'
            assert ticket['data']['plan_id'] == 'P-1'
            assert ws.receive_json()['type'] == 'trajectory'
            assert api.app.state.provider.trajectory_calls == [('P-1', 'H3')]


def test_real_detector_alert_and_callout_use_its_messages_without_duplicates():
    class DetectorProvider(MockProvider):
        def storm_alerts(self, t):
            self.pending = [
                {'type': 'alert', 'data': self.storm_alerts_sample(t)},
                {'type': 'callout', 'data': {'text': 'SAT-07 requires attention', 'priority': 2}},
            ]
            return []

        def storm_alerts_sample(self, t):
            return MockProvider.storm_alerts(self, t)[0].model_dump(mode='json')

        def drain_messages(self):
            events = getattr(self, 'pending', [])
            self.pending = []
            return events

    app = create_app(provider=DetectorProvider(t0=1_800_000_000.0))
    with TestClient(app) as api:
        with api.websocket_connect('/ws') as ws:
            assert ws.receive_json()['type'] == 'fleet_state'
            response = api.post('/api/fault-storm')
            assert response.json()['alert_count'] == 1
            assert [ws.receive_json()['type'], ws.receive_json()['type']] == ['alert', 'callout']


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


def test_provider_import_switch_defaults_to_real_and_keeps_explicit_mock(monkeypatch):
    monkeypatch.delenv('PENUMBRA_API_PROVIDER', raising=False)
    assert isinstance(select_provider(), RealProvider)
    monkeypatch.setenv('PENUMBRA_API_PROVIDER', 'mock')
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
