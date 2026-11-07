import assert from 'node:assert/strict';
import test from 'node:test';
import { buildFleetState, markerAlpha, orbitTrack, STATIONS } from './mockFleet.js';

test('preview fleet follows the fleet_state shape with 24 anonymous satellites', () => {
  const state = buildFleetState(1_799_999_000, 120);
  assert.equal(state.sats.length, 24);
  assert.deepEqual(state.sats.map(({ sat_id }) => sat_id),
    Array.from({ length: 24 }, (_, index) => `SAT-${String(index + 1).padStart(2, '0')}`));
  assert.equal(state.speed, 120);
  assert.equal(state.sun_direction.length, 3);
  assert.ok(Math.abs(Math.hypot(...state.sun_direction) - 1) < 1e-10);
  for (const sat of state.sats) {
    assert.ok(sat.lat_deg >= -90 && sat.lat_deg <= 90);
    assert.ok(sat.lon_deg >= -180 && sat.lon_deg <= 180);
    assert.ok(sat.alt_km >= 450 && sat.alt_km <= 600);
    assert.ok(sat.illum >= 0 && sat.illum <= 1);
    assert.ok(sat.soc >= 0 && sat.soc <= 1);
  }
});

test('each orbit track spans one future orbit and moves across the Earth', () => {
  const t = 1_799_999_000;
  const track = orbitTrack('SAT-07', t);
  assert.ok(track.length >= 30);
  assert.ok(track.every((point) => point.length === 3));
  assert.notDeepEqual(track[0], track[Math.floor(track.length / 3)]);
  assert.throws(() => orbitTrack('SAT-25', t));
});

test('station coordinates use only the three contracted names', () => {
  assert.deepEqual(STATIONS.map(({ id }) => id), ['SVALBARD', 'TROLL', 'FAIRBANKS']);
});

test('the preview enters penumbra and marker opacity fades continuously with illumination', () => {
  const partial = Array.from({ length: 570 }, (_, index) => buildFleetState(1_800_000_000 + index * 10))
    .flatMap(({ sats }) => sats)
    .filter(({ illum }) => illum > 0.05 && illum < 0.95);
  assert.ok(partial.length > 0, 'preview should show nonzero penumbra intervals');
  const alphas = [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1].map(markerAlpha);
  assert.ok(alphas.every((alpha, index) => index === 0 || alpha >= alphas[index - 1]));
  assert.ok(alphas[0] < alphas.at(-1));
});
