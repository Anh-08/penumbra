// Operator-triggered incident (T42): the judge picks up to three satellites and one fault, then starts the run.
// Pure state (node --test loads it); IncidentPicker.jsx renders it and App.jsx posts /api/incident.
//
// Server contract (issue #112): GET /api/incident/options -> { sats, faults: [{id, label}], max_incidents, live_available }
// POST /api/incident { incidents: [{ sat_id, fault }], seed } -> { started, alert_count, mode }.
// Without options (older server, 404) or without a model key (live_available false) only the recorded incident runs.

export const MAX_INCIDENTS = 3;
export const DEFAULT_SAT = 'SAT-07';
export const FALLBACK_SATS = Array.from({ length: 24 }, (_, i) => `SAT-${String(i + 1).padStart(2, '0')}`);

export const NOTICE_NO_SERVER = 'This server runs the recorded incident only.';
export const NOTICE_NO_KEY = 'Custom incidents need a model key on this server; the recorded incident runs instead.';

export function createIncidentState() {
  return { options: null, available: false, sats: FALLBACK_SATS, faults: [], maxIncidents: MAX_INCIDENTS,
    selected: [DEFAULT_SAT], fault: null, loaded: false };
}

export function reduceIncident(state, action) {
  switch (action.type) {
    case 'options_loaded': {
      const o = action.options ?? {};
      const faults = Array.isArray(o.faults) ? o.faults.filter((f) => f && typeof f.id === 'string') : [];
      const sats = Array.isArray(o.sats) && o.sats.length ? o.sats.filter((s) => /^SAT-\d{2}$/.test(s)) : FALLBACK_SATS;
      const maxIncidents = Number.isInteger(o.max_incidents) && o.max_incidents > 0 ? o.max_incidents : MAX_INCIDENTS;
      const available = Boolean(o.live_available) && faults.length > 0;
      const selected = state.selected.filter((s) => sats.includes(s));
      return { ...state, options: o, loaded: true, available, sats, faults, maxIncidents,
        selected: selected.length ? selected.slice(0, maxIncidents) : [sats.includes(DEFAULT_SAT) ? DEFAULT_SAT : sats[0]],
        fault: faults.some((f) => f.id === state.fault) ? state.fault : (faults[0]?.id ?? null) };
    }
    case 'options_failed':
      return { ...state, options: null, loaded: true, available: false };
    case 'toggle_sat': {
      const id = action.satId;
      if (!state.sats.includes(id)) return state;
      if (state.selected.includes(id)) {
        return state.selected.length === 1 ? state : { ...state, selected: state.selected.filter((s) => s !== id) };
      }
      if (state.selected.length >= state.maxIncidents) return state;
      return { ...state, selected: [...state.selected, id] };
    }
    case 'set_fault':
      return state.faults.some((f) => f.id === action.fault) ? { ...state, fault: action.fault } : state;
    case 'set_selection': {  // from a voice command: known ids only, at most maxIncidents, never empty
      const sats = (Array.isArray(action.sats) ? action.sats : []).filter((id, i, a) => state.sats.includes(id) && a.indexOf(id) === i);
      return sats.length ? { ...state, selected: sats.slice(0, state.maxIncidents) } : state;
    }
    default:
      return state;
  }
}

// One line under the picker when the custom path is off; null when it is on.
export function noticeFor(state) {
  if (state.available) return null;
  if (state.loaded && state.options && state.options.live_available === false) return NOTICE_NO_KEY;
  return NOTICE_NO_SERVER;
}

export function canStart(state) {
  return state.available && state.selected.length >= 1 && state.selected.length <= state.maxIncidents
    && state.faults.some((f) => f.id === state.fault);
}

export function requestBody(state, seed = 0) {
  if (!canStart(state)) return null;
  return { incidents: state.selected.map((sat_id) => ({ sat_id, fault: state.fault })), seed };
}

export function faultLabel(state, id = state.fault) {
  return state.faults.find((f) => f.id === id)?.label ?? id ?? '';
}
