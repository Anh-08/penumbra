import { observedPoints } from './flightState.js';
import { clipSeries } from './chartWindow.js';

const LEFT = 48;
const RIGHT = 960;
const TOP = 16;
const BOTTOM = 166;

function xFor(t, first, last) {
  return LEFT + (t - first) / Math.max(1, last - first) * (RIGHT - LEFT);
}

function yFor(soc) {
  return BOTTOM - Math.max(0, Math.min(1, soc)) * (BOTTOM - TOP);
}

function pathFor(points, first, last, y = yFor) {
  return points.map(({ t, value }, index) =>
    `${index ? 'L' : 'M'}${xFor(t, first, last).toFixed(1)},${y(value).toFixed(1)}`).join(' ');
}

function bandFor(upperPoints, lowerPoints, first, last) {
  const upper = pathFor(upperPoints, first, last);
  if (!upper || !lowerPoints.length) return '';
  const lower = [...lowerPoints].reverse().map(({ t, value }) =>
    `L${xFor(t, first, last).toFixed(1)},${yFor(value).toFixed(1)}`).join(' ');
  return `${upper} ${lower} Z`;
}

function overviewY(soc) {
  return 15 - Math.max(0, Math.min(1, soc)) * 12;
}

function utc(t) {
  return Number.isFinite(t) ? new Date(t * 1000).toISOString().slice(11, 16) : '—';
}

export function SocChart({ trajectory, telemetry, truthSoc, socMin, planId, hypothesis, window }) {
  if (!trajectory?.t?.length) return <div className="chart-empty">AWAITING VERIFIER TRAJECTORY</div>;
  const horizonFirst = trajectory.t[0];
  const horizonLast = trajectory.t.at(-1);
  const { first, last } = window ?? { first: horizonFirst, last: horizonLast };
  const p5 = clipSeries(trajectory.t, trajectory.soc_p5, first, last);
  const p50 = clipSeries(trajectory.t, trajectory.soc_p50, first, last);
  const p95 = clipSeries(trajectory.t, trajectory.soc_p95, first, last);
  const observed = observedPoints(trajectory, telemetry).filter((point) => point.t >= first && point.t <= last);
  const truth = truthSoc ? clipSeries(truthSoc.t, truthSoc.soc, first, last) : [];
  const truthPath = pathFor(truth, first, last);
  const horizonMedian = clipSeries(trajectory.t, trajectory.soc_p50, horizonFirst, horizonLast);
  return <section className="soc-chart" aria-label="State of charge trajectory">
    <div className="chart-heading"><strong>STATE OF CHARGE · {planId ?? '—'} · {hypothesis ?? '—'} WORST PLAUSIBLE</strong></div>
    <svg viewBox="0 0 1000 190" preserveAspectRatio="none" role="img" aria-label="Verifier SOC band, downlinked points, and optional revealed truth">
      <line className="chart-axis" x1={LEFT} x2={RIGHT} y1={BOTTOM} y2={BOTTOM} />
      <text className="chart-tick" x="2" y={TOP + 5}>100%</text>
      <text className="chart-tick" x="18" y={BOTTOM}>0%</text>
      <text className="chart-tick" x={LEFT} y="186">{utc(first)}Z</text>
      <text className="chart-tick" x={RIGHT} y="186" textAnchor="end">{utc(last)}Z</text>
      <path className="chart-band" d={bandFor(p95, p5, first, last)} />
      <path className="chart-median" d={pathFor(p50, first, last)} />
      {Number.isFinite(socMin) && <>
        <line className="chart-min" x1={LEFT} x2={RIGHT} y1={yFor(socMin)} y2={yFor(socMin)} />
        <text className="chart-min-label" x={RIGHT - 3} y={yFor(socMin) - 5} textAnchor="end">SOC_MIN</text>
      </>}
      {observed.map((point) => <circle className="chart-observation" key={point.t}
        cx={xFor(point.t, first, last)} cy={yFor(point.soc)} r="3" />)}
      {truthPath && <path className="chart-truth" d={truthPath} />}
    </svg>
    {window && <svg className="chart-overview" viewBox="0 0 1000 18" preserveAspectRatio="none"
      role="img" aria-label="Full trajectory with highlighted flight window">
      <path className="chart-overview-median" d={pathFor(horizonMedian, horizonFirst, horizonLast, overviewY)} />
      <rect className="chart-window-frame" x={xFor(first, horizonFirst, horizonLast)} y="1"
        width={Math.max(2, xFor(last, horizonFirst, horizonLast) - xFor(first, horizonFirst, horizonLast))} height="16" />
    </svg>}
    <div className="chart-key"><span>p5–p95 verifier band</span>{truthPath && <span>— actual battery</span>}</div>
  </section>;
}
