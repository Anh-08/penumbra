import { useEffect, useReducer, useRef, useState } from 'react';
import { buildFleetState } from './mockFleet.js';
import { createScene } from './scene.js';
import { createPanelState, reducePanel } from './panelState.js';
import {
  DirectorSlot, HypothesesPanel, ProvenanceLegend, TopBar, TracePanel, TriageQueue,
} from './Panels.jsx';

const initialTime = Date.now() / 1000;
const initialFleet = buildFleetState(initialTime);

function App() {
  const globeElement = useRef(null);
  const scene = useRef(null);
  const hasApiFleet = useRef(false);
  const firstAlertSeen = useRef(false);
  const [fleet, setFleet] = useState(initialFleet);
  const [selected, setSelected] = useState('SAT-07');
  const [panel, dispatch] = useReducer(reducePanel, undefined, createPanelState);
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
        if (message.type === 'fleet_state') {
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

  const showPanels = panel.phase !== 'attract' || panel.alerts.length > 0 || panel.trace.length > 0
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
      hasVerification={panel.trace.some((row) => row.tool === 'simulate')} />

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

    {showPanels && <>
      <TriageQueue alerts={panel.alerts} now={fleet.t} selected={selected} onSelect={setSelected} />
      <HypothesesPanel satId={selected} message={panel.hypotheses[selected]} />
      <TracePanel callout={panel.callout} rows={panel.trace} mode={mode} />
    </>}

    <ProvenanceLegend localPreview={localPreview} />
    <DirectorSlot />
  </div>;
}

export default App;
