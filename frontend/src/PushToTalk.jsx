// Push-to-talk (T43): hold Ctrl+Alt or the mic button, speak, release. Owns the browser APIs; the state and the
// picker mapping live in voiceCommand.js. Nothing starts by itself: the result only fills the picker.
import { useEffect, useReducer, useRef } from 'react';
import {
  autoStopDue, canRecord, chordTransition, createVoiceState, detectSupport, errorLineFor, pickerActionsFor,
  reduceVoice, statusLine,
} from './voiceCommand.js';

const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/ogg'];

function pickMime() {
  if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) return '';
  return MIME_CANDIDATES.find((m) => MediaRecorder.isTypeSupported(m)) ?? '';
}

export function PushToTalk({ incident, dispatchIncident, disabled }) {
  const [state, dispatch] = useReducer(reduceVoice, undefined, createVoiceState);
  const stateRef = useRef(state);
  stateRef.current = state;
  const recorder = useRef(null);
  const stream = useRef(null);
  const chunks = useRef([]);

  useEffect(() => {
    dispatch({ type: 'support', support: detectSupport({
      isSecureContext: typeof window !== 'undefined' && window.isSecureContext,
      hasMediaDevices: Boolean(navigator.mediaDevices?.getUserMedia),
      hasMediaRecorder: typeof MediaRecorder !== 'undefined',
    }) });
  }, []);

  async function start() {
    if (!canRecord(stateRef.current) || disabled) return;
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch { dispatch({ type: 'error', message: 'Microphone permission was refused.' }); return; }
    const mime = pickMime();
    const rec = new MediaRecorder(stream.current, mime ? { mimeType: mime } : undefined);
    chunks.current = [];
    rec.ondataavailable = (event) => { if (event.data?.size) chunks.current.push(event.data); };
    rec.onstop = () => send(rec.mimeType || mime || 'audio/webm');
    recorder.current = rec;
    rec.start();
    dispatch({ type: 'start', now: performance.now() });
  }

  function stop() {
    const rec = recorder.current;
    if (!rec) return;
    recorder.current = null;
    dispatch({ type: 'stop' });
    try { if (rec.state !== 'inactive') rec.stop(); } catch { /* already stopped */ }
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
  }

  async function send(mime) {
    const blob = new Blob(chunks.current, { type: mime });
    chunks.current = [];
    if (!blob.size) { dispatch({ type: 'error', message: 'Nothing recorded.' }); return; }
    const form = new FormData();
    form.append('audio', blob, mime.includes('ogg') ? 'clip.ogg' : 'clip.webm');
    try {
      const response = await fetch('/api/voice-command', { method: 'POST', body: form });
      if (!response.ok) {
        let detail = null;
        try { detail = (await response.json()).detail; } catch { /* no body */ }
        dispatch({ type: 'error', message: errorLineFor(response.status) || (typeof detail === 'string' ? detail : `Voice command failed (${response.status}).`) });
        return;
      }
      const result = await response.json();
      dispatch({ type: 'result', result });
      for (const action of pickerActionsFor(result, incident)) dispatchIncident(action);
    } catch { dispatch({ type: 'error', message: 'Voice command failed (network).' }); }
  }

  // Ctrl+Alt chord on the window; blur releases it. Ignore while typing in a field.
  useEffect(() => {
    const typing = (target) => target instanceof HTMLElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
    const onKey = (type) => (event) => {
      if (typing(event.target) && type === 'keydown') return;
      const before = stateRef.current;
      const after = reduceVoice(before, { type, key: event.key });
      if (after === before) return;
      dispatch({ type, key: event.key });
      const next = chordTransition(before, after);
      if (next === 'start') { event.preventDefault(); start(); }
      if (next === 'stop') stop();
    };
    const down = onKey('keydown');
    const up = onKey('keyup');
    const blur = () => { dispatch({ type: 'blur' }); if (stateRef.current.status === 'listening') stop(); };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    window.addEventListener('blur', blur);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('blur', blur);
    };
  }, [incident, disabled]);

  // Elapsed seconds and the 15 s auto-stop.
  useEffect(() => {
    if (state.status !== 'listening') return undefined;
    const timer = window.setInterval(() => {
      const now = performance.now();
      dispatch({ type: 'tick', now });
      if (autoStopDue(stateRef.current, now)) stop();
    }, 250);
    return () => window.clearInterval(timer);
  }, [state.status]);

  const listening = state.status === 'listening';
  const ok = Boolean(state.support?.ok) && !disabled;
  return <div className={`push-to-talk ${state.status}`}>
    <button type="button" className="mic-button" aria-pressed={listening} disabled={!ok || state.status === 'thinking'}
      title="Hold to speak (or hold Ctrl+Alt)"
      onPointerDown={(event) => { event.preventDefault(); start(); }}
      onPointerUp={stop} onPointerLeave={() => { if (listening) stop(); }} onPointerCancel={stop}>
      <span className="mic-dot" aria-hidden="true" />{listening ? 'RELEASE TO SEND' : 'HOLD TO SPEAK'}
    </button>
    <div className="voice-line" aria-live="polite">{statusLine(state)}</div>
  </div>;
}
