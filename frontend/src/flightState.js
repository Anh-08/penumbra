export function createFlightState() {
  return {
    approval: null,
    refusal: null,
    execution: null,
    trajectory: null,
    telemetry: {},
    reveals: {},
  };
}

export function reduceFlight(state, message) {
  if (!message?.type || !message.data) return state;
  const data = message.data;
  if (message.type === 'approval_request') {
    return { ...state, approval: data, refusal: null };
  }
  if (message.type === 'clear_refusal') return { ...state, refusal: null };
  if (message.type === 'approval_refused') {
    if (state.approval && data.approval_id !== state.approval.approval_id) return state;
    return { ...state, refusal: data };
  }
  if (message.type === 'execution') return { ...state, execution: data, refusal: null };
  if (message.type === 'trajectory') return { ...state, trajectory: data };
  if (message.type === 'reveal') {
    return { ...state, reveals: { ...state.reveals, [data.sat_id]: data } };
  }
  if (message.type === 'fleet_state') {
    const telemetry = { ...state.telemetry };
    for (const sat of data.sats ?? []) {
      if (!Number.isFinite(sat.soc)) continue;
      const previous = telemetry[sat.sat_id] ?? [];
      if (previous.at(-1)?.t === data.t) continue;
      telemetry[sat.sat_id] = [...previous, { t: data.t, soc: sat.soc }].slice(-2000);
    }
    return { ...state, telemetry };
  }
  return state;
}

export function approvalPayload(approval, result) {
  if (!approval?.approval_id || !result?.plan_hash) return null;
  return { approval_id: approval.approval_id, plan_hash: result.plan_hash };
}

export function hashPrefix(planHash) {
  return typeof planHash === 'string' ? planHash.slice(0, 8) : '—';
}

export function observedPoints(trajectory, telemetry) {
  if (!trajectory?.t?.length) return [];
  const first = trajectory.t[0];
  const last = trajectory.t.at(-1);
  return (telemetry ?? []).filter((point) =>
    Number.isFinite(point.t) && Number.isFinite(point.soc)
    && point.t >= first && point.t <= last);
}

export function advanceEclipse(step, illum) {
  if (!step || !Number.isFinite(illum) || step.stopping) return { step, complete: false };
  if (illum <= .05) return { step: { ...step, entered: true }, complete: false };
  if (step.entered && illum >= .95) return { step: { ...step, stopping: true }, complete: true };
  return { step, complete: false };
}
