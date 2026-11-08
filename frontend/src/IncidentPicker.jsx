// Operator-triggered incident (T42), shown as the incident console while the demo is in attract (T46): satellites,
// one fault card, push-to-talk, then START INCIDENT. The recorded run stays the default until the judge changes
// something (startMode); "reset to the recorded incident" goes back to it.
import { FALLBACK_FAULTS, START_TAGS, faultEffect, noticeFor, startMode } from './incidentState.js';
import { PushToTalk } from './PushToTalk.jsx';

export function IncidentPicker({ state, dispatch, pending, onStart, onStartRecorded, runLive, onToggleRunLive }) {
  const mode = startMode(state);
  const notice = noticeFor(state);
  const faults = state.faults.length ? state.faults : FALLBACK_FAULTS;
  const locked = pending || !state.available;
  return <div className="incident-picker">
    <div className="incident-heading">INCIDENT</div>
    <div className="incident-row">
      <span className="incident-label">SATELLITES <small>up to {state.maxIncidents}</small></span>
      <div className="sat-chips" role="group" aria-label="Satellites in the incident">
        {state.sats.map((id) => {
          const on = state.selected.includes(id);
          const full = !on && state.selected.length >= state.maxIncidents;
          return <button key={id} type="button" className="sat-chip" aria-pressed={on}
            disabled={locked || full}
            onClick={() => dispatch({ type: 'toggle_sat', satId: id })}>{id.slice(4)}</button>;
        })}
      </div>
    </div>
    <div className="incident-row">
      <span className="incident-label" id="incident-fault-label">FAULT</span>
      <div className="fault-cards" role="radiogroup" aria-labelledby="incident-fault-label">
        {faults.map((f) => <button key={f.id} type="button" role="radio" className="fault-card"
          aria-checked={state.fault === f.id} disabled={locked}
          onClick={() => dispatch({ type: 'set_fault', fault: f.id })}>
          <strong>{f.label}</strong><span>{faultEffect(f.id)}</span>
        </button>)}
      </div>
    </div>
    {state.available && <PushToTalk incident={state} dispatchIncident={dispatch} disabled={pending} />}
    <button className="director-action storm" disabled={pending}
      onClick={mode === 'custom' ? onStart : onStartRecorded}>
      <span>{pending ? 'STARTING…' : 'START INCIDENT'}</span>
      <small className="start-tag">{START_TAGS[mode]}</small>
    </button>
    {notice ? <div className="incident-notice">{notice}</div>
      : <div className="incident-notice">
        <button type="button" className="incident-link" onClick={() => dispatch({ type: 'reset' })}
          disabled={pending || !state.touched}>
          reset to the recorded incident
        </button>
      </div>}
    <label className="run-live-switch">
      <input type="checkbox" checked={runLive} onChange={onToggleRunLive} disabled={pending} />
      Recorded incident: run live with Gemini
    </label>
  </div>;
}
