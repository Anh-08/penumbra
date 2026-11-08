import assert from 'node:assert/strict';
import test from 'node:test';
import {
  autoStopDue, canRecord, chordTransition, createVoiceState, detectSupport, errorLineFor, LINE_HINT, LINE_INSECURE,
  LINE_NO_RECORDER, LINE_SERVER_OFF, MAX_CLIP_S, pickerActionsFor, reduceVoice, statusLine,
} from './voiceCommand.js';
import { createIncidentState, reduceIncident } from './incidentState.js';

const OPTIONS = {
  sats: Array.from({ length: 24 }, (_, i) => `SAT-${String(i + 1).padStart(2, '0')}`),
  faults: [{ id: 'array_degradation', label: 'Solar array degradation' }, { id: 'stuck_on_load', label: 'Stuck-on load (AUX)' },
    { id: 'capacity_fade', label: 'Battery capacity fade' }, { id: 'sensor_bias', label: 'SOC sensor bias' }],
  max_incidents: 3, live_available: true,
};
const ready = () => reduceVoice(createVoiceState(), { type: 'support', support: detectSupport({ isSecureContext: true, hasMediaDevices: true, hasMediaRecorder: true }) });

test('support: plain http, no recorder, and a good browser', () => {
  assert.deepEqual(detectSupport({ isSecureContext: false, hasMediaDevices: true, hasMediaRecorder: true }), { ok: false, line: LINE_INSECURE });
  assert.deepEqual(detectSupport({ isSecureContext: true, hasMediaDevices: false, hasMediaRecorder: true }), { ok: false, line: LINE_NO_RECORDER });
  assert.deepEqual(detectSupport({ isSecureContext: true, hasMediaDevices: true, hasMediaRecorder: true }), { ok: true, line: LINE_HINT });
  const s = ready();
  assert.equal(canRecord(s), true);
  assert.equal(statusLine(s), LINE_HINT);
  const http = reduceVoice(createVoiceState(), { type: 'support', support: detectSupport({ isSecureContext: false, hasMediaDevices: true, hasMediaRecorder: true }) });
  assert.equal(canRecord(http), false);
  assert.equal(statusLine(http), LINE_INSECURE);
  assert.equal(reduceVoice(http, { type: 'start', now: 0 }).status, 'idle', 'cannot start without support');
});

test('Ctrl+Alt chord starts on the second key down and stops when either goes up', () => {
  let s = ready();
  let t = reduceVoice(s, { type: 'keydown', key: 'Control' });
  assert.equal(chordTransition(s, t), null);
  s = t; t = reduceVoice(s, { type: 'keydown', key: 'Alt' });
  assert.equal(chordTransition(s, t), 'start');
  s = reduceVoice(t, { type: 'start', now: 1000 });
  assert.equal(s.status, 'listening');
  t = reduceVoice(s, { type: 'keyup', key: 'Alt' });
  assert.equal(chordTransition(s, t), 'stop');
  s = reduceVoice(t, { type: 'stop' });
  assert.equal(s.status, 'thinking');
  assert.equal(reduceVoice(s, { type: 'keydown', key: 'x' }), s, 'other keys are ignored');
  const blurred = reduceVoice(s, { type: 'blur' });
  assert.deepEqual(blurred.held, { Control: false, Alt: false });
});

test('elapsed ticks, auto-stop at the clip limit, cancel returns to idle', () => {
  let s = reduceVoice(ready(), { type: 'start', now: 5000 });
  s = reduceVoice(s, { type: 'tick', now: 7600 });
  assert.equal(Math.floor(s.elapsed), 2);
  assert.match(statusLine(s), /LISTENING · 2 s/);
  assert.equal(autoStopDue(s, 5000 + MAX_CLIP_S * 1000 - 1), false);
  assert.equal(autoStopDue(s, 5000 + MAX_CLIP_S * 1000), true);
  assert.equal(reduceVoice(s, { type: 'cancel' }).status, 'idle');
});

test('a result fills the picker without starting anything; unknown ids are dropped', () => {
  const incident = reduceIncident(createIncidentState(), { type: 'options_loaded', options: OPTIONS });
  const result = { transcript: 'stuck load on satellite seven and twelve', sats: ['SAT-07', 'SAT-12', 'SAT-99'], fault: 'stuck_on_load', notes: 'Heard 2 satellites and a fault' };
  const actions = pickerActionsFor(result, incident);
  assert.deepEqual(actions, [{ type: 'set_selection', sats: ['SAT-07', 'SAT-12'] }, { type: 'set_fault', fault: 'stuck_on_load' }]);
  const after = actions.reduce(reduceIncident, incident);
  assert.deepEqual(after.selected, ['SAT-07', 'SAT-12']);
  assert.equal(after.fault, 'stuck_on_load');
  assert.deepEqual(pickerActionsFor({ sats: [], fault: 'nope' }, incident), []);
  assert.deepEqual(pickerActionsFor({ sats: ['SAT-01', 'SAT-02', 'SAT-03', 'SAT-04'] }, incident)[0].sats.length, 3);
  const v = reduceVoice(reduceVoice(ready(), { type: 'start', now: 0 }), { type: 'stop' });
  const done = reduceVoice(v, { type: 'result', result });
  assert.equal(done.status, 'done');
  assert.match(statusLine(done), /stuck load on satellite seven/);
  assert.match(statusLine(done), /Heard 2 satellites/);
  assert.equal(canRecord(done), true, 'ready for the next utterance');
});

test('server errors map to one line each', () => {
  assert.equal(errorLineFor(503), LINE_SERVER_OFF);
  assert.equal(errorLineFor(404), LINE_SERVER_OFF);
  assert.match(errorLineFor(413), /15 seconds/);
  assert.equal(errorLineFor(500), null);
  const e = reduceVoice(reduceVoice(ready(), { type: 'start', now: 0 }), { type: 'error', message: LINE_SERVER_OFF });
  assert.equal(e.status, 'error');
  assert.equal(statusLine(e), LINE_SERVER_OFF);
  assert.equal(canRecord(e), true);
});

test('set_selection on the picker keeps only known ids, at most max, never empty', () => {
  const incident = reduceIncident(createIncidentState(), { type: 'options_loaded', options: OPTIONS });
  assert.deepEqual(reduceIncident(incident, { type: 'set_selection', sats: ['SAT-19', 'SAT-03', 'bogus'] }).selected, ['SAT-19', 'SAT-03']);
  assert.deepEqual(reduceIncident(incident, { type: 'set_selection', sats: ['bogus'] }).selected, ['SAT-07']);
  assert.equal(reduceIncident(incident, { type: 'set_selection', sats: ['SAT-01', 'SAT-02', 'SAT-03', 'SAT-04'] }).selected.length, 3);
});
