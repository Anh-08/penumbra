import assert from 'node:assert/strict';
import test from 'node:test';
import {
  advanceEclipse, approvalPayload, createFlightState, hashPrefix, observedPoints, reduceFlight,
} from './flightState.js';

test('approval uses the exact backend approval id and selected SimResult hash, including a failed plan', () => {
  const approval = { approval_id: 'AP-3', plan_id: 'P-2', plan_hash: 'redhash', sat_id: 'SAT-07', tier: 2 };
  const failed = { plan_id: 'P-2', plan_hash: 'redhash', passes_all: false };
  const state = reduceFlight(createFlightState(), { type: 'approval_request', data: approval });
  assert.deepEqual(approvalPayload(state.approvals['P-2'], failed),
    { approval_id: 'AP-3', plan_hash: 'redhash' });
  assert.equal(approvalPayload(state.approvals['P-2'], { ...failed, plan_hash: 'stale' }), null);
  assert.equal(hashPrefix(failed.plan_hash), 'redhash');
});

test('refusal retains backend reason and failing hypotheses; execution clears it', () => {
  let state = reduceFlight(createFlightState(), { type: 'approval_request', data: {
    approval_id: 'AP-3', sat_id: 'SAT-07', plan_id: 'P-1', plan_hash: 'hash', tier: 2,
  } });
  state = reduceFlight(state, { type: 'approval_refused', data: {
    approval_id: 'AP-3', reason_key: 'failed_verification', failing_hypotheses: ['H2', 'H5'],
  } });
  assert.deepEqual(state.refusal.failing_hypotheses, ['H2', 'H5']);
  state = reduceFlight(state, { type: 'execution', data: {
    plan_id: 'P-1', sat_id: 'SAT-07', status: 'executed', t: 1800000000,
  } });
  assert.equal(state.refusal, null);
  assert.equal(state.execution.plan_id, 'P-1');
});

test('each plan retains its own backend-issued approval ticket', () => {
  let state = createFlightState();
  state = reduceFlight(state, { type: 'approval_request', data: {
    approval_id: 'AP-1', plan_id: 'P-1', plan_hash: 'good', sat_id: 'SAT-07', tier: 2,
  } });
  state = reduceFlight(state, { type: 'approval_request', data: {
    approval_id: 'AP-2', plan_id: 'P-2', plan_hash: 'red', sat_id: 'SAT-07', tier: 2,
  } });
  assert.equal(state.approvals['P-1'].approval_id, 'AP-1');
  assert.equal(state.approvals['P-2'].approval_id, 'AP-2');
  assert.deepEqual(approvalPayload(state.approvals['P-2'], { plan_id: 'P-2', plan_hash: 'red' }),
    { approval_id: 'AP-2', plan_hash: 'red' });
});

test('only finite downlinked SOC from fleet messages becomes observed chart points', () => {
  let state = createFlightState();
  for (const [t, soc] of [[100, null], [110, .48], [120, null], [130, .43]]) {
    state = reduceFlight(state, { type: 'fleet_state', data: {
      t, sats: [{ sat_id: 'SAT-07', soc }],
    } });
  }
  assert.deepEqual(state.telemetry['SAT-07'], [{ t: 110, soc: .48 }, { t: 130, soc: .43 }]);
  assert.deepEqual(observedPoints({ t: [100, 120] }, state.telemetry['SAT-07']),
    [{ t: 110, soc: .48 }]);
});

test('reveal truth stays separate from telemetry until the reveal message', () => {
  const state = reduceFlight(createFlightState(), { type: 'reveal', data: {
    sat_id: 'SAT-07', truth: { kind: 'stuck_on_load', target: 'SAT-07', onset_t: 100, params: {} },
    diagnosed: { H: 'H2', label: 'Stuck-on load', posterior: .78 },
    truth_soc: { t: [100, 110], soc: [.5, .48] },
  } });
  assert.equal(state.reveals['SAT-07'].truth.kind, 'stuck_on_load');
  assert.deepEqual(state.telemetry, {});
});

test('fast-forward stops only after the selected satellite enters and exits eclipse', () => {
  let step = { entered: false, stopping: false };
  let update = advanceEclipse(step, 1);
  assert.equal(update.complete, false);
  update = advanceEclipse(update.step, 0);
  assert.equal(update.step.entered, true);
  assert.equal(update.complete, false);
  update = advanceEclipse(update.step, .5);
  assert.equal(update.complete, false);
  update = advanceEclipse(update.step, 1);
  assert.equal(update.complete, true);
  assert.equal(advanceEclipse(update.step, 1).complete, false);
});
