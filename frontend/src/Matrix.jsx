const HYPOTHESES = ['H0', 'H1', 'H2', 'H3', 'H4', 'H5'];

function percent(value) {
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : '—';
}

function MatrixCell({ result, hypothesis, plausible, fresh, rowIndex }) {
  const stats = result.per_hyp?.[hypothesis];
  if (!stats) return <div className="matrix-cell pending" role="cell">—</div>;
  const ruledOut = !plausible.has(hypothesis);
  const passed = stats.pass_rate === 1;
  return <div
    className={`matrix-cell ${ruledOut ? 'ruled-out' : passed ? 'passed' : 'failed'} ${fresh ? 'arriving' : ''}`}
    style={{ '--cell-index': rowIndex }}
    role="cell"
  >
    <strong>{ruledOut ? 'RULED OUT' : passed ? 'PASS' : 'FAILS'}</strong>
    <span className="num" title="Minimum state of charge, fifth percentile">SOC {percent(stats.min_soc_p5)}</span>
    <small className="num" title="Monte Carlo pass rate">MC {percent(stats.pass_rate)}</small>
  </div>;
}

function ScheduleConflict({ schedule }) {
  if (!schedule || schedule.unscheduled.length === 0) return null;
  return <section className="schedule-conflict" aria-label="Uplink schedule details">
    <div className="schedule-title"><span>UPLINK SCHEDULE · UNSCHEDULED</span><span className="source-tag">{schedule.solver.toUpperCase()}</span></div>
    <div className="schedule-lines">
      {schedule.assignments.map((item) => <span key={`${item.plan_id}:${item.station_id}`}>
        <b>{item.station_id}</b> ← <span className="num">{item.plan_id}</span>
      </span>)}
      {schedule.unscheduled.map((planId) => <span className="unscheduled" key={planId}>
        <b>{planId}</b> UNSCHEDULED
      </span>)}
    </div>
  </section>;
}

export function MatrixOverlay({ satId, matrix, hypotheses, schedule, selectedPlanId, onSelectPlan }) {
  if (!matrix?.results.length) return null;
  const plans = matrix.results;
  const plausible = new Set(hypotheses?.plausible ?? plans[0].plausible ?? []);
  const labels = hypotheses?.labels ?? {};
  const rank = matrix.rank;
  const bestPlanId = rank?.status === 'feasible' ? rank.ordered[0] : null;
  return <section className="hud-panel matrix-overlay" aria-label="Plan by hypothesis matrix">
    <div className="matrix-heading">
      <div><strong>{satId}</strong><span>PLAN × HYPOTHESIS</span></div>
      <span className="source-tag">SIM · VERIFIER</span>
    </div>
    {rank?.status === 'infeasible' && <div className="infeasible-banner">
      <strong>INFEASIBLE</strong><span>No plan survives every plausible cause · Recommend SAFE_MODE</span>
    </div>}
    <div className="matrix-scroll">
      <div className="matrix-table" style={{ '--plan-count': plans.length }} role="table">
        <div className="matrix-row matrix-plan-row" role="row">
          <div className="matrix-row-label" role="columnheader">HYPOTHESIS</div>
          {plans.map((result) => <button key={result.plan_id}
            className={`matrix-plan-head ${bestPlanId === result.plan_id ? 'best' : ''} ${selectedPlanId === result.plan_id ? 'selected' : ''}`}
            onClick={() => onSelectPlan(result.plan_id)} role="columnheader">
            <strong>{result.plan_id}</strong>
            {bestPlanId === result.plan_id && <small>BEST · VERIFIER</small>}
          </button>)}
        </div>
        {HYPOTHESES.map((hypothesis, rowIndex) => <div className="matrix-row" role="row" key={hypothesis}>
          <div className={`matrix-row-label ${plausible.has(hypothesis) ? '' : 'ruled-out'}`} role="rowheader">
            <strong>{hypothesis}</strong><span>{labels[hypothesis] ?? '—'}</span>
            {Number.isFinite(hypotheses?.scores?.[hypothesis]) && <em className="num">{percent(hypotheses.scores[hypothesis])}</em>}
          </div>
          {plans.map((result) => <MatrixCell key={result.plan_id}
            result={result} hypothesis={hypothesis} plausible={plausible}
            fresh={matrix.freshPlanId === result.plan_id} rowIndex={rowIndex} />)}
        </div>)}
      </div>
    </div>
    <div className="matrix-footer">
      <span>{matrix.complete ? 'VERIFICATION COMPLETE' : 'SIMULATIONS ARRIVING'}</span>
      <span>{rank ? `BACKEND STATUS ${rank.status.toUpperCase()}` : 'AWAITING BACKEND RANK'}</span>
    </div>
    <div className="matrix-lower">
      <ScheduleConflict schedule={schedule} />
      {!schedule?.unscheduled.length && <div className="trajectory-reserved">
        <span>TRAJECTORY</span><span>Awaiting verifier data</span>
      </div>}
    </div>
  </section>;
}
