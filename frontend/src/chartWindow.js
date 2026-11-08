export const ORBIT_S = 5700;

export function chartWindow(trajectory, executionT, nowT, revealT) {
  const times = trajectory?.t;
  if (!Array.isArray(times) || times.length === 0) return null;
  const horizonFirst = times[0];
  const horizonLast = times.at(-1);
  if (!Number.isFinite(executionT)) return { first: horizonFirst, last: horizonLast };
  const first = Math.max(horizonFirst, Math.min(horizonLast, executionT));
  const clockT = Number.isFinite(revealT) ? revealT
    : Number.isFinite(nowT) ? nowT : executionT;
  const last = Math.min(horizonLast, Math.max(first, clockT + ORBIT_S));
  return { first, last };
}

export function clipSeries(times, values, first, last) {
  if (!Array.isArray(times) || !Array.isArray(values) || times.length !== values.length
    || !times.length || !Number.isFinite(first) || !Number.isFinite(last)) return [];
  const start = Math.max(first, times[0]);
  const end = Math.min(last, times.at(-1));
  if (start > end) return [];
  const valueAt = (t) => {
    let low = 0;
    let high = times.length - 1;
    while (low <= high) {
      const mid = Math.floor((low + high) / 2);
      if (times[mid] === t) return values[mid];
      if (times[mid] < t) low = mid + 1;
      else high = mid - 1;
    }
    if (low === 0 || low === times.length) return null;
    const a = values[low - 1];
    const b = values[low];
    if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
    return a + (b - a) * (t - times[low - 1]) / (times[low] - times[low - 1]);
  };
  const result = [];
  const push = (t, value) => {
    if (Number.isFinite(value) && result.at(-1)?.t !== t) result.push({ t, value });
  };
  push(start, valueAt(start));
  for (let i = 0; i < times.length; i += 1) {
    if (times[i] > start && times[i] < end) push(times[i], values[i]);
  }
  push(end, valueAt(end));
  return result;
}
