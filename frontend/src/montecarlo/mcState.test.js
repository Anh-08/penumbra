import assert from 'node:assert/strict';
import test from 'node:test';
import {
  assignHypotheses, createMcState, currentResult, DWELL_MS, FADE_MS, gridModel, HOLD_MS, loadEnabled, reduceMc,
  sampleOutcomes, saveEnabled, socCue, SOC_LINE, spotlightModel, spotlightTile, sunlit, TILE_COUNT,
} from './mcState.js';

const stats = (pass_rate, min_soc_p5 = 0.4) => ({ pass_rate, min_soc_p5, min_v_bus: 14, t_bat_min_c: 5,
  t_bat_max_c: 20, payload_uptime: 0.5 });
const result = (planId, perHyp, extra = {}) => ({ sim_id: 'S-1', plan_id: planId, plan_hash: `hash-${planId}`,
  per_hyp: perHyp, plausible: Object.keys(perHyp), passes_all: Object.values(perHyp).every((s) => s.pass_rate >= 0.95),
  worst_margin_soc: 0.05, payload_uptime_worst: 0.5, n_commands: 1, ...extra });
const msg = (type, data) => ({ type, data });
const call = (name, args, seq = 1) => msg('trace_event', { run_id: 'r', seq, kind: 'tool_call', payload: { name, args } });
const send = (state, message, now = 0) => reduceMc(state, { type: 'message', message, now });

function agentState() {
  let s = createMcState(true);
  s = send(s, msg('alert', { sat_id: 'SAT-12' }));
  s = send(s, call('score_hypotheses', { sat_id: 'SAT-12' }));
  return s;
}

test('outcome sampling is deterministic and matches pass_rate within one tile', () => {
  for (const p of [1, 0.95, 0.9, 0.5, 0.2, 0]) {
    const hyps = Array(TILE_COUNT).fill('H2');
    const r = result('P-1', { H2: stats(p) });
    const a = sampleOutcomes(r, hyps);
    assert.deepEqual(a, sampleOutcomes(r, hyps));
    const fails = a.filter((o) => o === 'fail').length;
    assert.ok(Math.abs(fails - TILE_COUNT * (1 - p)) <= 1, `${p}: ${fails}`);
  }
  // a 0.95 column shows about 1 red tile in 20 and a 0.20 column about 16
  const one = sampleOutcomes(result('P-1', { H1: stats(0.95) }), Array(20).fill('H1')).filter((o) => o === 'fail');
  const many = sampleOutcomes(result('P-1', { H1: stats(0.2) }), Array(20).fill('H1')).filter((o) => o === 'fail');
  assert.ok(one.length <= 1 + 0 && one.length >= 1 - 1);
  assert.ok(many.length >= 15 && many.length <= 17);
});

test('the plan hash seeds which tiles fail', () => {
  const hyps = Array(20).fill('H3');
  const a = sampleOutcomes(result('P-1', { H3: stats(0.5) }), hyps);
  const b = sampleOutcomes({ ...result('P-1', { H3: stats(0.5) }), plan_hash: 'another' }, hyps);
  assert.notDeepEqual(a, b);
  assert.deepEqual(sampleOutcomes(result('P-1', {}), ['H0', 'H1']), ['unknown', 'unknown']);
});

test('hypotheses are assigned in proportion to the plausible set', () => {
  assert.deepEqual(assignHypotheses(['H0']), Array(20).fill('H0'));
  const two = assignHypotheses(['H2', 'H0']);
  assert.equal(two.filter((h) => h === 'H0').length, 10);
  assert.equal(two.filter((h) => h === 'H2').length, 10);
  const three = assignHypotheses(['H1', 'H5', 'H0'], { H5: 0.6, H1: 0.3, H0: 0.1 });
  assert.equal(three.length, 20);
  assert.deepEqual(['H0', 'H1', 'H5'].map((h) => three.filter((x) => x === h).length), [6, 7, 7]);
  assert.deepEqual(three, [...three].sort(), 'grouped in H0..H5 order');
  assert.deepEqual(assignHypotheses([]), Array(20).fill('H0'));
  assert.deepEqual(assignHypotheses(['H9', 'H4']), Array(20).fill('H4'));
});

test('overlay opens on the first simulate of the agent phase and shows that satellite', () => {
  let s = createMcState(true);
  s = send(s, call('simulate', { plan_id: 'P-1' }));
  assert.equal(s.open, false, 'attract phase: no agent run yet');
  s = agentState();
  assert.equal(s.phase, 'agent');
  s = send(s, call('simulate', { plan_id: 'P-1' }), 100);
  assert.equal(s.open, true);
  assert.equal(s.satId, 'SAT-12');
  assert.equal(s.planId, 'P-1');
  assert.equal(s.startedAt, 100);
});

