import assert from 'node:assert/strict';
import test from 'node:test';
import { createPanelState, formatCountdown, reducePanel } from './panelState.js';

test('triage preserves backend alert order and counts down from backend timestamps', () => {
  const first = {
    type: 'alert', data: { alert_id: 'AL-1', sat_id: 'SAT-07', severity: 'critical',
      time_to_critical_s: 7200, next_contact_aos: 1_000_252, reason_key: 'load' },
  };
  const second = {
    type: 'alert', data: { alert_id: 'AL-2', sat_id: 'SAT-19', severity: 'warning',
      time_to_critical_s: null, next_contact_aos: null, reason_key: 'battery' },
  };
  let state = reducePanel(createPanelState(), first);
  state = reducePanel(state, second);
  assert.deepEqual(state.alerts.map(({ sat_id }) => sat_id), ['SAT-07', 'SAT-19']);
  assert.equal(formatCountdown(state.alerts[0].next_contact_aos, 1_000_000), '04:12');
  assert.equal(formatCountdown(state.alerts[1].next_contact_aos, 1_000_000), '—');
  assert.equal(formatCountdown(1_000_000, 1_000_000), '00:00');
  state = reducePanel(state, { ...first, data: { ...first.data, next_contact_aos: 1_000_300 } });
  assert.equal(state.alerts.length, 2);
  assert.equal(state.alerts[0].next_contact_aos, 1_000_300);
});

test('hypothesis bars retain exactly the backend scores, labels, and plausible set', () => {
  const message = { type: 'hypotheses', data: {
    sat_id: 'SAT-07', scores: { H2: 0.73, H1: 0.19 },
    labels: { H2: 'Stuck-on load', H1: 'Array degradation' }, plausible: ['H2', 'H1'],
  } };
  const state = reducePanel(createPanelState(), message);
  assert.deepEqual(state.hypotheses['SAT-07'], message.data);
  assert.equal(state.hypotheses['SAT-07'].scores.H2, 0.73);
});

test('tool results fill the matching trace row without chat bubbles', () => {
  const call = { type: 'trace_event', data: {
    ts: 1_800_000_000, run_id: 'mock', seq: 4, kind: 'tool_call',
    payload: { tool: 'propose_plan', args: { sat_id: 'SAT-07', commands: ['LOAD_OFF AUX'] } },
  } };
  const result = { type: 'trace_event', data: {
    ts: 1_800_000_001, run_id: 'mock', seq: 5, kind: 'tool_result',
    payload: { tool: 'propose_plan', result: { plan_id: 'P-3' } },
  } };
  let state = reducePanel(createPanelState(), call);
  assert.equal(state.trace.length, 1);
  assert.equal(state.trace[0].tool, 'propose_plan');
  assert.match(state.trace[0].args, /SAT-07/);
  state = reducePanel(state, result);
  assert.equal(state.trace.length, 1);
  assert.equal(state.trace[0].result, 'P-3');
  assert.equal(state.trace[0].kind, 'tool');
});

test('fleet status messages update triage status without treating orbit labels as triage', () => {
  const alert = { type: 'alert', data: { alert_id: 'AL-1', sat_id: 'SAT-07', severity: 'warning',
    time_to_critical_s: 900, next_contact_aos: 1_000_300, reason_key: 'load' } };
  let state = reducePanel(createPanelState(), alert);
  state = reducePanel(state, { type: 'fleet_state', data: { sats: [{ sat_id: 'SAT-07', status: 'working' }] } });
  assert.equal(state.alerts[0].status, 'working');
  state = reducePanel(state, { type: 'fleet_state', data: { sats: [{ sat_id: 'SAT-07', status: 'ECLIPSE' }] } });
  assert.equal(state.alerts[0].status, 'working');
});
