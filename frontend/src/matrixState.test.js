import assert from 'node:assert/strict';
import test from 'node:test';
import { cellPasses, createMatrixState, rankedPlanId, reduceMatrix, scheduleConflicts } from './matrixState.js';

const result = (plan_id, min_soc_p5, pass_rate) => ({
  sim_id: `S-${plan_id.slice(2)}`, plan_id, plan_hash: `${plan_id}-hash`,
  per_hyp: { H2: { pass_rate, min_soc_p5, min_v_bus: 27.5,
    t_bat_min_c: 3, t_bat_max_c: 18, payload_uptime: .7 } },
  plausible: ['H2'], passes_all: pass_rate === 1,
  worst_margin_soc: min_soc_p5 - .3, payload_uptime_worst: .7, n_commands: 1,
});

test('matrix updates append each backend SimResult once and preserve its SOC and pass rate', () => {
  const first = result('P-1', .31, 1);
  const second = result('P-2', .26, .42);
  let state = reduceMatrix(createMatrixState(), { type: 'matrix', data: {
    sat_id: 'SAT-07', results: [first], complete: false,
  } });
  assert.deepEqual(state.bySat['SAT-07'].results.map((entry) => entry.plan_id), ['P-1']);
  assert.equal(state.bySat['SAT-07'].freshPlanId, 'P-1');
  assert.equal(rankedPlanId(state.bySat['SAT-07']), null);
  state = reduceMatrix(state, { type: 'matrix', data: {
    sat_id: 'SAT-07', results: [second, first], complete: true,
  } });
  assert.deepEqual(state.bySat['SAT-07'].results.map((entry) => entry.plan_id), ['P-2', 'P-1']);
  assert.equal(state.bySat['SAT-07'].results[0].per_hyp.H2.min_soc_p5, .26);
  assert.equal(state.bySat['SAT-07'].results[0].per_hyp.H2.pass_rate, .42);
  assert.equal(state.bySat['SAT-07'].freshPlanId, 'P-2');
  assert.equal(state.bySat['SAT-07'].complete, true);
  assert.equal(rankedPlanId(state.bySat['SAT-07']), 'P-2');
});

test('cell color uses the backend pass-rate threshold, while the plan verdict remains passes_all', () => {
  assert.equal(cellPasses({ pass_rate: .949 }), false);
  assert.equal(cellPasses({ pass_rate: .95 }), true);
  const backendResult = { ...result('P-4', .35, .95), passes_all: false };
  assert.equal(cellPasses(backendResult.per_hyp.H2), true);
  assert.equal(backendResult.passes_all, false);
});

test('infeasible comes only from fleet_state safe_mode_recommended', () => {
  let state = reduceMatrix(createMatrixState(), { type: 'matrix', data: {
    sat_id: 'SAT-19', results: [result('P-1', .18, 0)], complete: true,
  } });
  assert.equal(state.statusBySat['SAT-19'], undefined);
  state = reduceMatrix(state, { type: 'fleet_state', data: {
    sats: [{ sat_id: 'SAT-19', status: 'safe_mode_recommended' }],
  } });
  assert.equal(state.statusBySat['SAT-19'], 'safe_mode_recommended');
});

test('schedule view opens for two satellites sequenced at one station', () => {
  let state = createMatrixState();
  state = reduceMatrix(state, { type: 'matrix', data: { sat_id: 'SAT-07', results: [result('P-1', .31, 1)], complete: false } });
  state = reduceMatrix(state, { type: 'matrix', data: { sat_id: 'SAT-19', results: [result('P-2', .26, .4)], complete: false } });
  state = reduceMatrix(state, { type: 'schedule', data: { assignments: [
    { plan_id: 'P-1', station_id: 'SVALBARD', start_t: 100, end_t: 130 },
    { plan_id: 'P-2', station_id: 'SVALBARD', start_t: 140, end_t: 170 },
  ], unscheduled: [], solver: 'cpsat', optimal: true, solve_ms: 30 } });
  const conflict = scheduleConflicts(state.schedule, state.planSat);
  assert.equal(conflict.stations[0].station_id, 'SVALBARD');
  assert.deepEqual(conflict.stations[0].windows.map((window) => window.sat_id), ['SAT-07', 'SAT-19']);
});

test('unscheduled plans say no slot before deadline without naming a competing station', () => {
  let state = createMatrixState();
  state = reduceMatrix(state, { type: 'matrix', data: { sat_id: 'SAT-07', results: [result('P-1', .31, 1)], complete: false } });
  state = reduceMatrix(state, { type: 'matrix', data: { sat_id: 'SAT-12', results: [result('P-3', .28, .8)], complete: false } });
  state = reduceMatrix(state, { type: 'schedule', data: { assignments: [
    { plan_id: 'P-1', station_id: 'SVALBARD', start_t: 100, end_t: 130 },
  ], unscheduled: ['P-3'], solver: 'cpsat', optimal: true, solve_ms: 30 } });
  const conflict = scheduleConflicts(state.schedule, state.planSat);
  assert.deepEqual(conflict.unscheduled, [{ plan_id: 'P-3', sat_id: 'SAT-12' }]);
  assert.deepEqual(conflict.stations, []);
  assert.equal(scheduleConflicts({ ...state.schedule, unscheduled: [] }, state.planSat), null);
});
