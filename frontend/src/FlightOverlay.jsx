import { SocChart } from './SocChart.jsx';

function utc(t) {
  return Number.isFinite(t) ? new Date(t * 1000).toISOString().replace('T', ' ').slice(0, 19) + 'Z' : '—';
}

export function FlightOverlay({ phase, satId, trajectory, telemetry, reveal, socMin }) {
  return <section className={`hud-panel flight-overlay ${phase}`} aria-label="Eclipse flight and reveal">
    <div className="flight-heading"><strong>{satId}</strong><span>{phase === 'fastforward' ? 'NEXT ECLIPSE · 600×' : 'REVEAL'}</span></div>
    {reveal ? <div className="reveal-columns">
      <div><span>AGENT DIAGNOSED</span><strong>{reveal.diagnosed.H} · {reveal.diagnosed.label}</strong>
        <small>{Math.round(reveal.diagnosed.posterior * 100)}% posterior</small></div>
      <div><span>TRUE FAULT · SIMULATED</span><strong>{reveal.truth.kind.replaceAll('_', ' ')}</strong>
        <small>{reveal.truth.target} · onset {utc(reveal.truth.onset_t)}</small></div>
    </div> : <div className="flight-caption">{phase === 'fastforward'
      ? 'Following the approved satellite through eclipse. White points appear only when downlinked.'
      : 'Eclipse complete. Reveal the hidden simulated fault and true battery curve.'}</div>}
    <SocChart trajectory={trajectory} telemetry={telemetry} truthSoc={reveal?.truth_soc} socMin={socMin} />
  </section>;
}
