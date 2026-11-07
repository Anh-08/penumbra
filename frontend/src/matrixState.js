export function createMatrixState() {
  return { bySat: {}, planSat: {}, statusBySat: {}, schedule: null };
}

export function rankedPlanId(matrix) {
  return matrix?.complete ? matrix.results[0]?.plan_id ?? null : null;
}

function matrixFor(state, satId) {
  return state.bySat[satId] ?? {
    results: [], complete: false, freshPlanId: null, sequence: 0,
    approvalPlanId: null,
  };
}

export function scheduleConflicts(schedule, planSat) {
  if (!schedule) return null;
  const byStation = new Map();
  for (const assignment of schedule.assignments) {
    const satId = planSat[assignment.plan_id];
    if (!satId) continue;
    const windows = byStation.get(assignment.station_id) ?? [];
    windows.push({ ...assignment, sat_id: satId });
    byStation.set(assignment.station_id, windows);
  }
  const groups = [...byStation].map(([station_id, windows]) => ({ station_id, windows }));
  const sharedStations = groups.filter(({ windows }) => new Set(windows.map((window) => window.sat_id)).size >= 2);
  const unscheduled = schedule.unscheduled
    .map((plan_id) => ({ plan_id, sat_id: planSat[plan_id] }))
    .filter(({ sat_id }) => sat_id && schedule.assignments.some((assignment) =>
      planSat[assignment.plan_id] && planSat[assignment.plan_id] !== sat_id));
  if (sharedStations.length === 0 && unscheduled.length === 0) return null;
  return { stations: sharedStations, unscheduled };
}

export function reduceMatrix(state, message) {
  if (!message?.type || !message.data) return state;
  const data = message.data;
  if (message.type === 'matrix') {
    const previous = matrixFor(state, data.sat_id);
    let results = [...previous.results];
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
    if (data.complete) {
      const rankedIds = new Set(data.results.map((result) => result.plan_id));
      results = [...data.results, ...results.filter((result) => !rankedIds.has(result.plan_id))];
    }
    const planSat = { ...state.planSat };
    for (const result of data.results ?? []) planSat[result.plan_id] = data.sat_id;
    return { ...state, planSat, bySat: { ...state.bySat, [data.sat_id]: {
      ...previous, results, complete: previous.complete || data.complete,
      freshPlanId, sequence: previous.sequence + 1,
    } } };
  }
  if (message.type === 'fleet_state') {
    const statusBySat = { ...state.statusBySat };
    for (const sat of data.sats ?? []) statusBySat[sat.sat_id] = sat.status;
    return { ...state, statusBySat };
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
