import { cellPasses, rankedPlanId } from './matrixState.js';
import { SocChart } from './SocChart.jsx';

const HYPOTHESES = ['H0', 'H1', 'H2', 'H3', 'H4', 'H5'];

function percent(value) {
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : '—';
}

function MatrixCell({ result, hypothesis, plausible, fresh, rowIndex, refused }) {
  const stats = result.per_hyp?.[hypothesis];
  if (!stats) return <div className="matrix-cell pending" role="cell">—</div>;
  const ruledOut = !plausible.has(hypothesis);
  const passed = cellPasses(stats);
  return <div
    className={`matrix-cell ${ruledOut ? 'ruled-out' : passed ? 'passed' : 'failed'} ${fresh ? 'arriving' : ''} ${refused ? 'refused' : ''}`}
    style={{ '--cell-index': rowIndex }}
    role="cell"
  >
    <strong>{refused ? 'REFUSED' : ruledOut ? 'RULED OUT' : passed ? 'PASS' : 'FAILS'}</strong>
    <span className="num" title="Minimum state of charge, fifth percentile">SOC {percent(stats.min_soc_p5)}</span>
    <small className="num" title="Monte Carlo pass rate">MC {percent(stats.pass_rate)}</small>
  </div>;
}

function utcTime(t) {
  return Number.isFinite(t) ? new Date(t * 1000).toISOString().slice(11, 19) : '—';
}

function ScheduleConflict({ conflict }) {
  if (!conflict) return null;
  return <section className="schedule-conflict" aria-label="Uplink schedule details">
    <div className="schedule-title"><span>UPLINK SCHEDULE · CONTENTION</span><span className="source-tag">SCHEDULE</span></div>
    <div className="schedule-lines">
      {conflict.stations.map((station) => <span className="station-conflict" key={station.station_id}>
        <b>{station.station_id}</b> {station.windows.map((window) =>
          `${window.sat_id} ${utcTime(window.start_t)}–${utcTime(window.end_t)}Z`).join(' · ')}
      </span>)}
      {conflict.unscheduled.map((plan) => <span className="unscheduled" key={plan.plan_id}>
        <b>{plan.sat_id}</b> · {plan.plan_id} · NO SLOT BEFORE DEADLINE
      </span>)}
    </div>
  </section>;
}

export function MatrixOverlay({ satId, matrix, hypotheses, conflict, status, selectedPlanId,
  onSelectPlan, failingHypotheses, trajectory, telemetry, socMin, chartPlanId, chartHypothesis }) {
  if (!matrix?.results.length) return null;
  const plans = matrix.results;
  const plausible = new Set(hypotheses?.plausible ?? plans[0].plausible ?? []);
  const labels = hypotheses?.labels ?? {};
  const infeasible = status === 'safe_mode_recommended';
  const backendFirstPlanId = rankedPlanId(matrix);
  return <section className="hud-panel matrix-overlay" aria-label="Plan by hypothesis matrix">
    <div className="matrix-heading">
      <div><strong>{satId}</strong><span>PLAN × HYPOTHESIS</span></div>
      <span className="source-tag">SIM · VERIFIER</span>
    </div>
    {infeasible && <div className="infeasible-banner">
      <strong>INFEASIBLE</strong><span>No plan survives every plausible cause · Recommend SAFE_MODE</span>
    </div>}
    <div className="matrix-scroll">
      <div className="matrix-table" style={{ '--plan-count': plans.length }} role="table">
        <div className="matrix-row matrix-plan-row" role="row">
          <div className="matrix-row-label" role="columnheader">HYPOTHESIS</div>
          {plans.map((result) => <button key={result.plan_id}
            className={`matrix-plan-head ${backendFirstPlanId === result.plan_id ? infeasible ? 'safe-mode' : 'best' : ''} ${selectedPlanId === result.plan_id ? 'selected' : ''}`}
            onClick={() => onSelectPlan(result.plan_id)} role="columnheader">
            <strong>{result.plan_id}</strong>
            <span className={`matrix-verdict ${result.passes_all ? 'passed' : 'failed'}`}>
              {result.passes_all ? 'PASSES ALL' : 'FAILS'}
            </span>
            {backendFirstPlanId === result.plan_id && <small>{infeasible ? 'RECOMMEND SAFE_MODE' : 'BEST · VERIFIER'}</small>}
          </button>)}
        </div>
        {HYPOTHESES.map((hypothesis, rowIndex) => <div className="matrix-row" role="row" key={hypothesis}>
          <div className={`matrix-row-label ${plausible.has(hypothesis) ? '' : 'ruled-out'}`} role="rowheader">
            <strong>{hypothesis}</strong><span>{labels[hypothesis] ?? '—'}</span>
            {Number.isFinite(hypotheses?.scores?.[hypothesis]) && <em className="num">{percent(hypotheses.scores[hypothesis])}</em>}
          </div>
          {plans.map((result) => <MatrixCell key={result.plan_id}
            result={result} hypothesis={hypothesis} plausible={plausible}
            fresh={matrix.freshPlanId === result.plan_id} rowIndex={rowIndex}
            refused={selectedPlanId === result.plan_id && failingHypotheses?.includes(hypothesis)} />)}
        </div>)}
      </div>
    </div>
    <div className="matrix-footer">
      <span>{matrix.complete ? 'VERIFICATION COMPLETE' : 'SIMULATIONS ARRIVING'}</span>
      <span>{infeasible ? 'BACKEND STATUS INFEASIBLE' : matrix.complete ? 'BACKEND RANK ORDER' : 'AWAITING BACKEND RANK'}</span>
    </div>
    <div className="matrix-lower">
      <ScheduleConflict conflict={conflict} />
      <SocChart trajectory={trajectory} telemetry={telemetry} socMin={socMin}
        planId={chartPlanId} hypothesis={chartHypothesis} />
    </div>
  </section>;
}
