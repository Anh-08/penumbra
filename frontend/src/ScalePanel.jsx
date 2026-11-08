import { useEffect, useState } from 'react';
import {
  benchMachines, formatRate, formatSeconds, machineLabel, scaleRow, schedulerRow, SCALE_STOPS, solverLabel,
  stopLabel,
} from './scale.js';

function MachineFigures({ b4, satellites, title, showTitle }) {
  const row = scaleRow(b4, satellites);
  const sched = schedulerRow(b4);
  return <div className="scale-machine-block">
    {showTitle && <div className="scale-machine-title">{title}</div>}
    {row ? <div className="scale-figures num">
      <span>{satellites.toLocaleString('en-US')} sats × {row.hypotheses} hyp × {row.plans} plans × {row.monte_carlo} MC</span>
      <span>verify {row.hours} h: {formatSeconds(row.wall_s)} · {formatRate(row.evals_per_s)} · {Math.round(row.peak_ram_mib)} MiB</span>
      {sched && <span>schedule {sched.requests} uplinks: {solverLabel(sched.solver)}{sched.optimal ? ' optimal' : ''} · {formatSeconds(sched.solve_ms / 1000)}</span>}
    </div> : <div className="scale-figures">No measurement at this scale.</div>}
    <div className="scale-machine">{machineLabel(b4)}</div>
  </div>;
}

export function ScalePanel() {
  const [machines, setMachines] = useState(null);
  const [failed, setFailed] = useState(false);
  const [index, setIndex] = useState(0);

  useEffect(() => {
    let alive = true;
    fetch('/api/bench').then((response) => (response.ok ? response.json() : Promise.reject()))
      .then((bench) => { if (alive) setMachines(benchMachines(bench)); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, []);

  if (failed) return null;  // no API (local preview): the slider has nothing measured to show
  const satellites = SCALE_STOPS[index];
  const several = (machines?.length ?? 0) > 1;
  return <section className={`hud-panel scale-panel${several ? ' scale-panel-wide' : ''}`} aria-label="Scale benchmark">
    <div className="scale-heading"><span>SCALE</span><span className="source-tag">MEASURED OFFLINE (HEADLESS)</span></div>
    <label className="scale-slider">
      <input type="range" min="0" max={SCALE_STOPS.length - 1} step="1" value={index}
        aria-valuetext={`${satellites} satellites`} onChange={(event) => setIndex(Number(event.target.value))} />
      <span className="scale-stops">{SCALE_STOPS.map((n, i) =>
        <span key={n} className={i === index ? 'active' : ''}>{stopLabel(n)}</span>)}</span>
    </label>
    {machines === null ? <div className="scale-figures">Loading measurements…</div>
      : machines.length === 0 ? <div className="scale-figures">No scale benchmark recorded.</div>
        : <div className={several ? 'scale-machines' : ''}>{machines.map((m) =>
          <MachineFigures key={m.id} b4={m.b4} satellites={satellites} title={m.title} showTitle={several} />)}</div>}
    {satellites > 24 && <div className="scale-machine">Fleets above 24 repeat the 24 real orbits (load test).</div>}
  </section>;
}
