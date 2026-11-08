// Push-to-talk for the incident picker (T43): hold Ctrl+Alt (or the mic button), speak, release. The clip goes
// to POST /api/voice-command, Gemini returns { transcript, sats, fault, notes } and the picker fills itself.
// Pure state here (node --test); PushToTalk.jsx owns the browser APIs (getUserMedia, MediaRecorder, fetch).

export const MAX_CLIP_S = 15;
export const CHORD_KEYS = ['Control', 'Alt'];

export const LINE_INSECURE = 'Voice needs HTTPS or the laptop (microphone blocked on plain http).';
export const LINE_NO_RECORDER = 'Voice needs a browser with MediaRecorder.';
export const LINE_NO_MIC = 'Microphone permission was refused.';
export const LINE_SERVER_OFF = 'Voice is off on this server.';
export const LINE_HINT = 'Hold Ctrl+Alt or the mic and say the incident.';

export function createVoiceState() {
  return { support: null, status: 'idle', held: { Control: false, Alt: false }, startedAt: null, elapsed: 0,
    transcript: null, notes: null, error: null, lastResult: null };
}

// Browser capability, computed once by the component (pure so it can be tested).
export function detectSupport({ isSecureContext, hasMediaDevices, hasMediaRecorder }) {
  if (!isSecureContext) return { ok: false, line: LINE_INSECURE };
  if (!hasMediaDevices || !hasMediaRecorder) return { ok: false, line: LINE_NO_RECORDER };
  return { ok: true, line: LINE_HINT };
}

export function canRecord(state) {
  return Boolean(state.support?.ok) && (state.status === 'idle' || state.status === 'done' || state.status === 'error');
}

export function reduceVoice(state, action) {
  switch (action.type) {
    case 'support':
      return { ...state, support: action.support };
    case 'keydown': {
      if (!CHORD_KEYS.includes(action.key)) return state;
      return { ...state, held: { ...state.held, [action.key]: true } };
    }
    case 'keyup': {
      if (!CHORD_KEYS.includes(action.key)) return state;
      return { ...state, held: { ...state.held, [action.key]: false } };
    }
    case 'blur':
      return { ...state, held: { Control: false, Alt: false } };
    case 'start':
      if (!canRecord(state)) return state;
      return { ...state, status: 'listening', startedAt: action.now, elapsed: 0, error: null, transcript: null, notes: null };
    case 'tick':
      if (state.status !== 'listening') return state;
      return { ...state, elapsed: Math.max(0, (action.now - state.startedAt) / 1000) };
    case 'stop':
      return state.status === 'listening' ? { ...state, status: 'thinking' } : state;
    case 'cancel':
      return state.status === 'listening' ? { ...state, status: 'idle', startedAt: null, elapsed: 0 } : state;
    case 'result': {
      const r = action.result ?? {};
      return { ...state, status: 'done', transcript: typeof r.transcript === 'string' ? r.transcript : '',
        notes: typeof r.notes === 'string' ? r.notes : null, lastResult: r, error: null };
    }
    case 'error':
      return { ...state, status: 'error', error: action.message || 'Voice command failed.' };
    default:
      return state;
  }
}

// Both chord keys down and not already recording -> start; any chord key up while recording -> stop.
export function chordTransition(before, after) {
  const was = before.held.Control && before.held.Alt;
  const now = after.held.Control && after.held.Alt;
  if (!was && now && canRecord(after)) return 'start';
  if (was && !now && after.status === 'listening') return 'stop';
  return null;
}

export function autoStopDue(state, now) {
  return state.status === 'listening' && state.startedAt != null && now - state.startedAt >= MAX_CLIP_S * 1000;
}

// The server's answer, reduced to picker actions. Unknown ids are dropped; nothing starts by itself.
export function pickerActionsFor(result, incident) {
  const actions = [];
  const sats = Array.isArray(result?.sats) ? result.sats.filter((s) => incident.sats.includes(s)) : [];
  if (sats.length) actions.push({ type: 'set_selection', sats: sats.slice(0, incident.maxIncidents) });
  if (typeof result?.fault === 'string' && incident.faults.some((f) => f.id === result.fault)) {
    actions.push({ type: 'set_fault', fault: result.fault });
  }
  return actions;
}

export function statusLine(state) {
  if (state.support && !state.support.ok) return state.support.line;
  switch (state.status) {
    case 'listening': return `LISTENING · ${Math.floor(state.elapsed)} s · release to send`;
    case 'thinking': return 'THINKING…';
    case 'done': return state.transcript ? `“${state.transcript}”${state.notes ? ` · ${state.notes}` : ''}` : (state.notes || 'Nothing heard.');
    case 'error': return state.error;
    default: return state.support?.line ?? LINE_HINT;
  }
}

export function errorLineFor(status) {
  if (status === 503) return LINE_SERVER_OFF;
  if (status === 413) return 'Clip too long; keep it under 15 seconds.';
  if (status === 404) return LINE_SERVER_OFF;
  return null;
}