test('the chip turns the overlay off and is remembered per viewer', () => {
  let s = createMcState(false);
  s = send(s, msg('alert', { sat_id: 'SAT-12' }));
  s = send(s, call('simulate', { plan_id: 'P-1' }));
  assert.equal(s.open, false);
  s = reduceMc(agentState(), { type: 'message', message: call('simulate', { plan_id: 'P-1' }) });
  assert.equal(s.open, true);
  s = reduceMc(s, { type: 'toggle_chip' });
  assert.equal(s.open, false);
  assert.equal(s.enabled, false);
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  assert.equal(loadEnabled(storage), true, 'default ON');
  saveEnabled(storage, false);
  assert.equal(loadEnabled(storage), false);
  assert.equal(loadEnabled({ getItem() { throw new Error('blocked'); } }), true);
});

test('overlay stays HOLD_MS after the matrix is complete, closes at once when the phase leaves agent/approval', () => {
  let s = send(agentState(), call('simulate', { plan_id: 'P-1' }), 0);
  s = send(s, msg('matrix', { sat_id: 'SAT-12', results: [result('P-1', { H0: stats(1) })], complete: false }), 500);
  assert.equal(currentResult(s).plan_id, 'P-1');
  s = send(s, msg('matrix', { sat_id: 'SAT-12', results: [result('P-1', { H0: stats(1) })], complete: true }), 2000);
  assert.equal(s.closeAt, 2000 + HOLD_MS);
  s = reduceMc(s, { type: 'tick', now: 2000 + HOLD_MS - 1 });
  assert.equal(s.open, true);
  s = reduceMc(s, { type: 'tick', now: 2000 + HOLD_MS });
  assert.equal(s.open, false);

  let t = send(agentState(), call('simulate', { plan_id: 'P-1' }), 0);
  t = send(t, msg('approval_request', { sat_id: 'SAT-12', plan_id: 'P-1' }));
  assert.equal(t.open, true, 'approval keeps it');
  t = send(t, msg('demo_state', { state: 'fastforward', speed: 60 }));
  assert.equal(t.open, false, 'fast-forward closes it at once');
  t = send(agentState(), call('simulate', { plan_id: 'P-1' }), 0);
  assert.equal(reduceMc(t, { type: 'phase', phase: 'reveal' }).open, false);
});

test('a matrix for another satellite does not close the overlay', () => {
  let s = send(agentState(), call('simulate', { plan_id: 'P-1' }), 0);
  s = send(s, msg('matrix', { sat_id: 'SAT-07', results: [result('P-1', { H0: stats(1) })], complete: true }), 10);
  assert.equal(s.closeAt, null);
  assert.equal(currentResult(s), null, 'P-1 of SAT-07 is not P-1 of SAT-12');
});

test('plans simulated in one turn each get their own run, in order', () => {
  let s = send(agentState(), call('simulate', { plan_id: 'P-1' }), 0);
  s = send(s, call('simulate', { plan_id: 'P-2' }), 5);
  s = send(s, call('simulate', { plan_id: 'P-3' }), 6);
  assert.equal(s.planId, 'P-1');
  assert.deepEqual(s.queue, ['P-2', 'P-3']);
  const run = s.run;
  s = reduceMc(s, { type: 'tick', now: DWELL_MS - 1 });
  assert.equal(s.planId, 'P-1');
  s = reduceMc(s, { type: 'tick', now: DWELL_MS });
  assert.equal(s.planId, 'P-2');
  assert.equal(s.run, run + 1, 'tiles reset for the next plan');
  s = send(s, msg('matrix', { sat_id: 'SAT-12', results: [], complete: true }), DWELL_MS + 10);
  s = reduceMc(s, { type: 'tick', now: DWELL_MS + 10 + HOLD_MS });
  assert.equal(s.open, true, 'P-3 has not been shown yet');
  assert.equal(s.planId, 'P-3');
  s = reduceMc(s, { type: 'tick', now: 3 * DWELL_MS + HOLD_MS });
  assert.equal(s.open, false);
});

test('a new satellite restarts the grid', () => {
  let s = send(agentState(), call('simulate', { plan_id: 'P-1' }), 0);
  s = send(s, call('propose_plan', { sat_id: 'SAT-07', commands: [] }), 10);
  s = send(s, call('simulate', { plan_id: 'P-1' }), 20);
  assert.equal(s.satId, 'SAT-07');
  assert.equal(s.startedAt, 20);
  assert.deepEqual(s.queue, []);
});

test('simulate tool results count as SimResult messages', () => {
  let s = send(agentState(), call('simulate', { plan_id: 'P-1' }), 0);
  s = send(s, msg('trace_event', { run_id: 'r', seq: 2, kind: 'tool_result',
    payload: { name: 'simulate', result: result('P-1', { H2: stats(0.2) }) } }));
  assert.equal(currentResult(s).per_hyp.H2.pass_rate, 0.2);
});

