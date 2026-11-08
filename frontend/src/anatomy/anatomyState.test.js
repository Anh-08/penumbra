import assert from 'node:assert/strict';
import test from 'node:test';
import {
  createAnatomyState, ENABLED_KEY, focusPart, keyAction, loadEnabled, reduceAnatomy, saveEnabled,
} from './anatomyState.js';

const run = (state, ...actions) => actions.reduce(reduceAnatomy, state);

test('open and close; the view only opens while the ANATOMY chip is on', () => {
  let s = createAnatomyState();
  s = run(s, { type: 'open' });
  assert.equal(s.open, true);
  s = run(s, { type: 'hover', part: 'battery' }, { type: 'click', part: 'pcdu' }, { type: 'close' });
  assert.deepEqual([s.open, s.hovered, s.pinned], [false, null, null]);
  s = run(s, { type: 'toggle_chip' }, { type: 'open' });
  assert.equal(s.open, false);
  s = run(createAnatomyState(), { type: 'open' }, { type: 'toggle_chip' });
  assert.equal(s.open, false);  // switching the chip off closes the view
});

test('hover lights a part, click pins it, a second click or Esc unpins, then Esc closes', () => {
  let s = run(createAnatomyState(), { type: 'open' }, { type: 'hover', part: 'battery' });
  assert.equal(focusPart(s), 'battery');
  s = run(s, { type: 'click', part: 'wings' }, { type: 'hover', part: 'battery' });
  assert.equal(focusPart(s), 'wings');  // pinned wins over hovered
  s = run(s, { type: 'click', part: 'wings' });
  assert.equal(s.pinned, null);
  s = run(s, { type: 'click', part: 'pcdu' }, { type: 'escape' });
  assert.deepEqual([s.pinned, s.open], [null, true]);
  s = run(s, { type: 'escape' });
  assert.equal(s.open, false);
});

test('power path toggles; non-essential loads switch, essential loads never', () => {
  let s = run(createAnatomyState(), { type: 'toggle_power_path' });
  assert.equal(s.powerPath, true);
  s = run(s, { type: 'toggle_load', load: 'PAYLOAD' }, { type: 'toggle_load', load: 'AUX' });
  assert.deepEqual(s.loadsOn, { PAYLOAD: false, HEATER: true, AUX: true });
  for (const load of ['BUS', 'COMMS', 'ADCS']) assert.equal(run(s, { type: 'toggle_load', load }), s);
  assert.equal(run(s, { type: 'toggle_sun' }).sunlit, false);
});

test('the chip is remembered per viewer, default on, and survives broken storage', () => {
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  assert.equal(loadEnabled(storage), true);
  saveEnabled(storage, false);
  assert.equal(store.get(ENABLED_KEY), '0');
  assert.equal(loadEnabled(storage), false);
  saveEnabled(storage, true);
  assert.equal(loadEnabled(storage), true);
  const broken = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.equal(loadEnabled(broken), true);
  saveEnabled(broken, false);
  assert.equal(loadEnabled(null), true);
});

test('keyboard: A opens when the chip is on, Esc goes to the view, text fields are left alone', () => {
  const s = createAnatomyState();
  assert.deepEqual(keyAction('a', s), { type: 'open' });
  assert.equal(keyAction('a', s, true), null);
  assert.equal(keyAction('a', { ...s, enabled: false }), null);
  assert.equal(keyAction('Escape', s), null);
  assert.deepEqual(keyAction('Escape', { ...s, open: true }), { type: 'escape' });
});
