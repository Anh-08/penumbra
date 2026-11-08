// Operator-triggered incident (T42): satellites + fault, then START INCIDENT. Lives in the Flight Director slot
// while the demo is in attract. Without the custom path the button runs the recorded incident.
import { canStart, noticeFor } from './incidentState.js';

export function IncidentPicker({ state, dispatch, pending, onStart, onStartRecorded, runLive, onToggleRunLive }) {
  const custom = canStart(state);
  const notice = noticeFor(state);
  return <div className="incident-picker">
    <div className="incident-row">
      <span className="incident-label">SATELLITES <small>up to {state.maxIncidents}</small></span>
      <div className="sat-chips" role="group" aria-label="Satellites in the incident">
        {state.sats.map((id) => {
          const on = state.selected.includes(id);
          const full = !on && state.selected.length >= state.maxIncidents;
          return <button key={id} type="button" className="sat-chip" aria-pressed={on}
            disabled={pending || !state.available || full}
            onClick={() => dispatch({ type: 'toggle_sat', satId: id })}>{id.slice(4)}</button>;
        })}
      </div>
    </div>
    <div className="incident-row">
      <label className="incident-label" htmlFor="incident-fault">FAULT</label>
      <select id="incident-fault" className="incident-select" value={state.fault ?? ''}
        disabled={pending || !state.available}
        onChange={(event) => dispatch({ type: 'set_fault', fault: event.target.value })}>
        {state.faults.length === 0 && <option value="">—</option>}
        {state.faults.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
      </select>
    </div>
    <button className="director-action storm" disabled={pending}
      onClick={custom ? onStart : onStartRecorded}>
      {pending ? 'STARTING…' : 'START INCIDENT'}
    </button>
    {notice ? <div className="incident-notice">{notice}</div>
      : <div className="incident-notice">
        <button type="button" className="incident-link" onClick={onStartRecorded} disabled={pending}>
          or run the recorded incident
        </button>
      </div>}
    <label className="run-live-switch">
      <input type="checkbox" checked={runLive} onChange={onToggleRunLive} disabled={pending} />
      Recorded incident: run live with Gemini
    </label>
  </div>;
}
