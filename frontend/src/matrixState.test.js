import assert from 'node:assert/strict';
import test from 'node:test';
import { createMatrixState, reduceMatrix } from './matrixState.js';

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
  state = reduceMatrix(state, { type: 'matrix', data: {
    sat_id: 'SAT-07', results: [first, second], complete: true,
  } });
  assert.deepEqual(state.bySat['SAT-07'].results.map((entry) => entry.plan_id), ['P-1', 'P-2']);
  assert.equal(state.bySat['SAT-07'].results[1].per_hyp.H2.min_soc_p5, .26);
  assert.equal(state.bySat['SAT-07'].results[1].per_hyp.H2.pass_rate, .42);
  assert.equal(state.bySat['SAT-07'].freshPlanId, 'P-2');
  assert.equal(state.bySat['SAT-07'].complete, true);
  assert.equal(state.bySat['SAT-07'].rank, null);
});

test('best and infeasible appear only after an explicit backend RankResult signal', () => {
  let state = reduceMatrix(createMatrixState(), { type: 'matrix', data: {
    sat_id: 'SAT-19', results: [result('P-1', .18, 0)], complete: true,
  } });
  assert.equal(state.bySat['SAT-19'].rank, null);
  state = reduceMatrix(state, { type: 'trace_event', data: {
    ts: 1_800_000_000, kind: 'tool_result', run_id: 'demo', seq: 4,
    payload: { sat_id: 'SAT-19', result: { ordered: ['P-1'], status: 'infeasible' } },
  } });
  assert.deepEqual(state.bySat['SAT-19'].rank,
    { ordered: ['P-1'], status: 'infeasible' });
});

test('schedule details are retained without guessing an unscheduled plan’s station', () => {
  const schedule = { assignments: [
    { plan_id: 'P-1', station_id: 'SVALBARD', start_t: 100, end_t: 130 },
  ], unscheduled: ['P-2'], solver: 'cpsat', optimal: true, solve_ms: 30 };
  const state = reduceMatrix(createMatrixState(), { type: 'schedule', data: schedule });
  assert.deepEqual(state.schedule, schedule);
});
