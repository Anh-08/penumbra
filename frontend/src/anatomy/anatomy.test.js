import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import * as THREE from 'three';
import {
  animatePowerFlow, createSatelliteSkeleton, ESSENTIAL_LOADS, LOAD_PARTS, manhattan, PART_IDS, pointAt,
  SIMPLE_PART_IDS,
} from './index.js';

const table = JSON.parse(readFileSync(new URL('../data/satellite_anatomy.json', import.meta.url), 'utf8'));
const HYPS = ['H1', 'H2', 'H3', 'H4', 'H5'];
const LOADS = ['BUS', 'COMMS', 'ADCS', 'PAYLOAD', 'HEATER', 'AUX'];

test('anatomy table: every part has an id, name, role, load class and existing hypotheses; marked illustrative', () => {
  assert.equal(table.illustrative, true);
  assert.match(table._source, /params\.py/);
  assert.match(table.disclaimer, /not the operator's design/);
  const ids = table.parts.map((p) => p.id);
  assert.deepEqual([...ids].sort(), [...PART_IDS].sort());
  for (const p of table.parts) {
    assert.ok(p.id && p.name && p.role, p.id);
    assert.ok(['structure', 'source', 'storage', 'distribution', 'load'].includes(p.kind), p.id);
    assert.ok(p.load === null || LOADS.includes(p.load), p.id);
    assert.equal(p.kind === 'load', p.load !== null, p.id);
    for (const h of p.hypotheses) assert.ok(HYPS.includes(h) && table.hypotheses[h], `${p.id} ${h}`);
    for (const f of p.figures) assert.match(f._source, /params\.py/, `${p.id} ${f.label}`);
  }
  for (const [load, info] of Object.entries(table.loads)) {
    assert.equal(info.essential, ['BUS', 'COMMS', 'ADCS'].includes(load), load);
  }
  // the hypothesis -> part links the issue asks for
  const touches = (h) => table.parts.filter((p) => p.hypotheses.includes(h)).map((p) => p.id).sort();
  assert.deepEqual(touches('H1'), ['wings']);
  assert.deepEqual(touches('H2'), ['aux', 'payload']);
  assert.deepEqual(touches('H3'), ['battery']);
  assert.deepEqual(touches('H4'), ['pcdu']);
  assert.deepEqual(touches('H5'), ['wings']);
});

test('every callout references a part, once, and every part has a callout', () => {
  const ids = new Set(table.parts.map((p) => p.id));
  const called = table.callouts.map((c) => c.part);
  assert.equal(new Set(called).size, called.length);
  for (const c of table.callouts) {
    assert.ok(ids.has(c.part), c.part);
    assert.ok(['left', 'right'].includes(c.side), c.part);
  }
  assert.deepEqual([...called].sort(), [...ids].sort());
});

test('the skeleton builds every part headless, with anchors and pickable meshes', () => {
  const sat = createSatelliteSkeleton(THREE);
  assert.deepEqual([...sat.partIds].sort(), [...PART_IDS].sort());
  for (const id of PART_IDS) {
    assert.ok(sat.anchor(id) instanceof THREE.Vector3, id);
    assert.ok(sat.parts.get(id).lines.length > 0, id);
  }
  assert.ok(sat.pickables.every((m) => PART_IDS.includes(m.userData.partId)));
  const simple = createSatelliteSkeleton(THREE, { detail: 'simple' });
  assert.deepEqual([...simple.partIds].sort(), [...SIMPLE_PART_IDS].sort());
  sat.dispose(); simple.dispose();
});

test('highlight lights one part and dims the rest; null clears', () => {
  const sat = createSatelliteSkeleton(THREE);
  sat.setHighlight('battery');
  assert.equal(sat.highlighted, 'battery');
  assert.equal(sat.parts.get('battery').lines[0].material.opacity, 1);
  assert.ok(sat.parts.get('battery').fills[0].material.opacity > 0);
  assert.ok(sat.parts.get('wings').lines[0].material.opacity < 0.5);
  sat.setHighlight(null);
  assert.equal(sat.highlighted, null);
  assert.equal(sat.parts.get('battery').fills[0].material.opacity, 0);
  assert.equal(sat.setHighlight('no-such-part'), null);
});

test('power flow: sunlit wings feed PCDU and battery, eclipse runs from the battery; loads switch, essentials do not', () => {
  const sat = createSatelliteSkeleton(THREE);
  const flow = animatePowerFlow(THREE, sat);
  const froms = flow.paths.map((p) => `${p.from}>${p.to}`);
  assert.ok(froms.includes('wing_py>pcdu') && froms.includes('wing_my>pcdu') && froms.includes('pcdu>battery'));
  assert.ok(!flow.paths.some((p) => p.load === 'AUX'));  // AUX is normally off
  for (const load of ESSENTIAL_LOADS) {
    assert.ok(flow.paths.some((p) => p.load === load), load);
    assert.equal(flow.setLoad(load, false), false, load);
  }
  assert.equal(flow.setLoad('PAYLOAD', false), true);
  assert.ok(!flow.paths.some((p) => p.load === 'PAYLOAD'));
  assert.equal(sat.isPartOn(LOAD_PARTS.PAYLOAD), false);
  flow.setSunlit(false);
  assert.ok(flow.paths.some((p) => p.from === 'battery' && p.to === 'pcdu'));
  assert.ok(!flow.paths.some((p) => p.from.startsWith('wing')));
  const before = flow.pulsePositions().map((p) => p.join());
  flow.update(0.4);
  assert.notDeepEqual(flow.pulsePositions().map((p) => p.join()), before);  // pulses move
  flow.dispose(); sat.dispose();
});

test('paths are right-angle polylines and pointAt walks them end to end', () => {
  const path = manhattan([0, 0, 0], [1, 2, 3]);
  for (let i = 1; i < path.length; i += 1) {
    const changed = path[i].filter((v, k) => v !== path[i - 1][k]).length;
    assert.equal(changed, 1);  // one axis per segment: no diagonals, no curves
  }
  assert.deepEqual(pointAt(path, 0), [0, 0, 0]);
  assert.deepEqual(pointAt(path, 0.5), [1, 2, 0]);  // half of 1 + 2 + 3: the end of the x and y legs
  const end = pointAt(path, 0.999999);
  assert.ok(Math.abs(end[2] - 3) < 1e-3);
});
