// State of the SATELLITE ANATOMY view (T37). Pure: no DOM, no three, so it is tested in Node.
// The ANATOMY chip is remembered per viewer (default ON); everything else lives only while the page is open.

export const ENABLED_KEY = 'penumbra.anatomy.enabled';
export const ESSENTIAL = ['BUS', 'COMMS', 'ADCS'];
export const SWITCHABLE = ['PAYLOAD', 'HEATER', 'AUX'];

export function loadEnabled(storage) {
  try { return storage?.getItem(ENABLED_KEY) !== '0'; } catch { return true; }
}

export function saveEnabled(storage, enabled) {
  try { storage?.setItem(ENABLED_KEY, enabled ? '1' : '0'); } catch { /* per-viewer convenience only */ }
}

export function createAnatomyState(enabled = true) {
  return { enabled, open: false, hovered: null, pinned: null, powerPath: false, sunlit: true,
    loadsOn: { PAYLOAD: true, HEATER: true, AUX: false } };
}

// The part that is lit: a pinned part wins over a hovered one.
export function focusPart(state) {
  return state.pinned ?? state.hovered;
}

export function reduceAnatomy(state, action) {
  switch (action.type) {
    case 'toggle_chip': {
      const enabled = !state.enabled;
      return { ...state, enabled, open: enabled ? state.open : false };
    }
    case 'open':
      return state.enabled ? { ...state, open: true } : state;
    case 'close':
      return { ...state, open: false, hovered: null, pinned: null };
    case 'hover':
      return { ...state, hovered: action.part ?? null };
    case 'click': {  // pin, or unpin when the same part is clicked again
      const part = action.part ?? null;
      return { ...state, pinned: part && state.pinned !== part ? part : null };
    }
    case 'escape':  // Esc unpins first, then closes
      return state.pinned ? { ...state, pinned: null } : { ...state, open: false, hovered: null };
    case 'toggle_power_path':
      return { ...state, powerPath: !state.powerPath };
    case 'toggle_sun':
      return { ...state, sunlit: !state.sunlit };
    case 'toggle_load': {
      if (!SWITCHABLE.includes(action.load)) return state;  // essential loads are always on
      return { ...state, loadsOn: { ...state.loadsOn, [action.load]: !state.loadsOn[action.load] } };
    }
    default:
      return state;
  }
}

// Keyboard shortcuts on the page: A opens (when the chip is on and focus is not in a text field), Esc as above.
export function keyAction(key, state, targetIsTextField = false) {
  if (targetIsTextField) return null;
  if (key === 'Escape' && state.open) return { type: 'escape' };
  if ((key === 'a' || key === 'A') && !state.open && state.enabled) return { type: 'open' };
  return null;
}
