import { useEffect, useReducer, useRef, useState } from 'react';
import { buildFleetState } from './mockFleet.js';
import { createScene } from './scene.js';
import { createPanelState, reducePanel } from './panelState.js';
import { createMatrixState, reduceMatrix, scheduleConflicts, worstPlausibleHypothesis } from './matrixState.js';
import { advanceEclipse, approvalPayload, createFlightState, reduceFlight } from './flightState.js';
import { MatrixOverlay } from './Matrix.jsx';
import { FlightOverlay } from './FlightOverlay.jsx';
import {
  DirectorSlot, HypothesesPanel, ProvenanceLegend, TopBar, TracePanel, TriageQueue,
} from './Panels.jsx';

const initialTime = Date.now() / 1000;
const initialFleet = buildFleetState(initialTime);
const SOC_MIN = 0.30; // Mirrors backend.verify.constants.SOC_MIN; B1 confirmed on the board at 00:16Z.

async function postJson(path, body) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`);
  return data;
}

function App() {
  const globeElement = useRef(null);
  const scene = useRef(null);
  const hasApiFleet = useRef(false);
  const firstAlertSeen = useRef(false);
  const fastForward = useRef(null);
  const [fleet, setFleet] = useState(initialFleet);
  const [selected, setSelected] = useState('SAT-07');
  const [panel, dispatch] = useReducer(reducePanel, undefined, createPanelState);
  const [matrix, dispatchMatrix] = useReducer(reduceMatrix, undefined, createMatrixState);
  const [flight, dispatchFlight] = useReducer(reduceFlight, undefined, createFlightState);
  const [selectedPlanId, setSelectedPlanId] = useState(null);
  const [attemptPlanId, setAttemptPlanId] = useState(null);
  const [pending, setPending] = useState(false);
  const [actionError, setActionError] = useState(null);
  const [localPreview, setLocalPreview] = useState(true);
  const [sceneError, setSceneError] = useState(false);

  useEffect(() => {
    const wallStart = performance.now();
    let lastUiUpdate = 0;
    try {
      scene.current = createScene(globeElement.current, initialFleet, setSelected);
    } catch (error) {
      console.error('Cesium scene failed to initialize', error);
      setSceneError(true);
    }

    const timer = window.setInterval(() => {
      if (hasApiFleet.current) return;
      const now = performance.now();
      const state = buildFleetState(initialTime + (now - wallStart) / 1000 * 120);
      scene.current?.update(state);
      if (now - lastUiUpdate >= 500) {
        setFleet(state);
        lastUiUpdate = now;
      }
    }, 100);

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let socket;
    try {
      socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
      socket.onmessage = (event) => {
        let message;
        try { message = JSON.parse(event.data); } catch { return; }
        if (!message?.type || !message?.data) return;
        dispatchMatrix(message);
        dispatchFlight(message);
        if (message.type === 'fleet_state') {
          const flightStep = fastForward.current;
          const sat = flightStep && message.data.sats.find((item) => item.sat_id === flightStep.satId);
          if (sat) {
            const next = advanceEclipse(flightStep, sat.illum);
            fastForward.current = next.step;
            if (next.complete) postJson('/api/demo', { state: 'reveal' })
              .then((data) => dispatch({ type: 'demo_state', data }))
              .catch((error) => setActionError(error.message));
          }
          hasApiFleet.current = true;
          setLocalPreview(false);
          scene.current?.update(message.data);
          setFleet(message.data);
          dispatch(message);
          return;
        }
        if (message.type === 'alert' && !firstAlertSeen.current) {
          firstAlertSeen.current = true;
          setSelected(message.data.sat_id);
        }
        if (message.type === 'matrix') {
          setSelected(message.data.sat_id);
          if (message.data.complete) setSelectedPlanId(message.data.results[0]?.plan_id ?? null);
        }
        if (message.type === 'approval_request') {
          setSelected(message.data.sat_id);
        }
        dispatch(message);
      };
      socket.onclose = () => {
        hasApiFleet.current = false;
        setLocalPreview(true);
      };
    } catch {
      hasApiFleet.current = false;
    }

    return () => {
      window.clearInterval(timer);
      socket?.close();
      scene.current?.destroy();
      scene.current = null;
    };
  }, []);

  useEffect(() => {
    scene.current?.setHighlights?.(selected, panel.alerts.map((alert) => alert.sat_id));
  }, [selected, panel.alerts]);

  useEffect(() => {
    scene.current?.setMotionActive?.(['attract', 'storm', 'fastforward'].includes(panel.phase));
  }, [panel.phase]);

  useEffect(() => {
    const executionApproval = flight.approvals[flight.execution?.plan_id];
    if (!executionApproval
      || panel.phase !== 'approval' || fastForward.current) return;
    fastForward.current = { satId: executionApproval.sat_id, entered: false, stopping: false };
    postJson('/api/demo', { state: 'fastforward' })
      .then((data) => dispatch({ type: 'demo_state', data }))
      .catch((error) => { fastForward.current = null; setActionError(error.message); });
  }, [flight.approvals, flight.execution, panel.phase]);

  async function approveSelected() {
    const payload = approvalPayload(activeApproval, activeResult);
    if (!payload) return;
    setPending(true);
    setActionError(null);
    setAttemptPlanId(activeResult.plan_id);
    dispatchFlight({ type: 'clear_refusal', data: {} });
    try {
      const result = await postJson('/api/approve', payload);
      dispatchFlight({ type: result.refused ? 'approval_refused' : 'execution',
        data: result.refused ? { ...result, approval_id: payload.approval_id } : result });
    } catch (error) { setActionError(error.message); }
    finally { setPending(false); }
  }

  async function revealTruth() {
    setPending(true);
    setActionError(null);
    try {
      const data = await postJson('/api/reveal', { sat_id: selected });
      dispatchFlight({ type: 'reveal', data });
    } catch (error) { setActionError(error.message); }
    finally { setPending(false); }
  }

  const activeMatrix = matrix.bySat[selected];
  const matrixOpen = Boolean(activeMatrix?.results.length);
  const flightOpen = panel.phase === 'fastforward' || panel.phase === 'reveal';
  const activeResult = activeMatrix?.results.find((result) => result.plan_id === selectedPlanId)
    ?? activeMatrix?.results[0];
  const chartResult = activeMatrix?.complete ? activeMatrix.results[0] : null;
  const activeApproval = flight.approvals[activeResult?.plan_id]?.sat_id === selected
    ? flight.approvals[activeResult.plan_id] : null;
  const activeRefusal = attemptPlanId === activeResult?.plan_id ? flight.refusal : null;
  const showPanels = matrixOpen || flightOpen || panel.phase !== 'attract' || panel.alerts.length > 0 || panel.trace.length > 0
    || Object.keys(panel.hypotheses).length > 0;
  const daylight = fleet.sats.filter((sat) => sat.illum >= 0.95).length;
  const penumbra = fleet.sats.filter((sat) => sat.illum > 0.05 && sat.illum < 0.95).length;
  const eclipse = fleet.sats.length - daylight - penumbra;
  const mode = localPreview ? 'SIMULATED PREVIEW' : 'API FEED';

  return <div className={`mission-shell ${showPanels ? 'panels-visible' : 'attract-view'}`}>
    <div className="globe-stage" aria-label="Satellite fleet globe">
      <div ref={globeElement} className="cesium-canvas" />
      <div className="globe-vignette" aria-hidden="true" />
      {sceneError && <div className="scene-error">Globe renderer unavailable. Fleet data remains available.</div>}
    </div>

    <TopBar phase={panel.phase} mode={mode} time={fleet.t} speed={fleet.speed}
      hasVerification={matrixOpen || panel.trace.some((row) => row.tool === 'simulate')} />

    {!showPanels && <>
      <div className="poster-line">Every satellite in that shadow<br />is running on battery.</div>
      <div className="fleet-ticker num">
        <span>FLEET {fleet.sats.length} / 24</span>
        <span>DAYLIGHT {String(daylight).padStart(2, '0')}</span>
        <span>PENUMBRA {String(penumbra).padStart(2, '0')}</span>
        <span>ECLIPSE {String(eclipse).padStart(2, '0')}</span>
        <span>SIM CLOCK {fleet.speed}×</span>
      </div>
    </>}

    {showPanels && !matrixOpen && !flightOpen && <>
      <TriageQueue alerts={panel.alerts} now={fleet.t} selected={selected} onSelect={setSelected} />
      <HypothesesPanel satId={selected} message={panel.hypotheses[selected]} />
      <TracePanel callout={panel.callout} rows={panel.trace} mode={mode} />
    </>}

    {matrixOpen && !flightOpen && <MatrixOverlay satId={selected} matrix={activeMatrix}
      hypotheses={panel.hypotheses[selected]}
      conflict={scheduleConflicts(matrix.schedule, matrix.planSat)} status={matrix.statusBySat[selected]}
      selectedPlanId={selectedPlanId} onSelectPlan={(planId) => {
        setSelectedPlanId(planId); setAttemptPlanId(null); dispatchFlight({ type: 'clear_refusal', data: {} });
      }} failingHypotheses={activeRefusal?.failing_hypotheses}
      trajectory={flight.trajectory} telemetry={flight.telemetry[selected]} socMin={SOC_MIN}
      chartPlanId={chartResult?.plan_id} chartHypothesis={worstPlausibleHypothesis(chartResult)} />}

    {flightOpen && <FlightOverlay phase={panel.phase} satId={selected}
      trajectory={flight.trajectory} telemetry={flight.telemetry[selected]}
      reveal={flight.reveals[selected]} socMin={SOC_MIN}
      chartPlanId={chartResult?.plan_id} chartHypothesis={worstPlausibleHypothesis(chartResult)} />}

    <ProvenanceLegend localPreview={localPreview} />
    <DirectorSlot phase={panel.phase} approval={activeApproval} result={activeResult}
      refusal={activeRefusal} execution={flight.execution} pending={pending} error={actionError}
      onApprove={approveSelected} onReveal={revealTruth} />
  </div>;
}

export default App;
