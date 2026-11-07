export function createMatrixState() {
  return { bySat: {}, schedule: null };
}

function matrixFor(state, satId) {
  return state.bySat[satId] ?? {
    results: [], complete: false, freshPlanId: null, sequence: 0,
    rank: null, approvalPlanId: null,
  };
}

function rankFromTrace(event) {
  if (event.kind !== 'tool_result') return null;
  const payload = event.payload ?? {};
  const rank = payload.rank ?? payload.result?.rank ?? payload.result;
  if (!Array.isArray(rank?.ordered) || !['feasible', 'infeasible'].includes(rank.status)) return null;
  const satId = payload.sat_id ?? payload.args?.sat_id ?? payload.result?.sat_id;
  if (!satId) return null;
  return { satId, rank: { ordered: rank.ordered, status: rank.status } };
}

export function reduceMatrix(state, message) {
  if (!message?.type || !message.data) return state;
  const data = message.data;
  if (message.type === 'matrix') {
    const previous = matrixFor(state, data.sat_id);
    const results = [...previous.results];
    let freshPlanId = null;
    for (const incoming of data.results ?? []) {
      const index = results.findIndex((result) => result.plan_id === incoming.plan_id);
      if (index < 0) {
        results.push(incoming);
        freshPlanId = incoming.plan_id;
      } else {
        results[index] = incoming;
      }
    }
    return { ...state, bySat: { ...state.bySat, [data.sat_id]: {
      ...previous, results, complete: previous.complete || data.complete,
      freshPlanId, sequence: previous.sequence + 1,
    } } };
  }
  if (message.type === 'trace_event') {
    const signal = rankFromTrace(data);
    if (!signal) return state;
    const previous = matrixFor(state, signal.satId);
    return { ...state, bySat: { ...state.bySat, [signal.satId]: {
      ...previous, rank: signal.rank,
    } } };
  }
  if (message.type === 'approval_request') {
    const previous = matrixFor(state, data.sat_id);
    return { ...state, bySat: { ...state.bySat, [data.sat_id]: {
      ...previous, approvalPlanId: data.plan_id,
    } } };
  }
  if (message.type === 'schedule') return { ...state, schedule: data };
  return state;
}
