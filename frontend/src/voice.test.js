import assert from 'node:assert/strict';
import test from 'node:test';
import { createVoicePlayer, loadMuted, MUTE_KEY, saveMuted, shouldSpeak } from './voice.js';

function fakeAudio(log, { fail = false } = {}) {
  return (url) => ({
    url,
    play() { log.push(`play ${url}`); return fail ? Promise.reject(new Error('autoplay blocked')) : Promise.resolve(); },
    pause() { log.push(`pause ${url}`); },
  });
}

test('only a callout with an audio_url is spoken, and never when muted', () => {
  assert.equal(shouldSpeak({ text: 'x', audio_url: '/audio/ab.mp3' }, false), true);
  assert.equal(shouldSpeak({ text: 'x', audio_url: '/audio/ab.mp3' }, true), false);
  assert.equal(shouldSpeak({ text: 'captions only' }, false), false);
  assert.equal(shouldSpeak({ text: 'x', audio_url: null }, false), false);
});

test('a new callout stops the clip still speaking', () => {
  const log = [];
  const player = createVoicePlayer(fakeAudio(log));
  assert.equal(player.play('/audio/a.mp3'), true);
  player.play('/audio/b.mp3');
  player.stop();
  assert.deepEqual(log, ['play /audio/a.mp3', 'pause /audio/a.mp3', 'play /audio/b.mp3', 'pause /audio/b.mp3']);
  assert.equal(player.play(''), false);
});

test('blocked autoplay or a broken Audio never throws', async () => {
  const player = createVoicePlayer(fakeAudio([], { fail: true }));
  assert.equal(player.play('/audio/a.mp3'), true);
  await new Promise((resolve) => setTimeout(resolve, 0));  // the rejected play() promise is handled
  const broken = createVoicePlayer(() => { throw new Error('no Audio in this browser'); });
  assert.equal(broken.play('/audio/a.mp3'), false);
});

test('mute is remembered per viewer and survives unavailable storage', () => {
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  assert.equal(loadMuted(storage), false);
  saveMuted(storage, true);
  assert.equal(store.get(MUTE_KEY), '1');
  assert.equal(loadMuted(storage), true);
  const throwing = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.equal(loadMuted(throwing), false);
  saveMuted(throwing, true);
  assert.equal(loadMuted(null), false);
});
