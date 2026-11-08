// Plan and approval ids restart per satellite in a storm (each satellite has its own agent run), so tickets
// are keyed by satellite and plan.
export function ticketKey(satId, planId) {
  return `${satId}/${planId}`;
}

export function createFlightState() {
  return {
    approvals: {},
    refusal: null,
    execution: null,
    approved: null,  // the last plan a human approved (execution status "executed"), not a Tier 1 auto-execution
    trajectory: null,
    telemetry: {},
    reveals: {},
  };
}

export function reduceFlight(state, message) {
  if (!message?.type || !message.data) return state;
  const data = message.data;
  if (message.type === 'approval_request') {
    return { ...state, approvals: { ...state.approvals, [ticketKey(data.sat_id, data.plan_id)]: data }, refusal: null };
  }
  if (message.type === 'clear_refusal') return { ...state, refusal: null };
  if (message.type === 'approval_refused') {
    if (!Object.values(state.approvals).some((ticket) => ticket.approval_id === data.approval_id)) return state;
    return { ...state, refusal: data };
  }
  if (message.type === 'execution') {
    return { ...state, execution: data, approved: data.status === 'executed' ? data : state.approved, refusal: null };
  }
  if (message.type === 'trajectory') return { ...state, trajectory: data };
  if (message.type === 'reveal') {
    return { ...state, reveals: { ...state.reveals, [data.sat_id]: data } };
  }
  if (message.type === 'trace_event' && data.kind === 'tool_result'
    && data.payload?.name === 'get_telemetry') {
    const result = data.payload.result;
    if (!result?.sat_id || !Array.isArray(result.t) || !Array.isArray(result.channels?.soc)) return state;
    const points = result.t.flatMap((t, index) =>
      Number.isFinite(t) && Number.isFinite(result.channels.soc[index])
        ? [{ t, soc: result.channels.soc[index] }] : []);
    return { ...state, telemetry: { ...state.telemetry, [result.sat_id]: points } };
  }
  return state;
}

// What the flight overlay, the reveal call and the Flight Director follow. In fastforward and reveal that is the
// satellite and plan the human approved (the director fast-forwards that one), whatever satellite the judge
// clicked afterwards (e.g. a red SAT-19 column for the physics veto). Elsewhere it is the selected satellite.
export function flightFocus({ phase, selected, selectedPlanId, flight, matrixBySat }) {
  const approved = flight?.approved;
  const follow = (phase === 'fastforward' || phase === 'reveal') && approved;
  const satId = follow ? approved.sat_id : selected;
  const matrix = matrixBySat?.[satId];
  const planId = follow ? approved.plan_id : selectedPlanId;
  const result = matrix?.results.find((r) => r.plan_id === planId) ?? (follow ? null : matrix?.results[0]) ?? null;
  const approval = result ? flight?.approvals?.[ticketKey(satId, result.plan_id)] ?? null : null;
  return { satId, following: Boolean(follow), result, approval,
    chartResult: follow ? result : (matrix?.complete ? matrix.results[0] : null) };
}

export function approvalPayload(approval, result) {
  if (!approval?.approval_id || !result?.plan_hash
    || approval.plan_id !== result.plan_id || approval.plan_hash !== result.plan_hash) return null;
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
