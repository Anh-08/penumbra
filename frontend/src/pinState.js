export const PIN_IDLE_MS = 10_000;

export function createPinState() {
  return { parked: false, idleAt: null };
}

export function reducePin(state, event, now) {
  if (event === 'toggle') return state.parked
    ? createPinState()
    : { parked: true, idleAt: now + PIN_IDLE_MS };
  if (event === 'activity' && state.parked) return { ...state, idleAt: now + PIN_IDLE_MS };
  if (event === 'tick' && state.parked && now >= state.idleAt) return createPinState();
  return state;
}
