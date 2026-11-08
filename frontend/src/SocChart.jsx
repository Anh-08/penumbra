import { observedPoints } from './flightState.js';

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

function pathFor(t, values, first, last) {
  if (!Array.isArray(t) || !Array.isArray(values) || t.length !== values.length) return '';
  return t.map((time, index) => `${index ? 'L' : 'M'}${xFor(time, first, last).toFixed(1)},${yFor(values[index]).toFixed(1)}`).join(' ');
}

function bandFor(trajectory, first, last) {
  const upper = pathFor(trajectory.t, trajectory.soc_p95, first, last);
  if (!upper || trajectory.soc_p5?.length !== trajectory.t.length) return '';
  const lower = [...trajectory.t].reverse().map((t, index) =>
    `L${xFor(t, first, last).toFixed(1)},${yFor(trajectory.soc_p5[trajectory.t.length - index - 1]).toFixed(1)}`).join(' ');
  return `${upper} ${lower} Z`;
}

function utc(t) {
  return Number.isFinite(t) ? new Date(t * 1000).toISOString().slice(11, 16) : '—';
}

export function SocChart({ trajectory, telemetry, truthSoc, socMin }) {
  if (!trajectory?.t?.length) return <div className="chart-empty">AWAITING VERIFIER TRAJECTORY</div>;
  const first = trajectory.t[0];
  const last = trajectory.t.at(-1);
  const observed = observedPoints(trajectory, telemetry);
  const truthPath = truthSoc && pathFor(truthSoc.t, truthSoc.soc, first, last);
  return <section className="soc-chart" aria-label="State of charge trajectory">
    <div className="chart-heading"><strong>STATE OF CHARGE</strong><span className="source-tag">SIM · VERIFIER</span></div>
    <svg viewBox="0 0 1000 190" preserveAspectRatio="none" role="img" aria-label="Verifier SOC band, downlinked points, and optional revealed truth">
      <line className="chart-axis" x1={LEFT} x2={RIGHT} y1={BOTTOM} y2={BOTTOM} />
      <text className="chart-tick" x="2" y={TOP + 5}>100%</text>
      <text className="chart-tick" x="18" y={BOTTOM}>0%</text>
      <text className="chart-tick" x={LEFT} y="186">{utc(first)}Z</text>
      <text className="chart-tick" x={RIGHT} y="186" textAnchor="end">{utc(last)}Z</text>
      <path className="chart-band" d={bandFor(trajectory, first, last)} />
      <path className="chart-median" d={pathFor(trajectory.t, trajectory.soc_p50, first, last)} />
      {Number.isFinite(socMin) && <>
        <line className="chart-min" x1={LEFT} x2={RIGHT} y1={yFor(socMin)} y2={yFor(socMin)} />
        <text className="chart-min-label" x={RIGHT - 3} y={yFor(socMin) - 5} textAnchor="end">SOC_MIN</text>
      </>}
      {observed.map((point) => <circle className="chart-observation" key={point.t}
        cx={xFor(point.t, first, last)} cy={yFor(point.soc)} r="3" />)}
      {truthPath && <path className="chart-truth" d={truthPath} />}
    </svg>
    <div className="chart-key"><span>p5–p95 verifier band</span><span>● downlinked only</span>{truthPath && <span>— simulated truth</span>}</div>
  </section>;
}
