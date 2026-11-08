export const PIN_IDLE_MS = 10_000;

export function createPinState() {
  return { parked: false, idleAt: null, fly: false };
}

export function reducePin(state, event, now) {
  if (event === 'toggle') return state.parked
    ? { ...createPinState(), fly: true }
    : { parked: true, idleAt: now + PIN_IDLE_MS, fly: true };
  if (event === 'activity' && state.parked) return { ...state, idleAt: now + PIN_IDLE_MS, fly: false };
  if (event === 'tick' && state.parked && now >= state.idleAt) return createPinState();
  if (event === 'tick' && state.parked) return { ...state, fly: false };
  return state;
}
