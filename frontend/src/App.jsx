import { lazy, Suspense, useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { buildFleetState } from './mockFleet.js';
import { createScene } from './scene.js';
import { connectFleetSocket } from './fleetSocket.js';
import { createPanelState, reducePanel } from './panelState.js';
import { createMatrixState, reduceMatrix, scheduleConflicts, worstPlausibleHypothesis } from './matrixState.js';
import {
  advanceEclipse, approvalPayload, createFlightState, flightFocus, reduceFlight, ticketKey,
} from './flightState.js';
import {
  createAnatomyState, keyAction, loadEnabled as loadAnatomyEnabled, reduceAnatomy, saveEnabled as saveAnatomyEnabled,
} from './anatomy/anatomyState.js';
import { createVoicePlayer, loadMuted, saveMuted, shouldSpeak } from './voice.js';
import { createIncidentState, reduceIncident, requestBody } from './incidentState.js';
import { RECORDED_INCIDENT_SATS } from './satGlyph.js';
import {
  createMcState, loadEnabled as loadMcEnabled, reduceMc, saveEnabled as saveMcEnabled,
} from './montecarlo/mcState.js';
import { MatrixOverlay } from './Matrix.jsx';
import { FlightOverlay } from './FlightOverlay.jsx';
import {
  DirectorSlot, HypothesesPanel, ProvenanceLegend, TopBar, TracePanel, TriageQueue,
} from './Panels.jsx';

// SATELLITE ANATOMY (T37): three and the view load only on demand, never with the landing page.
const AnatomyView = lazy(() => import('./anatomy/AnatomyView.jsx'));
const AnatomyCard = lazy(() => import('./anatomy/AnatomyCard.jsx'));
const ANATOMY_CARD_DELAY_MS = 1500;  // after first paint: the thumbnail never slows the boot
// MONTE CARLO grid (T38): lazy as well; it mounts the first time a plan is simulated with MC VIEW on.
const MonteCarloOverlay = lazy(() => import('./montecarlo/MonteCarloOverlay.jsx'));
const MC_TICK_MS = 200;

const initialTime = Date.now() / 1000;
const initialFleet = buildFleetState(initialTime);
const NO_SATS = [];  // T47: no incident highlight outside attract unless a run set is frozen
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
  // A FAULT STORM run is directed by the API (backend/api/storm.py): it sends every demo_state, including
  // fastforward and reveal, so the page must not drive those transitions itself.
  const directed = useRef(false);
  const storage = typeof window !== 'undefined' ? window.localStorage : null;
  const [muted, setMuted] = useState(() => { try { return loadMuted(storage); } catch { return false; } });
  const mutedRef = useRef(muted);
  const voice = useRef(null);
  if (voice.current === null) voice.current = createVoicePlayer((url) => new Audio(url));
  const [fleet, setFleet] = useState(initialFleet);
  const [selected, setSelected] = useState('SAT-07');
  const [panel, dispatch] = useReducer(reducePanel, undefined, createPanelState);
  const [matrix, dispatchMatrix] = useReducer(reduceMatrix, undefined, createMatrixState);
  const [flight, dispatchFlight] = useReducer(reduceFlight, undefined, createFlightState);
  const [selectedPlanId, setSelectedPlanId] = useState(null);
  const [anatomy, dispatchAnatomyRaw] = useReducer(reduceAnatomy, undefined, () => {
    try { return createAnatomyState(loadAnatomyEnabled(window.localStorage)); } catch { return createAnatomyState(); }
  });
  const dispatchAnatomy = useCallback((action) => dispatchAnatomyRaw(action), []);
  const [anatomyCardReady, setAnatomyCardReady] = useState(false);
  const [mc, dispatchMc] = useReducer(reduceMc, undefined, () => {
    try { return createMcState(loadMcEnabled(window.localStorage)); } catch { return createMcState(); }
  });
  const [mcMounted, setMcMounted] = useState(false);
  const [attemptPlanId, setAttemptPlanId] = useState(null);
  const [pending, setPending] = useState(false);
  const [actionError, setActionError] = useState(null);
  const [localPreview, setLocalPreview] = useState(true);
  const [sceneError, setSceneError] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [revealT, setRevealT] = useState(null);
  const [runLive, setRunLive] = useState(false);
  const [incident, dispatchIncident] = useReducer(reduceIncident, undefined, createIncidentState);
  const [skyVisible, setSkyVisible] = useState(() => {
    try { return window.localStorage.getItem('penumbra.sky') !== 'off'; } catch { return true; }
  });

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
    const stopSocket = connectFleetSocket(`${protocol}//${window.location.host}/ws`, (event) => {
        let message;
        try { message = JSON.parse(event.data); } catch { return; }
        if (!message?.type || !message?.data) return;
        dispatchMatrix(message);
        dispatchMc({ type: 'message', message, now: performance.now() });
        dispatchFlight(message);
        if (message.type === 'fleet_state') {
          const flightStep = directed.current ? null : fastForward.current;
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
          setReconnecting(false);
          scene.current?.update(message.data);
          setFleet(message.data);
          dispatch(message);
          return;
        }
        if (message.type === 'callout' && shouldSpeak(message.data, mutedRef.current)) {
          voice.current.play(message.data.audio_url);
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
      }, () => setReconnecting(true));

    return () => {
      window.clearInterval(timer);
      stopSocket();
      scene.current?.destroy();
      scene.current = null;
    };
  }, []);

  useEffect(() => {
    scene.current?.setHighlights?.(selected, panel.alerts.map((alert) => alert.sat_id));
  }, [selected, panel.alerts]);

  // T47: the incident's satellites stay amber on the globe. In attract they follow the picker; START INCIDENT freezes
  // the set for the run (the recorded run's three satellites, or the custom pick); back in attract it clears.
  const [runSats, setRunSats] = useState(null);
  useEffect(() => { if (panel.phase === 'attract') setRunSats(null); }, [panel.phase]);
  const incidentSats = runSats ?? (panel.phase === 'attract' ? incident.selected : NO_SATS);
  useEffect(() => { scene.current?.setIncidentSats?.(incidentSats); }, [incidentSats]);

  useEffect(() => {
    scene.current?.setMotionActive?.(['attract', 'storm', 'fastforward'].includes(panel.phase));
  }, [panel.phase]);

  useEffect(() => { scene.current?.setSkyVisible?.(skyVisible); }, [skyVisible]);

  useEffect(() => { dispatchMc({ type: 'phase', phase: panel.phase }); }, [panel.phase]);
  // Warm the MC chunk (and three) once a storm starts, so the grid is ready at the first simulate.
  useEffect(() => {
    if (mc.enabled && ['storm', 'agent', 'approval'].includes(panel.phase)) setMcMounted(true);
  }, [mc.enabled, panel.phase]);
  useEffect(() => {
    if (!mc.open) return undefined;
    setMcMounted(true);
    const timer = window.setInterval(() => dispatchMc({ type: 'tick', now: performance.now() }), MC_TICK_MS);
    return () => window.clearInterval(timer);
  }, [mc.open]);

  useEffect(() => {
    const timer = window.setTimeout(() => setAnatomyCardReady(true), ANATOMY_CARD_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    const onKey = (event) => {
      const target = event.target;
      const typing = target instanceof HTMLElement
        && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
      const action = event.ctrlKey || event.metaKey || event.altKey ? null : keyAction(event.key, anatomy, typing);
      if (action) { event.preventDefault(); dispatchAnatomy(action); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [anatomy, dispatchAnatomy]);

  function toggleAnatomy() {
    saveAnatomyEnabled(window.localStorage, !anatomy.enabled);
    dispatchAnatomy({ type: 'toggle_chip' });
  }

  function toggleMc() {
    saveMcEnabled(window.localStorage, !mc.enabled);
    dispatchMc({ type: 'toggle_chip' });
  }

  function toggleSky() {
    setSkyVisible((visible) => {
      const next = !visible;
      try { window.localStorage.setItem('penumbra.sky', next ? 'on' : 'off'); } catch { /* optional preference */ }
      return next;
    });
  }

  useEffect(() => {
    setRevealT(panel.phase === 'reveal' ? fleet.t : null);
  }, [panel.phase]);

  useEffect(() => {
    const execution = flight.execution;
    const executionApproval = execution ? flight.approvals[ticketKey(execution.sat_id, execution.plan_id)] : null;
    if (!executionApproval || directed.current
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
      const data = await postJson('/api/reveal', { sat_id: focus.satId });
      dispatchFlight({ type: 'reveal', data });
    } catch (error) { setActionError(error.message); }
    finally { setPending(false); }
  }

  function toggleVoice() {
    const next = !mutedRef.current;
    mutedRef.current = next;
    setMuted(next);
    saveMuted(storage, next);
    if (next) voice.current.stop();
  }

  // T42: the picker's options come from the API once; an older server (404) or one without a model key keeps
  // the recorded incident as the only run.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch('/api/incident/options');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const options = await response.json();
        if (!cancelled) dispatchIncident({ type: 'options_loaded', options });
      } catch { if (!cancelled) dispatchIncident({ type: 'options_failed' }); }
    })();
    return () => { cancelled = true; };
  }, []);

  async function startIncident() {
    const body = requestBody(incident);
    if (!body) return startStorm();
    setPending(true);
    setActionError(null);
    setRunSats(body.incidents.map((item) => item.sat_id));
    try {
      await postJson('/api/incident', body);
      directed.current = true;
      fastForward.current = null;
    } catch (error) { setActionError(error.message); setRunSats(null); }
    finally { setPending(false); }
  }

  async function startStorm() {
    setPending(true);
    setActionError(null);
    setRunSats(RECORDED_INCIDENT_SATS);
    try {
      await postJson('/api/fault-storm', { live: runLive });
      directed.current = true;
      fastForward.current = null;
    } catch (error) { setActionError(error.message); setRunSats(null); }
    finally { setPending(false); }
  }

  const activeMatrix = matrix.bySat[selected];
  const matrixOpen = Boolean(activeMatrix?.results.length);
  const flightOpen = panel.phase === 'fastforward' || panel.phase === 'reveal';
  const focus = flightFocus({ phase: panel.phase, selected, selectedPlanId, flight, matrixBySat: matrix.bySat });
  const activeResult = focus.result ?? undefined;
  const chartResult = focus.chartResult;
  const activeApproval = focus.approval;
  const activeRefusal = !focus.following && attemptPlanId === activeResult?.plan_id ? flight.refusal : null;
  const showPanels = matrixOpen || flightOpen || panel.phase !== 'attract' || panel.alerts.length > 0 || panel.trace.length > 0
    || Object.keys(panel.hypotheses).length > 0;
  const daylight = fleet.sats.filter((sat) => sat.illum >= 0.95).length;
  const penumbra = fleet.sats.filter((sat) => sat.illum > 0.05 && sat.illum < 0.95).length;
  const eclipse = fleet.sats.length - daylight - penumbra;
  const mode = runLive ? 'LIVE · GEMINI' : 'PACED REPLAY';

  return <div className={`mission-shell ${showPanels ? 'panels-visible' : 'attract-view'}`}>
    <div className="globe-stage" aria-label="Satellite fleet globe">
      <div ref={globeElement} className="cesium-canvas" />
      <div className="globe-vignette" aria-hidden="true" />
      {sceneError && <div className="scene-error">Globe renderer unavailable. Fleet data remains available.</div>}
    </div>

    <TopBar phase={panel.phase} mode={mode} time={fleet.t} speed={fleet.speed}
      hasVerification={matrixOpen || panel.trace.some((row) => row.tool === 'simulate')}
      skyVisible={skyVisible} onToggleSky={toggleSky}
      anatomyOn={anatomy.enabled} onToggleAnatomy={toggleAnatomy}
      mcOn={mc.enabled} onToggleMc={toggleMc}
      muted={muted} onToggleVoice={toggleVoice} />
    {reconnecting && hasApiFleet.current && <div className="reconnect-badge" role="status">RECONNECTING · showing last state</div>}

    {!showPanels && <>
      <div className="poster-line">Every satellite in that shadow<br />is running on battery.</div>
      <div className="fleet-ticker num">
        <span>FLEET {fleet.sats.length}</span>
        <span>DAYLIGHT {daylight}</span>
        <span>PENUMBRA {penumbra}</span>
        <span>ECLIPSE {eclipse}</span>
      </div>
    </>}

    {showPanels && !matrixOpen && !flightOpen && <>
      <TriageQueue alerts={panel.alerts} now={fleet.t} selected={selected} onSelect={setSelected} />
      <HypothesesPanel satId={selected} message={panel.hypotheses[selected]} />
      <TracePanel callout={panel.callout} rows={panel.trace} />
    </>}

    {matrixOpen && !flightOpen && <MatrixOverlay satId={selected} matrix={activeMatrix}
      hypotheses={panel.hypotheses[selected]}
      conflict={scheduleConflicts(matrix.schedule, matrix.planSat)} status={matrix.statusBySat[selected]}
      selectedPlanId={selectedPlanId} onSelectPlan={(planId) => {
        setSelectedPlanId(planId); setAttemptPlanId(null); dispatchFlight({ type: 'clear_refusal', data: {} });
      }} failingHypotheses={activeRefusal?.failing_hypotheses}
      trajectory={flight.trajectory} telemetry={flight.telemetry[selected]} socMin={SOC_MIN}
      chartPlanId={chartResult?.plan_id} chartHypothesis={worstPlausibleHypothesis(chartResult)} />}

    {flightOpen && <FlightOverlay phase={panel.phase} satId={focus.satId}
      trajectory={flight.trajectory} telemetry={flight.telemetry[focus.satId]}
      reveal={flight.reveals[focus.satId]} socMin={SOC_MIN}
      chartPlanId={chartResult?.plan_id} chartHypothesis={worstPlausibleHypothesis(chartResult)}
      executionT={flight.approved?.t} nowT={fleet.t} revealT={revealT} />}

    {anatomy.enabled && anatomyCardReady && !showPanels && <Suspense fallback={null}>
      <AnatomyCard onOpen={() => dispatchAnatomy({ type: 'open' })} />
    </Suspense>}
    {mcMounted && <Suspense fallback={null}>
      <MonteCarloOverlay state={mc} dispatch={dispatchMc} visible={mc.open && !flightOpen}
        legend={<ProvenanceLegend localPreview={localPreview} />} />
    </Suspense>}
    <ProvenanceLegend localPreview={localPreview} />
    {anatomy.open && <Suspense fallback={<div className="anatomy-view anatomy-loading" role="status">Loading the anatomy view…</div>}>
      <AnatomyView state={anatomy} dispatch={dispatchAnatomy} legend={<ProvenanceLegend localPreview={localPreview} />} />
    </Suspense>}
    <DirectorSlot phase={panel.phase} approval={activeApproval} result={activeResult}
      refusal={activeRefusal} execution={flight.execution} pending={pending} error={actionError}
      runLive={runLive} onToggleRunLive={() => setRunLive((value) => !value)}
      onStartStorm={startStorm} onApprove={approveSelected} onReveal={revealTruth}
      incident={incident} dispatchIncident={dispatchIncident} onStartIncident={startIncident} />
  </div>;
}

export default App;
