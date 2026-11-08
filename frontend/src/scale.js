// Scale slider (T28): the B4 benchmark (bench/results/b4.json, served by GET /api/bench) at 24, 1k and 10k
// satellites. These are offline, headless measurements on a named machine, not live numbers.

export const SCALE_STOPS = [24, 1000, 10000];

export function b4From(bench) {
  return bench?.results?.['b4.json'] ?? null;
}

export function stopLabel(satellites) {
  return satellites >= 1000 ? `${satellites / 1000}k` : String(satellites);
}

export function scaleRow(b4, satellites) {
  return (b4?.planning_scale ?? []).find((row) => row.satellites === satellites) ?? null;
}

export function schedulerRow(b4) {
  const rows = b4?.scheduler ?? [];
  return rows.reduce((best, row) => (!best || row.requests > best.requests ? row : best), null);
}

export function machineLabel(b4) {
  const machine = b4?.machine;
  if (!machine) return 'machine not recorded';
  const platform = String(machine.platform ?? '');
  const os = platform.split('-')[0] || 'unknown OS';
  const arch = (platform.match(/x86_64|aarch64|arm64|amd64/) ?? [''])[0];
  return [os + (arch ? ` ${arch}` : ''), machine.cpus ? `${machine.cpus} CPUs` : null,
    machine.python ? `Python ${machine.python}` : null].filter(Boolean).join(' · ');
}

export function formatRate(evalsPerSecond) {
  if (!Number.isFinite(evalsPerSecond)) return '—';
  return `${(evalsPerSecond / 1e6).toFixed(1)} M evals/s`;
}

export function formatSeconds(seconds) {
  if (!Number.isFinite(seconds)) return '—';
  return seconds < 1 ? `${Math.round(seconds * 1000)} ms` : `${seconds.toFixed(1)} s`;
}

export function solverLabel(solver) {
  return solver === 'cpsat' ? 'CP-SAT' : String(solver ?? '').toUpperCase();
}
