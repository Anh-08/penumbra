import assert from 'node:assert/strict';
import test from 'node:test';
import { PIN_IDLE_MS, createPinState, reducePin } from './pinState.js';

test('Buffalo pin toggles park and releases after ten idle seconds', () => {
  const initial = createPinState();
  const parked = reducePin(initial, 'toggle', 1_000);
  assert.deepEqual(parked, { parked: true, idleAt: 1_000 + PIN_IDLE_MS });
  assert.equal(reducePin(parked, 'tick', 10_999).parked, true);
  assert.deepEqual(reducePin(parked, 'tick', 11_000), initial);
  assert.deepEqual(reducePin(parked, 'toggle', 2_000), initial);
});

test('drag activity extends park while idle ticks do not', () => {
  const parked = reducePin(createPinState(), 'toggle', 0);
  const dragged = reducePin(parked, 'activity', 8_000);
  assert.equal(dragged.idleAt, 18_000);
  assert.equal(reducePin(dragged, 'tick', 10_000).parked, true);
  assert.equal(reducePin(dragged, 'tick', 18_000).parked, false);
});
