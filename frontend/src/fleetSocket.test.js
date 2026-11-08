import assert from 'node:assert/strict';
import test from 'node:test';
import { connectFleetSocket } from './fleetSocket.js';

test('socket reconnect retains the last fleet until a fresh frame arrives', () => {
  const sockets = [];
  const timers = [];
  class Socket {
    constructor(url) { this.url = url; sockets.push(this); }
    close() { this.onclose?.(); }
  }
  let fleet = { t: 1, sats: ['SAT-07'] };
  let reconnecting = false;
  const stop = connectFleetSocket('/ws', (event) => {
    fleet = event.data;
    reconnecting = false;
  }, () => { reconnecting = true; }, {
    Socket,
    schedule: (callback, delay) => { timers.push({ callback, delay }); return timers.length; },
    cancel: () => {},
  });
  sockets[0].onmessage({ data: { t: 2, sats: ['SAT-07'] } });
  sockets[0].close();
  assert.equal(reconnecting, true);
  assert.equal(fleet.t, 2);
  assert.equal(timers[0].delay, 1000);
  timers[0].callback();
  assert.equal(sockets.length, 2);
  assert.equal(reconnecting, true);
  sockets[1].onmessage({ data: { t: 3, sats: ['SAT-07'] } });
  assert.equal(reconnecting, false);
  assert.equal(fleet.t, 3);
  stop();
  assert.equal(timers.length, 1);
});
