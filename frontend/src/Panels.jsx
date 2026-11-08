import { formatCountdown, formatDuration } from './panelState.js';
import { hashPrefix } from './flightState.js';
import { STEPPER_LABEL, approvalTierLine, stepperItems } from './operatorCopy.js';

const HYPOTHESES = ['H0', 'H1', 'H2', 'H3', 'H4', 'H5'];

function PanelHeading({ children }) {
  return <div className="panel-heading"><span>{children}</span></div>;
}

function timeLabel(t) {
  if (!Number.isFinite(t)) return '—';
  return new Date(t * 1000).toISOString().slice(11, 19);
}

function statusLabel(status) {
  return {
    queued: 'QUEUED',
    working: 'AI WORKING',
    auto_executed: 'AUTO-EXECUTED',
    awaiting_approval: 'NEEDS APPROVAL',
    executed: 'EXECUTED',
    safe_mode_recommended: 'SAFE MODE RECOMMENDED',
  }[status] ?? 'QUEUED';
}

export function TriageQueue({ alerts, now, selected, onSelect }) {
  return <section className="hud-panel triage-panel" aria-label="Triage queue">
    <PanelHeading>ALERTS</PanelHeading>
    <div className="triage-scroll">
      {alerts.length === 0 && <div className="panel-empty">AWAITING FLEET ALERTS</div>}
      {alerts.map((alert) => <button
        className={`triage-row ${selected === alert.sat_id ? 'focused' : ''}`}
        key={alert.alert_id}
        onClick={() => onSelect(alert.sat_id)}
      >
        <span className="triage-main">
          <span className="triage-line"><strong>{alert.sat_id}</strong><span className={`severity ${alert.severity}`}>{alert.severity.toUpperCase()}</span></span>
          <span className={`triage-status ${alert.status}`}>{statusLabel(alert.status)}</span>
        </span>
        <span className="triage-times">
          <span className="aos num">NEXT PASS {formatCountdown(alert.next_contact_aos, now)}</span>
          <span className="ttc num">TIME LEFT {formatDuration(alert.time_to_critical_s)}</span>
        </span>
      </button>)}
    </div>
  </section>;
}

export function HypothesesPanel({ satId, message }) {
  const scores = message?.scores ?? {};
  const labels = message?.labels ?? {};
  const plausible = new Set(message?.plausible ?? []);
  const hypotheses = [...HYPOTHESES].sort((a, b) => (scores[b] ?? -1) - (scores[a] ?? -1));
  const leader = hypotheses.find((h) => Number.isFinite(scores[h]));
  return <section className="hud-panel hypothesis-panel" aria-label="Hypothesis scores">
    <PanelHeading>HYPOTHESES · {satId}</PanelHeading>
    {!message && <div className="panel-empty">AWAITING BACKEND SCORES</div>}
    {message && <div className="hypothesis-rows">
      {hypotheses.map((hypothesis) => {
        const value = scores[hypothesis];
        const hasScore = Number.isFinite(value);
        return <div className={`hypothesis-row ${plausible.has(hypothesis) ? '' : 'ruled-out'} ${leader === hypothesis ? 'leading' : ''}`} key={hypothesis}>
          <span className="hyp-code num">{hypothesis}</span>
          <span className="hyp-label">{labels[hypothesis] ?? '—'}</span>
          <span className="hyp-bar"><span style={{ transform: `scaleX(${hasScore ? Math.max(0, Math.min(1, value)) : 0})` }} /></span>
          <span className="hyp-value num">{hasScore ? `${Math.round(value * 100)}%` : '—'}</span>
          {!plausible.has(hypothesis) && <span className="ruled-label">RULED OUT</span>}
        </div>;
      })}
    </div>}
  </section>;
}

export function TracePanel({ callout, rows }) {
  return <section className="hud-panel trace-panel" aria-label="Agent tool trace">
    <div className="trace-caption">{callout ?? 'Awaiting mission events'}</div>
    <div className="trace-heading"><span>TOOL-CALL TRACE</span></div>
    <div className="trace-rows">
      {rows.length === 0 && <div className="trace-empty">No agent tools have run yet.</div>}
      {rows.slice(-4).map((row) => <div className="trace-row" key={row.id}>
        <strong>{row.tool}</strong>
        <span className="trace-args num" title={row.args}>{row.args}</span>
        <span className="trace-result num" title={row.result}>→ {row.result}</span>
      </div>)}
    </div>
  </section>;
}

