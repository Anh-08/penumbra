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
    for (let i = 0; i < times.length; i += 1) {
      if (times[i] === t) return values[i];
      if (i > 0 && times[i] > t && times[i - 1] < t) {
        const a = values[i - 1];
        const b = values[i];
        if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
        return a + (b - a) * (t - times[i - 1]) / (times[i] - times[i - 1]);
      }
    }
    return null;
  };
  const points = [start, ...times.filter((t) => t > start && t < end), end];
  return [...new Set(points)].flatMap((t) => {
    const value = valueAt(t);
    return Number.isFinite(value) ? [{ t, value }] : [];
  });
}
