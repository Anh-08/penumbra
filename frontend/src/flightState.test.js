import assert from 'node:assert/strict';
import test from 'node:test';
import {
  advanceEclipse, approvalPayload, createFlightState, flightFocus, hashPrefix, observedPoints, reduceFlight,
  ticketKey,
} from './flightState.js';

test('approval uses the exact backend approval id and selected SimResult hash, including a failed plan', () => {
  const approval = { approval_id: 'AP-3', plan_id: 'P-2', plan_hash: 'redhash', sat_id: 'SAT-07', tier: 2 };
  const failed = { plan_id: 'P-2', plan_hash: 'redhash', passes_all: false };
  const state = reduceFlight(createFlightState(), { type: 'approval_request', data: approval });
  assert.deepEqual(approvalPayload(state.approvals['SAT-07/P-2'], failed),
    { approval_id: 'AP-3', plan_hash: 'redhash' });
  assert.equal(approvalPayload(state.approvals['SAT-07/P-2'], { ...failed, plan_hash: 'stale' }), null);
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
  assert.equal(state.approvals['SAT-07/P-1'].approval_id, 'AP-1');
  assert.equal(state.approvals['SAT-07/P-2'].approval_id, 'AP-2');
  assert.deepEqual(approvalPayload(state.approvals['SAT-07/P-2'], { plan_id: 'P-2', plan_hash: 'red' }),
    { approval_id: 'AP-2', plan_hash: 'red' });
});

test('only finite SOC from get_telemetry tool results becomes observed chart points', () => {
  let state = reduceFlight(createFlightState(), { type: 'fleet_state', data: {
    t: 100, sats: [{ sat_id: 'SAT-07', soc: .48 }],
  } });
  assert.deepEqual(state.telemetry, {});
  state = reduceFlight(state, { type: 'trace_event', data: {
    kind: 'tool_result', payload: { name: 'get_telemetry', result: {
      sat_id: 'SAT-07', t: [100, 110, 120, 130], channels: { soc: [null, .48, null, .43] },
    } },
  } });
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

test('tickets of two satellites with the same plan id do not overwrite each other', () => {
  let state = createFlightState();
  state = reduceFlight(state, { type: 'approval_request', data: {
    approval_id: 'AP-1', plan_id: 'P-1', plan_hash: 'sat07', sat_id: 'SAT-07', tier: 2, sim_id: 'S-1' } });
  state = reduceFlight(state, { type: 'approval_request', data: {
    approval_id: 'AP-1', plan_id: 'P-1', plan_hash: 'sat19', sat_id: 'SAT-19', tier: 2, sim_id: 'S-2' } });
  assert.equal(state.approvals[ticketKey('SAT-07', 'P-1')].plan_hash, 'sat07');
  assert.equal(state.approvals[ticketKey('SAT-19', 'P-1')].plan_hash, 'sat19');
});

test('after approving SAT-07 and clicking a red SAT-19 column, fastforward and reveal still follow SAT-07', () => {
  const ticket = (sat, plan, hash) => ({ type: 'approval_request', data: {
    approval_id: 'AP-1', plan_id: plan, plan_hash: hash, sat_id: sat, tier: 2, sim_id: 'S-1' } });
  const result = (plan, hash, passes) => ({ plan_id: plan, plan_hash: hash, passes_all: passes,
    plausible: ['H2'], per_hyp: { H2: { min_soc_p5: passes ? 0.3 : 0.1 } } });
  let flight = createFlightState();
  for (const message of [
    { type: 'execution', data: { plan_id: 'P-1', sat_id: 'SAT-12', status: 'auto_executed', t: 1, flight_rule: 'FR-2' } },
    ticket('SAT-07', 'P-1', 'h07'),
    { type: 'execution', data: { plan_id: 'P-1', sat_id: 'SAT-07', status: 'executed', t: 2 } },
    ticket('SAT-19', 'P-1', 'h19a'), ticket('SAT-19', 'P-2', 'h19b'),
    { type: 'approval_refused', data: { approval_id: 'AP-1', reason_key: 'failed_verification', failing_hypotheses: ['H3'] } },
  ]) flight = reduceFlight(flight, message);
  assert.equal(flight.approved.sat_id, 'SAT-07');
  const matrixBySat = {
    'SAT-07': { complete: true, results: [result('P-1', 'h07', true)] },
    'SAT-19': { complete: true, results: [result('P-1', 'h19a', false), result('P-2', 'h19b', false)] },
  };
  for (const phase of ['fastforward', 'reveal']) {
    const focus = flightFocus({ phase, selected: 'SAT-19', selectedPlanId: 'P-2', flight, matrixBySat });
    assert.equal(focus.following, true);
    assert.equal(focus.satId, 'SAT-07');  // overlay header, telemetry, reveal and the /api/reveal sat_id
    assert.equal(focus.result.plan_hash, 'h07');
    assert.equal(focus.chartResult.plan_hash, 'h07');
    assert.equal(focus.approval.sat_id, 'SAT-07');
  }
  const matrixView = flightFocus({ phase: 'agent', selected: 'SAT-19', selectedPlanId: 'P-2', flight, matrixBySat });
  assert.equal(matrixView.following, false);
  assert.equal(matrixView.satId, 'SAT-19');  // triage and matrix panels still follow the selection
  assert.equal(matrixView.result.plan_hash, 'h19b');
  assert.equal(matrixView.approval.sat_id, 'SAT-19');
});
