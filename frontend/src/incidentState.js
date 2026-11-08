// Operator-triggered incident (T42): the judge picks up to three satellites and one fault, then starts the run.
// Pure state (node --test loads it); IncidentPicker.jsx renders it and App.jsx posts /api/incident.
//
// Server contract (issue #112): GET /api/incident/options -> { sats, faults: [{id, label}], max_incidents, live_available }
// POST /api/incident { incidents: [{ sat_id, fault }], seed } -> { started, alert_count, mode }.
// Without options (older server, 404) or without a model key (live_available false) only the recorded incident runs.
//
// T46: the recorded run stays the default. `touched` turns true once the judge changes the picker (chip, fault card or
// a voice result); only then does START INCIDENT run the custom live incident. `reset` goes back to the default.

export const MAX_INCIDENTS = 3;
export const DEFAULT_SAT = 'SAT-07';
export const FALLBACK_SATS = Array.from({ length: 24 }, (_, i) => `SAT-${String(i + 1).padStart(2, '0')}`);

export const NOTICE_NO_SERVER = 'This server runs the recorded incident only.';
export const NOTICE_NO_KEY = 'Custom incidents need a model key on this server; the recorded incident runs instead.';

// The four faults with the server's labels, shown (disabled) before options load; one effect line per card.
export const FALLBACK_FAULTS = [
  { id: 'array_degradation', label: 'Solar array degradation' },
  { id: 'stuck_on_load', label: 'Stuck-on load (AUX)' },
  { id: 'capacity_fade', label: 'Battery capacity fade' },
  { id: 'sensor_bias', label: 'SOC sensor bias' },
];
export const FAULT_EFFECTS = {
  array_degradation: 'panels lose half their output',
  stuck_on_load: 'a 25 W line stays on',
  capacity_fade: 'the battery holds a fifth',
  sensor_bias: 'the gauge reads low and drifts',
};
export const START_TAGS = { recorded: 'RECORDED · 90 s', custom: 'CUSTOM · LIVE' };

export function createIncidentState() {
  return { options: null, available: false, sats: FALLBACK_SATS, faults: [], maxIncidents: MAX_INCIDENTS,
    selected: [DEFAULT_SAT], fault: null, loaded: false, touched: false };
}

function defaultSelection(state) {
  return [state.sats.includes(DEFAULT_SAT) ? DEFAULT_SAT : state.sats[0]];
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
        selected: selected.length ? selected.slice(0, maxIncidents) : defaultSelection({ sats }),
        fault: faults.some((f) => f.id === state.fault) ? state.fault : (faults[0]?.id ?? null) };
    }
    case 'options_failed':
      return { ...state, options: null, loaded: true, available: false };
    case 'toggle_sat': {
      const id = action.satId;
      if (!state.sats.includes(id)) return state;
      if (state.selected.includes(id)) {
        return state.selected.length === 1 ? state
          : { ...state, touched: true, selected: state.selected.filter((s) => s !== id) };
      }
      if (state.selected.length >= state.maxIncidents) return state;
      return { ...state, touched: true, selected: [...state.selected, id] };
    }
    case 'set_fault':
      return state.faults.some((f) => f.id === action.fault) ? { ...state, touched: true, fault: action.fault } : state;
    case 'set_selection': {  // from a voice command: known ids only, at most maxIncidents, never empty
      const sats = (Array.isArray(action.sats) ? action.sats : []).filter((id, i, a) => state.sats.includes(id) && a.indexOf(id) === i);
      return sats.length ? { ...state, touched: true, selected: sats.slice(0, state.maxIncidents) } : state;
    }
    case 'reset':  // back to the recorded incident: SAT-07 + the first fault, untouched
      return { ...state, touched: false, selected: defaultSelection(state), fault: state.faults[0]?.id ?? null };
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

// 'custom' only when the judge changed something and the custom path is on; otherwise the recorded run.
export function startMode(state) {
  return state.touched && canStart(state) ? 'custom' : 'recorded';
}

export function faultEffect(id) {
  return FAULT_EFFECTS[id] ?? '';
}

export function requestBody(state, seed = 0) {
  if (!canStart(state)) return null;
  return { incidents: state.selected.map((sat_id) => ({ sat_id, fault: state.fault })), seed };
}

export function faultLabel(state, id = state.fault) {
  return state.faults.find((f) => f.id === id)?.label ?? id ?? '';
}