test('grid model, verdict and spotlight use backend numbers only', () => {
  let s = send(agentState(), msg('hypotheses', { sat_id: 'SAT-12', scores: { H0: 0.3, H2: 0.7 },
    labels: { H0: 'Nominal', H2: 'Stuck-on load' }, plausible: ['H0', 'H2'] }));
  s = send(s, call('simulate', { plan_id: 'P-2' }), 0);
  let m = gridModel(s);
  assert.equal(m.tiles.length, TILE_COUNT);
  assert.ok(m.tiles.every((t) => t.outcome === 'unknown'));
  assert.equal(m.verdict, null);
  s = send(s, msg('matrix', { sat_id: 'SAT-12', complete: false,
    results: [result('P-2', { H0: stats(1, 0.5), H2: stats(0.2, 0.1) })] }));
  m = gridModel(s);
  assert.equal(m.verdict, 'FAILS');
  assert.equal(m.tiles.filter((t) => t.hyp === 'H2').length, 10);
  assert.equal(m.tiles.find((t) => t.hyp === 'H2').label, 'Stuck-on load');
  assert.equal(m.tiles.find((t) => t.hyp === 'H2').fault, 'aux');
  assert.equal(m.tiles.find((t) => t.hyp === 'H0').fault, null);
  const tile = spotlightTile(m);
  assert.equal(m.tiles[tile].hyp, 'H2');
  assert.equal(m.tiles[tile].outcome, 'fail');
  const spot = spotlightModel(s, m, tile);
  assert.deepEqual([spot.passRate, spot.minSocP5, spot.worstMarginSoc, spot.posterior], [0.2, 0.1, 0.05, 0.7]);
  assert.equal(spotlightModel(s, m, 99), null);
  assert.equal(reduceMc(s, { type: 'expand', tile: 3 }).expanded, 3);
  assert.equal(reduceMc(s, { type: 'expand', tile: 40 }).expanded, null);
});

test('a satellite waiting for the human holds the grid until its execution, then it fades', () => {
  let s = send(agentState(), call('simulate', { plan_id: 'P-1' }), 0);
  s = send(s, msg('matrix', { sat_id: 'SAT-12', results: [result('P-1', { H0: stats(1) })], complete: true }), 1000);
  s = send(s, msg('approval_request', { sat_id: 'SAT-12', plan_id: 'P-1', approval_id: 'AP-1', tier: 2 }), 1100);
  assert.equal(s.held, true);
  assert.equal(s.closeAt, null);
  s = reduceMc(s, { type: 'tick', now: 1100 + 10 * HOLD_MS });
  assert.equal(s.open, true, 'held while the judge thinks');
  s = reduceMc(s, { type: 'collapse', now: 1100 + 10 * HOLD_MS });
  assert.equal(s.closeAt, null, 'Esc does not start the fade while held');
  s = send(s, msg('execution', { sat_id: 'SAT-07', plan_id: 'P-1', status: 'executed', t: 0 }), 70000);
  assert.equal(s.closeAt, null, 'another satellite executing changes nothing');
  s = send(s, msg('execution', { sat_id: 'SAT-12', plan_id: 'P-1', status: 'executed', t: 0 }), 80000);
  assert.equal(s.held, false);
  assert.equal(s.closeAt, 80000 + FADE_MS);
  assert.equal(reduceMc(s, { type: 'tick', now: 80000 + FADE_MS }).open, false);

  // the ticket arrives before the matrix completes: still held, the completion does not schedule a close
  let t = send(agentState(), call('simulate', { plan_id: 'P-1' }), 0);
  t = send(t, msg('approval_request', { sat_id: 'SAT-12', plan_id: 'P-1' }), 500);
  t = send(t, msg('matrix', { sat_id: 'SAT-12', results: [result('P-1', { H0: stats(1) })], complete: true }), 900);
  assert.equal(t.complete, true);
  assert.equal(t.closeAt, null);
  assert.equal(send(t, msg('demo_state', { state: 'fastforward', speed: 600 })).open, false, 'phase change still closes');
});

test('an expanded sample holds the overlay; Esc (collapse) restarts the 1.5 s fade', () => {
  let s = send(agentState(), call('simulate', { plan_id: 'P-1' }), 0);
  s = send(s, msg('matrix', { sat_id: 'SAT-12', results: [result('P-1', { H0: stats(1) })], complete: true }), 2000);
  s = reduceMc(s, { type: 'expand', tile: 0 });
  s = reduceMc(s, { type: 'tick', now: 9000 });
  assert.equal(s.open, true);
  s = reduceMc(s, { type: 'collapse', now: 9000 });
  assert.equal(s.closeAt, 9000 + FADE_MS);
  assert.equal(reduceMc(s, { type: 'tick', now: 9000 + FADE_MS }).open, false);
});

test('cue curves: eclipse is longer under H5, the SOC bar ends at the sampled outcome', () => {
  const sunFraction = (h) => Array.from({ length: 400 }, (_, i) => sunlit(h, i / 100)).filter(Boolean).length / 400;
  assert.ok(sunFraction('H5') < sunFraction('H0'));
  for (let t = 0; t < 8; t += 0.37) {
    const v = socCue('H1', t);
    assert.ok(v > 0 && v < 1);
  }
  assert.ok(socCue('H2', 3, 'fail', 5) < SOC_LINE);
  assert.ok(socCue('H2', 3, 'pass', 5) > SOC_LINE);
  assert.equal(socCue('H2', 3, 'pass', null), socCue('H2', 3));
});
