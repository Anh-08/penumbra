const MAX_TRACE_ROWS = 40;

export function createPanelState() {
  return {
    alerts: [],
    hypotheses: {},
    trace: [],
    callout: null,
    phase: 'attract',
    mode: 'SIMULATED PREVIEW',
  };
}

export function formatCountdown(aos, now) {
  if (aos == null || !Number.isFinite(aos) || !Number.isFinite(now)) return '—';
  const seconds = Math.max(0, Math.floor(aos - now));
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

export function formatDuration(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  const rounded = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  return hours > 0 ? `${hours}h ${String(minutes).padStart(2, '0')}m` : `${minutes}m`;
}

function compact(value) {
  if (value == null) return '—';
  if (typeof value === 'string') return value;
  if (typeof value !== 'object') return String(value);
  if ('error' in value) return `ERROR: ${compact(value.error)}`;
  for (const key of ['plan_id', 'sim_id', 'approval_id', 'status']) {
    if (key in value) return String(value[key]);
  }
  return JSON.stringify(value);
}

function toolName(payload) {
  return payload.tool ?? payload.tool_name ?? payload.name ?? payload.call?.name ?? 'tool';
}

function toolArgs(payload) {
  return compact(payload.args ?? payload.arguments ?? payload.call?.args ?? {});
}

function toolResult(payload) {
  return compact(payload.result ?? payload.output ?? payload.response ?? payload.error ?? payload);
}

function reduceTrace(trace, event) {
  const payload = event.payload ?? {};
  const tool = toolName(payload);
  if (event.kind === 'tool_call') {
    const row = {
      id: `${event.run_id}:${event.seq}`,
      ts: event.ts,
      tool,
      args: toolArgs(payload),
      result: '—',
      kind: 'tool',
    };
    return [...trace, row].slice(-MAX_TRACE_ROWS);
  }
  if (event.kind === 'tool_result') {
    const index = trace.findLastIndex((row) => (tool === 'tool' || row.tool === tool) && row.result === '—');
    if (index >= 0) {
      const rows = [...trace];
      rows[index] = { ...rows[index], result: toolResult(payload) };
      return rows;
    }
    return [...trace, {
      id: `${event.run_id}:${event.seq}`, ts: event.ts, tool,
      args: '—', result: toolResult(payload), kind: 'tool',
    }].slice(-MAX_TRACE_ROWS);
  }
  return trace;
}

function updateAlertStatus(alerts, satId, status) {
  return alerts.map((alert) => alert.sat_id === satId ? { ...alert, status } : alert);
}

export function reducePanel(state, message) {
  if (!message || !message.type || !message.data) return state;
  const data = message.data;
  switch (message.type) {
    case 'alert': {
      const index = state.alerts.findIndex((alert) => alert.alert_id === data.alert_id);
      const alerts = [...state.alerts];
      const updated = { ...data, status: alerts[index]?.status ?? 'queued' };
      if (index >= 0) alerts[index] = updated;
      else alerts.push(updated);
      return { ...state, alerts, phase: state.phase === 'attract' ? 'storm' : state.phase };
    }
    case 'hypotheses':
      return { ...state, hypotheses: { ...state.hypotheses, [data.sat_id]: data } };
    case 'trace_event':
      return { ...state, trace: reduceTrace(state.trace, data),
        phase: state.phase === 'storm' && data.kind === 'tool_call' ? 'agent' : state.phase };
    case 'callout':
      return { ...state, callout: data.text };
    case 'demo_state':
      return { ...state, phase: data.state };
    case 'approval_request':
      return { ...state, alerts: updateAlertStatus(state.alerts, data.sat_id, 'awaiting_approval'), phase: 'approval' };
    case 'execution':
      return { ...state, alerts: updateAlertStatus(state.alerts, data.sat_id, 'executed') };
    case 'fleet_state': {
      const statuses = new Map(data.sats.filter((sat) => [
        'queued', 'working', 'auto_executed', 'awaiting_approval', 'executed', 'safe_mode_recommended',
      ].includes(sat.status)).map((sat) => [sat.sat_id, sat.status]));
      return { ...state, alerts: state.alerts.map((alert) => statuses.has(alert.sat_id)
        ? { ...alert, status: statuses.get(alert.sat_id) } : alert) };
    }
    default:
      return state;
  }
}
