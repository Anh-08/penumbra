import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import {
  b4From, formatRate, formatSeconds, machineLabel, scaleRow, schedulerRow, SCALE_STOPS, stopLabel,
} from './scale.js';

const b4 = JSON.parse(readFileSync(new URL('../../bench/results/b4.json', import.meta.url), 'utf8'));

test('the slider stops are 24, 1k and 10k and each has a measured row in b4.json', () => {
  assert.deepEqual(SCALE_STOPS.map(stopLabel), ['24', '1k', '10k']);
  for (const n of SCALE_STOPS) {
    const row = scaleRow(b4, n);
    assert.ok(row, `no b4 row for ${n}`);
    assert.equal(row.satellites, n);
    assert.ok(Number.isFinite(row.wall_s) && Number.isFinite(row.evals_per_s));
  }
});

test('the bench API response is unwrapped and the machine is named', () => {
  assert.equal(b4From({ results: { 'b4.json': b4 } }), b4);
  assert.equal(b4From({ results: {} }), null);
  const label = machineLabel(b4);
  assert.match(label, /CPUs/);
  assert.match(label, /Python/);
  assert.equal(machineLabel(null), 'machine not recorded');
});

test('scheduler line uses the largest request count and numbers are formatted', () => {
  assert.equal(schedulerRow(b4).requests, Math.max(...b4.scheduler.map((r) => r.requests)));
  assert.equal(formatSeconds(0.049), '49 ms');
  assert.equal(formatSeconds(12.597), '12.6 s');
  assert.equal(formatRate(37655835.2), '37.7 M evals/s');
  assert.equal(formatRate(undefined), '—');
});
