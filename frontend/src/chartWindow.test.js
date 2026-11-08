import assert from 'node:assert/strict';
import test from 'node:test';
import { chartWindow, clipSeries } from './chartWindow.js';

const trajectory = { t: [1000, 4600, 8200, 11800, 15400, 19000] };

test('without execution the chart keeps the entire verifier horizon', () => {
  assert.deepEqual(chartWindow(trajectory, null, 9000, null), { first: 1000, last: 19000 });
});

test('flight window starts at execution and slides with the sim clock plus one orbit', () => {
  assert.deepEqual(chartWindow(trajectory, 4600, 4600, null), { first: 4600, last: 10300 });
  assert.deepEqual(chartWindow(trajectory, 4600, 8200, null), { first: 4600, last: 13900 });
});

test('reveal freezes the flight window and both ends remain inside the trajectory', () => {
  assert.deepEqual(chartWindow(trajectory, 4600, 15000, 8200), { first: 4600, last: 13900 });
  assert.deepEqual(chartWindow(trajectory, -100, 30000, null), { first: 1000, last: 19000 });
  assert.deepEqual(chartWindow(trajectory, 30000, 30000, null), { first: 19000, last: 19000 });
});

test('series clipping interpolates window edges and excludes out-of-window samples', () => {
  assert.deepEqual(clipSeries([0, 10, 20], [0, .5, 1], 5, 15), [
    { t: 5, value: .25 }, { t: 10, value: .5 }, { t: 15, value: .75 },
  ]);
  assert.deepEqual(clipSeries([0, 10, 20], [0, .5, 1], 25, 30), []);
});
