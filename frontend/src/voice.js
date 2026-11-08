// Voice callouts (T28): play the cached ElevenLabs clip of a `callout` when it has an audio_url, unless muted.
// Captions are always shown; voice is an extra. Browser storage may be unavailable, so every access is guarded.

export const MUTE_KEY = 'penumbra.voice.muted';

export function loadMuted(storage) {
  try { return storage?.getItem(MUTE_KEY) === '1'; } catch { return false; }
}

export function saveMuted(storage, muted) {
  try { storage?.setItem(MUTE_KEY, muted ? '1' : '0'); } catch { /* per-viewer convenience only */ }
}

export function shouldSpeak(callout, muted) {
  return !muted && typeof callout?.audio_url === 'string' && callout.audio_url.length > 0;
}

// One clip at a time: a new callout replaces the one still speaking, like the caption line.
export function createVoicePlayer(makeAudio) {
  let current = null;
  return {
    play(url) {
      if (!url) return false;
      try {
        current?.pause?.();
        current = makeAudio(url);
        const started = current.play?.();
        started?.catch?.(() => {});  // autoplay blocked or file missing: the caption still shows
        return true;
      } catch { current = null; return false; }
    },
    stop() {
      try { current?.pause?.(); } catch { /* nothing to stop */ }
      current = null;
    },
  };
}
