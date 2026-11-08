import assert from 'node:assert/strict';
import test from 'node:test';
import {
  canStart, createIncidentState, DEFAULT_SAT, FALLBACK_SATS, faultLabel, MAX_INCIDENTS, NOTICE_NO_KEY,
  NOTICE_NO_SERVER, noticeFor, reduceIncident, requestBody,
} from './incidentState.js';

const OPTIONS = {
  sats: FALLBACK_SATS,
  faults: [
    { id: 'array_degradation', label: 'Solar array degradation' },
    { id: 'stuck_on_load', label: 'Stuck-on load (AUX)' },
    { id: 'capacity_fade', label: 'Battery capacity fade' },
    { id: 'sensor_bias', label: 'SOC sensor bias' },
  ],
  max_incidents: 3,
  live_available: true,
};

test('before options load, only the recorded incident is offered', () => {
  const s = createIncidentState();
  assert.equal(s.selected[0], DEFAULT_SAT);
  assert.equal(s.sats.length, 24);
  assert.equal(canStart(s), false);
  assert.equal(requestBody(s), null);
  assert.equal(noticeFor(s), NOTICE_NO_SERVER);
  const failed = reduceIncident(s, { type: 'options_failed' });
  assert.equal(failed.loaded, true);
  assert.equal(noticeFor(failed), NOTICE_NO_SERVER);
});

test('options with a model key enable the custom path with the first fault preselected', () => {
  const s = reduceIncident(createIncidentState(), { type: 'options_loaded', options: OPTIONS });
  assert.equal(s.available, true);
  assert.equal(s.fault, 'array_degradation');
  assert.equal(faultLabel(s), 'Solar array degradation');
  assert.equal(noticeFor(s), null);
  assert.equal(canStart(s), true);
  assert.deepEqual(requestBody(s), { incidents: [{ sat_id: 'SAT-07', fault: 'array_degradation' }], seed: 0 });
});

test('a server without a model key says so and keeps the recorded run', () => {
  const s = reduceIncident(createIncidentState(), { type: 'options_loaded', options: { ...OPTIONS, live_available: false } });
  assert.equal(s.available, false);
  assert.equal(noticeFor(s), NOTICE_NO_KEY);
  assert.equal(canStart(s), false);
});

test('satellite chips toggle, never below one, never above max_incidents', () => {
  let s = reduceIncident(createIncidentState(), { type: 'options_loaded', options: OPTIONS });
  s = reduceIncident(s, { type: 'toggle_sat', satId: 'SAT-07' });
  assert.deepEqual(s.selected, ['SAT-07'], 'the last satellite cannot be removed');
  s = reduceIncident(s, { type: 'toggle_sat', satId: 'SAT-12' });
  s = reduceIncident(s, { type: 'toggle_sat', satId: 'SAT-19' });
  assert.deepEqual(s.selected, ['SAT-07', 'SAT-12', 'SAT-19']);
  s = reduceIncident(s, { type: 'toggle_sat', satId: 'SAT-01' });
  assert.equal(s.selected.length, MAX_INCIDENTS, 'a fourth satellite is ignored');
  s = reduceIncident(s, { type: 'toggle_sat', satId: 'SAT-12' });
  assert.deepEqual(s.selected, ['SAT-07', 'SAT-19']);
  assert.equal(reduceIncident(s, { type: 'toggle_sat', satId: 'SAT-99' }), s, 'unknown ids are ignored');
  s = reduceIncident(s, { type: 'set_fault', fault: 'capacity_fade' });
  assert.deepEqual(requestBody(s, 4), {
    incidents: [{ sat_id: 'SAT-07', fault: 'capacity_fade' }, { sat_id: 'SAT-19', fault: 'capacity_fade' }], seed: 4,
  });
  assert.equal(reduceIncident(s, { type: 'set_fault', fault: 'nope' }).fault, 'capacity_fade');
});

test('malformed options degrade to the recorded run instead of throwing', () => {
  const s = reduceIncident(createIncidentState(), { type: 'options_loaded', options: { faults: 'x', live_available: true } });
  assert.equal(s.available, false);
  assert.equal(s.sats.length, 24);
  assert.equal(canStart(s), false);
});
