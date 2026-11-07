import { useEffect, useRef, useState } from 'react';
import { buildFleetState, STATIONS } from './mockFleet.js';
import { createScene } from './scene.js';

const initialTime = Date.now() / 1000;
const initialFleet = buildFleetState(initialTime);

function formatTime(t) {
  return new Date(t * 1000).toISOString().slice(11, 19);
}

function App() {
  const globeElement = useRef(null);
  const scene = useRef(null);
  const [fleet, setFleet] = useState(initialFleet);
  const [selected, setSelected] = useState('SAT-07');
  const [sceneError, setSceneError] = useState(false);

  useEffect(() => {
    let active = true;
    const wallStart = performance.now();
    try {
      scene.current = createScene(globeElement.current, initialFleet, setSelected);
    } catch (error) {
      console.error('Cesium scene failed to initialize', error);
      if (active) setSceneError(true);
    }

    const timer = window.setInterval(() => {
      const simTime = initialTime + (performance.now() - wallStart) / 1000 * 120;
      const state = buildFleetState(simTime);
      scene.current?.update(state);
      setFleet(state);
    }, 100);
    return () => {
      active = false;
      window.clearInterval(timer);
      scene.current?.destroy();
      scene.current = null;
    };
  }, []);

  const activeSat = fleet.sats.find((sat) => sat.sat_id === selected) ?? fleet.sats[0];
  const sunlight = fleet.sats.filter((sat) => sat.illum >= 0.95).length;
  const penumbra = fleet.sats.filter((sat) => sat.illum > 0.05 && sat.illum < 0.95).length;
  const eclipse = fleet.sats.length - sunlight - penumbra;

  return (
    <div className="app-shell">
      <header className="masthead">
        <div className="brand">
          <div className="brand-mark"><span /></div>
          <div>
            <div className="eyebrow">VERIFIED MISSION OPS</div>
            <h1>PENUMBRA<span className="brand-period">.</span></h1>
          </div>
        </div>
        <div className="header-center"><span className="tiny-cross">✳</span> FLEET OVERVIEW <span className="header-separator">/</span> ORBITAL SHADOW MONITOR</div>
        <div className="header-right">
          <span className="live-dot" />
          <span>SIMULATED PREVIEW</span>
          <span className="header-time">{formatTime(fleet.t)} UTC</span>
        </div>
      </header>

      <main className="main-grid">
        <aside className="left-rail">
          <div className="rail-heading">
            <span className="section-index">01 / CONSTELLATION</span>
            <span className="panel-icon">↗</span>
          </div>
          <h2>Twenty-four eyes<br />on the dark.</h2>
          <p className="rail-copy">Watch the fleet cross Earth's shadow in accelerated time. Each point fades through penumbra before eclipse.</p>
          <div className="rail-divider" />
          <div className="small-label">FLEET STATUS <span>24 ACTIVE</span></div>
          <div className="sat-grid" aria-label="Satellite selector">
            {fleet.sats.map((sat) => (
              <button
                key={sat.sat_id}
                className={`sat-chip ${selected === sat.sat_id ? 'selected' : ''}`}
                onClick={() => setSelected(sat.sat_id)}
                title={`${sat.sat_id}: ${sat.status}`}
              >
                <span className={`chip-light ${sat.status.toLowerCase()}`} />
                {sat.sat_id.slice(-2)}
              </button>
            ))}
          </div>
          <div className="rail-divider" />
          <div className="small-label">GROUND NETWORK <span>3 STATIONS</span></div>
          <div className="station-list">
            {STATIONS.map((station, index) => (
              <div className="station-row" key={station.id}>
                <span className="station-number">0{index + 1}</span>
                <span>{station.id}</span>
                <span className="station-ring">◉</span>
              </div>
            ))}
          </div>
          <div className="rail-footer">AI PROPOSES <span>·</span> PHYSICS VERIFIES <span>·</span> HUMANS APPROVE</div>
        </aside>

        <section className="globe-panel" aria-label="Live simulated fleet globe">
          <div ref={globeElement} className="cesium-canvas" />
          <div className="globe-vignette" aria-hidden="true" />
          <div className="globe-topline"><span className="crosshair">＋</span> EARTH VIEW <span className="muted">//</span> ORBITAL PLANE 01–06</div>
          <div className="globe-upper-right"><span className="readout-label">SCENE RATE</span><strong>120×</strong><span className="readout-label">SIM TIME</span></div>
          <div className="globe-bottomline"><span>DRAG TO ROTATE · SCROLL TO ZOOM</span><span>← SUNWARD <b className="globe-line" /> SHADOW AXIS →</span></div>
          {sceneError && <div className="scene-error">Globe renderer unavailable. The fleet preview remains active.</div>}
        </section>

        <aside className="right-rail">
          <div className="rail-heading"><span className="section-index">02 / LIVE READOUT</span><span className="panel-icon">◎</span></div>
          <div className="focus-title">IN FOCUS <span>↘</span></div>
          <div className="focus-id">{activeSat.sat_id}</div>
          <div className="focus-status"><span className={`status-dot ${activeSat.status.toLowerCase()}`} />{activeSat.status}</div>
          <div className="readout-grid">
            <div><span>LATITUDE</span><strong>{Math.abs(activeSat.lat_deg).toFixed(1)}° {activeSat.lat_deg >= 0 ? 'N' : 'S'}</strong></div>
            <div><span>LONGITUDE</span><strong>{Math.abs(activeSat.lon_deg).toFixed(1)}° {activeSat.lon_deg >= 0 ? 'E' : 'W'}</strong></div>
            <div><span>ALTITUDE</span><strong>{activeSat.alt_km.toFixed(0)} <small>KM</small></strong></div>
            <div><span>ILLUMINATION</span><strong>{Math.round(activeSat.illum * 100)}<small>%</small></strong></div>
          </div>
          <div className="shadow-block">
            <div className="small-label">SHADOW GEOMETRY <span>LIVE</span></div>
            <div className="shadow-diagram">
              <div className="sun-icon">☼</div>
              <div className="beam" />
              <div className="earth-disc" />
              <div className="shadow-cone" />
              <div className="orbit-dash" />
              <span className="diagram-label">UMBRA</span>
            </div>
            <p>Solar visibility transitions continuously at the shadow boundary.</p>
          </div>
          <div className="legend-block">
            <div className="small-label">DATA PROVENANCE <span>ALWAYS VISIBLE</span></div>
            <div className="legend-row"><span className="legend-key real">REAL</span><span>Orbit elements, Sun geometry, stations, contacts in live mode</span></div>
            <div className="legend-row"><span className="legend-key simulated">SIMULATED</span><span>Power, thermal, faults; local preview orbit positions</span></div>
          </div>
        </aside>
      </main>

      <footer className="ticker">
        <div className="ticker-heading"><span className="ticker-pulse" /> FLEET TELEMETRY</div>
        <div className="ticker-stat"><span>TRACKED</span><strong>24 / 24</strong></div>
        <div className="ticker-stat"><span>DAYLIGHT</span><strong>{String(sunlight).padStart(2, '0')}</strong></div>
        <div className="ticker-stat penumbra-stat"><span>PENUMBRA</span><strong>{String(penumbra).padStart(2, '0')}</strong></div>
        <div className="ticker-stat eclipse-stat"><span>ECLIPSE</span><strong>{String(eclipse).padStart(2, '0')}</strong></div>
        <div className="ticker-stat"><span>SIM CLOCK</span><strong>120×</strong></div>
        <div className="ticker-source">LOCAL FLEET_STATE MOCK <span>●</span> OFFLINE READY</div>
      </footer>
    </div>
  );
}

export default App;