export function ProvenanceLegend({ localPreview }) {
  return <section className="hud-panel provenance-legend" aria-label="Real and simulated data legend">
    <div className="legend-line"><span className="legend-swatch real" /><strong>REAL</strong><span>Orbits · Sun/Moon · stations · contacts · star field</span></div>
    <div className="legend-line"><span className="legend-swatch simulated" /><strong>SIMULATED</strong><span>{localPreview ? 'Preview orbit · power · thermal · faults' : 'Power · thermal · faults'}</span></div>
  </section>;
}

function Chevron({ className }) {
  return <svg className={className} viewBox="0 0 12 24" focusable="false"><path d="M3 4l7 8-7 8" /></svg>;
}

export function TopBar({ phase, mode, time, speed, hasVerification, muted, onToggleVoice,
  skyVisible, onToggleSky, anatomyOn, onToggleAnatomy, mcOn, onToggleMc }) {
  return <header className="top-bar">
    <div className="top-brand"><strong>PENUMBRA</strong></div>
    <div className="stage-stepper" aria-label={STEPPER_LABEL}>
      {stepperItems(phase, hasVerification).map((item) => item.kind === 'arrow'
        ? <span className={`stage-arrow ${item.state}${item.last ? ' last' : ''}`} aria-hidden="true" key={item.key}>
          <Chevron className="chevron-base" /><Chevron className="chevron-fill" /></span>
        : <div className={`stage ${item.state}${item.last ? ' last' : ''}`} key={item.key}>
          <span className="stage-label">{item.label}</span></div>)}
    </div>
    <div className="top-status"><span className="mode-chip">{mode}</span>
      <button type="button" className="sky-chip" aria-pressed={skyVisible}
        title="Toggle constellation lines" onClick={onToggleSky}>SKY</button>
      {onToggleAnatomy && <button type="button" className="sky-chip" aria-pressed={anatomyOn}
        title="Satellite anatomy: card on the attract screen, press A to open" onClick={onToggleAnatomy}>ANATOMY</button>}
      {onToggleMc && <button type="button" className="sky-chip" aria-pressed={mcOn}
        title="Monte Carlo grid while physics verifies a plan (illustrative)" onClick={onToggleMc}>MC VIEW</button>}
      {onToggleVoice && <button type="button" className="voice-chip" aria-pressed={!muted}
        title="Voice callouts (captions always show)" onClick={onToggleVoice}>{muted ? 'VOICE OFF' : 'VOICE ON'}</button>}<time className="num">{timeLabel(time)} UTC</time><span className="speed-chip num">{speed === 0 ? 'PAUSED' : `${speed}×`}</span></div>
  </header>;
}

export function DirectorSlot({ phase, approval, result, refusal, execution, pending, error,
  runLive, onToggleRunLive, onStartStorm, onApprove, onReveal }) {
  const executed = Boolean(execution && approval && execution.plan_id === approval.plan_id
    && execution.sat_id === approval.sat_id);
  const canReveal = phase === 'reveal' && executed;
  const failed = result?.passes_all === false;
  const currentHash = approval?.plan_hash === result?.plan_hash;
  return <section className="hud-panel director-slot" aria-label="Flight Director">
    <PanelHeading>FLIGHT DIRECTOR</PanelHeading>
    {phase === 'attract' ? <>
      <button className="director-action storm" onClick={onStartStorm} disabled={pending}>
        {pending ? 'STARTING…' : 'START INCIDENT'}
      </button>
      <label className="run-live-switch">
        <input type="checkbox" checked={runLive} onChange={onToggleRunLive} disabled={pending} />
        Run live with Gemini next run
      </label>
    </> : canReveal ? <button className="director-action reveal" onClick={onReveal} disabled={pending}>
      {pending ? 'SHOWING…' : 'SHOW WHAT HAPPENED'}
    </button> : executed ? <div className="director-executed">EXECUTED · {execution.plan_id}
      {phase === 'fastforward' && <small>Following the next eclipse at 600×</small>}
    </div> : approval && result ? <>
      <button className={`director-action ${failed ? 'failed' : 'verified'}`}
        onClick={onApprove} disabled={pending || !currentHash}>
        <span>{pending ? 'CHECKING…' : `APPROVE ${result.plan_id}`}</span>
        <small>{approvalTierLine(approval.tier, hashPrefix(result.plan_hash))}</small>
        {failed && <small>FAILED VERIFICATION · PHYSICS WILL VETO</small>}
      </button>
    </> : <div className="director-wait">Waiting for a verified plan</div>}
    {refusal && <div className="director-refusal" role="alert">
      <strong>REFUSED · PHYSICS VETO</strong>
      <span>{refusal.reason_key.replaceAll('_', ' ')} · {refusal.failing_hypotheses.join(', ') || 'No failing hypotheses reported'} · No override exists.</span>
    </div>}
    {error && <div className="director-error" role="alert">{error}</div>}
  </section>;
}
