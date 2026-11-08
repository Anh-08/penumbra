import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import {
  b4From, benchMachines, formatRate, formatSeconds, machineLabel, scaleRow, schedulerRow, SCALE_STOPS, solverLabel,
  stopLabel,
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
  assert.equal(solverLabel('cpsat'), 'CP-SAT');
  assert.equal(solverLabel('greedy'), 'GREEDY');
});

test('one machine without b4_vultr.json, laptop and Vultr side by side with it, each with its own label', () => {
  assert.deepEqual(benchMachines({ results: { 'b4.json': b4 } }).map((m) => m.id), ['laptop']);
  const vultr = {
    ...b4,
    machine: { provider: 'Vultr', plan: 'vc2-4c-8gb', vcpus: 4, ram_gib: 8, python: '3.11.9',
      platform: 'Linux-6.8.0-generic-x86_64-with-glibc2.39' },
  };
  const machines = benchMachines({ results: { 'b4_vultr.json': vultr, 'b4.json': b4, 'b5.json': {} } });
  assert.deepEqual(machines.map((m) => [m.id, m.title]), [['laptop', 'LAPTOP'], ['vultr', 'VULTR']]);
  assert.equal(machineLabel(machines[1].b4), 'Vultr vc2-4c-8gb · Linux x86_64 · 4 vCPUs · 8 GiB RAM · Python 3.11.9');
  assert.equal(machineLabel(machines[0].b4), 'Linux x86_64 · 22 CPUs · Python 3.11.16');
  assert.deepEqual(benchMachines({ results: {} }), []);
  assert.equal(machineLabel({ machine: {} }), 'machine not recorded');
});
