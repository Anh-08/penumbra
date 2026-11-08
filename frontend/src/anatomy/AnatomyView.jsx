// SATELLITE ANATOMY full-page view (T37). Lazy-loaded with three, so the landing page never pays for it.
// Three columns: callout boxes left and right, the wireframe in the middle, so boxes never cover the model.
// Leader lines are right angles (horizontal, vertical, horizontal), redrawn every frame from projected anchors.
import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import table from '../data/satellite_anatomy.json';
import { createSatelliteSkeleton, LOAD_PARTS } from './skeleton.js';
import { animatePowerFlow } from './powerFlow.js';
import { focusPart, SWITCHABLE } from './anatomyState.js';
import './anatomy.css';

const PARTS = Object.fromEntries(table.parts.map((p) => [p.id, p]));
const PART_LOAD = Object.fromEntries(Object.entries(LOAD_PARTS).map(([load, part]) => [part, load]));

function cssColor(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch { return fallback; }
}

export function loadLabel(part) {
  if (part.kind !== 'load') return part.kind.toUpperCase();
  return `${part.load} · ${table.loads[part.load]?.essential ? 'ESSENTIAL' : 'NON-ESSENTIAL'}`;
}

function Callout({ id, focus, pinned, state, dispatch, boxRefs }) {
  const part = PARTS[id];
  const load = PART_LOAD[id];
  const switchable = state.powerPath && SWITCHABLE.includes(load);
  const off = state.powerPath && load && state.loadsOn[load] === false;
  const onClick = () => (switchable ? dispatch({ type: 'toggle_load', load }) : dispatch({ type: 'click', part: id }));
  return <li>
    <button type="button" ref={(el) => { boxRefs.current[id] = el; }}
      className={`anatomy-callout${focus === id ? ' lit' : ''}${off ? ' off' : ''}`}
      aria-pressed={pinned === id}
      aria-label={`${part.name}: ${part.role}${switchable ? ' Click to switch this load.' : ''}`}
      onMouseEnter={() => dispatch({ type: 'hover', part: id })} onMouseLeave={() => dispatch({ type: 'hover', part: null })}
      onFocus={() => dispatch({ type: 'hover', part: id })} onBlur={() => dispatch({ type: 'hover', part: null })}
      onClick={onClick}>
      <span className="callout-head"><strong>{part.name}</strong><span className="callout-load">{off ? 'OFF' : loadLabel(part)}</span></span>
      <span className="callout-role">{part.role}</span>
      {(part.hypotheses.length > 0 || part.figures.length > 0) && <span className="callout-meta">
        {part.hypotheses.map((h) => <span key={h} className="callout-hyp">{h} {table.hypotheses[h]}</span>)}
        {part.figures.map((f) => <span key={f.label} className="callout-fig" title={f._source}>{f.label} {f.value}</span>)}
      </span>}
    </button>
  </li>;
}

export default function AnatomyView({ state, dispatch, legend }) {
  const stageRef = useRef(null);
  const canvasRef = useRef(null);
  const bodyRef = useRef(null);
  const boxRefs = useRef({});
  const leaderRefs = useRef({});
  const closeRef = useRef(null);
  const rig = useRef(null);
  const latest = useRef(state);
  latest.current = state;

  // three scene: built once per open view
  useEffect(() => {
    const canvas = canvasRef.current;
    const stage = stageRef.current;
    const reduced = typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 100);
    const sat = createSatelliteSkeleton(THREE, { ink: cssColor('--c-ink', '#e9eff7'), accent: cssColor('--c-alert', '#ffb224') });
    sat.group.rotation.set(0.12, -0.55, 0);
    scene.add(sat.group);
    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.enablePan = false;
    controls.autoRotate = !reduced;
    controls.autoRotateSpeed = 0.55;
    const sphere = new THREE.Box3().setFromObject(sat.group).getBoundingSphere(new THREE.Sphere());

    function fit() {
      const w = Math.max(1, stage.clientWidth);
      const h = Math.max(1, stage.clientHeight);
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      const vfov = THREE.MathUtils.degToRad(camera.fov);
      const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
      const dist = sphere.radius / Math.sin(Math.min(vfov, hfov) / 2) * 1.04;
      camera.position.copy(sphere.center).add(new THREE.Vector3(0.55, 0.35, 0.76).normalize().multiplyScalar(dist));
      camera.near = dist / 50; camera.far = dist * 4;
      camera.updateProjectionMatrix();
      controls.target.copy(sphere.center);
      controls.minDistance = dist * 0.45; controls.maxDistance = dist * 1.8;
      controls.update();
      controls.saveState();
    }
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(stage);

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let hoverId = null;
    let down = null;
    function pick(event) {
      const rect = canvas.getBoundingClientRect();
      pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(sat.pickables, false)[0];
      return hit?.object.userData.partId ?? null;
    }
    const onMove = (event) => {
      const id = pick(event);
      if (id !== hoverId) { hoverId = id; dispatch({ type: 'hover', part: id }); }
      canvas.style.cursor = id ? 'pointer' : 'grab';
    };
    const onLeave = () => { if (hoverId) { hoverId = null; dispatch({ type: 'hover', part: null }); } };
    const onDown = (event) => { down = [event.clientX, event.clientY]; };
    const onUp = (event) => {
      if (!down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 4) return;  // a drag, not a click
      const id = pick(event);
      const load = PART_LOAD[id];
      const s = latest.current;
      if (s.powerPath && SWITCHABLE.includes(load)) dispatch({ type: 'toggle_load', load });
      else if (id) dispatch({ type: 'click', part: id });
    };
    const onDbl = () => controls.reset();
    canvas.addEventListener('pointermove', onMove);
    canvas.addEventListener('pointerleave', onLeave);
    canvas.addEventListener('pointerdown', onDown);
    canvas.addEventListener('pointerup', onUp);
    canvas.addEventListener('dblclick', onDbl);

    const anchor = new THREE.Vector3();
    let raf = 0;
    let last = performance.now();
    function frame(now) {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      controls.update();
      rig.current?.flow?.update(dt);
      renderer.render(scene, camera);
      drawLeaders();
      raf = requestAnimationFrame(frame);
    }
    function drawLeaders() {
      const body = bodyRef.current;
      if (!body) return;
      const b = body.getBoundingClientRect();
      const s = canvas.getBoundingClientRect();
      const focus = focusPart(latest.current);
      for (const { part: id, side } of table.callouts) {
        const path = leaderRefs.current[id];
        const box = boxRefs.current[id];
        if (!path || !box) continue;
        sat.anchor(id, anchor).project(camera);
        const ax = s.left - b.left + (anchor.x + 1) / 2 * s.width;
        const ay = s.top - b.top + (1 - anchor.y) / 2 * s.height;
        const r = box.getBoundingClientRect();
        const ex = side === 'left' ? r.right - b.left : r.left - b.left;
        const ey = r.top - b.top + r.height / 2;
        const knee = side === 'left' ? s.left - b.left + 14 : s.right - b.left - 14;
        path.setAttribute('d', `M${ex},${ey} H${knee} V${ay} H${ax}`);
        path.setAttribute('class', focus === id ? 'lit' : '');
      }
    }
    raf = requestAnimationFrame(frame);
    rig.current = { THREE, sat, flow: null };
    closeRef.current?.focus();

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      for (const [type, fn] of [['pointermove', onMove], ['pointerleave', onLeave], ['pointerdown', onDown],
        ['pointerup', onUp], ['dblclick', onDbl]]) canvas.removeEventListener(type, fn);
      rig.current?.flow?.dispose();
      controls.dispose();
      sat.dispose();
      renderer.dispose();
      rig.current = null;
    };
  }, [dispatch]);

  // highlight follows the state; power path is created, switched and removed with the toggles
  const focus = focusPart(state);
  useEffect(() => { rig.current?.sat.setHighlight(focus); }, [focus]);
  useEffect(() => {
    const r = rig.current;
    if (!r) return;
    if (!state.powerPath) {
      r.flow?.dispose();
      r.flow = null;
      for (const part of Object.values(LOAD_PARTS)) r.sat.setPartOn(part, true);
      return;
    }
    if (!r.flow) r.flow = animatePowerFlow(r.THREE, r.sat, { sunlit: state.sunlit, loadsOn: state.loadsOn });
    else {
      if (r.flow.sunlit !== state.sunlit) r.flow.setSunlit(state.sunlit);
      for (const load of SWITCHABLE) if (r.flow.isLoadOn(load) !== state.loadsOn[load]) r.flow.setLoad(load, state.loadsOn[load]);
    }
  }, [state.powerPath, state.sunlit, state.loadsOn]);

  const side = (s) => table.callouts.filter((c) => c.side === s).map((c) => c.part);
  return <div className="anatomy-view" role="dialog" aria-modal="true" aria-label="Satellite anatomy">
    <header className="anatomy-head">
      <div className="anatomy-title"><strong>SATELLITE ANATOMY</strong><span>{table.class_label}</span><span className="source-tag">ILLUSTRATIVE</span></div>
      <div className="anatomy-actions">
        <button type="button" className="anatomy-toggle" aria-pressed={state.powerPath}
          onClick={() => dispatch({ type: 'toggle_power_path' })}>POWER PATH</button>
        {state.powerPath && <button type="button" className="anatomy-toggle" aria-pressed={!state.sunlit}
          onClick={() => dispatch({ type: 'toggle_sun' })}>{state.sunlit ? 'SUNLIT' : 'ECLIPSE'}</button>}
        <button type="button" ref={closeRef} className="anatomy-close" aria-label="Close the anatomy view (Esc)"
          onClick={() => dispatch({ type: 'close' })}>CLOSE ✕</button>
      </div>
    </header>
    <div className="anatomy-body" ref={bodyRef}>
      <ul className="anatomy-col">{side('left').map((id) => <Callout key={id} id={id} focus={focus} pinned={state.pinned}
        state={state} dispatch={dispatch} boxRefs={boxRefs} />)}</ul>
      <div className="anatomy-stage" ref={stageRef}><canvas ref={canvasRef} aria-label="Rotatable wireframe of the illustrative bus" /></div>
      <ul className="anatomy-col">{side('right').map((id) => <Callout key={id} id={id} focus={focus} pinned={state.pinned}
        state={state} dispatch={dispatch} boxRefs={boxRefs} />)}</ul>
      <svg className="anatomy-leaders" aria-hidden="true">
        {table.callouts.map(({ part }) => <path key={part} ref={(el) => { leaderRefs.current[part] = el; }} />)}
      </svg>
    </div>
    <footer className="anatomy-foot">
      <span>{table.disclaimer}.{state.powerPath ? ' Power path: where current flows, not a physics run; essential loads stay on.' : ' Drag to orbit · scroll to zoom · double-click to reset · click to pin.'}</span>
      {legend}
    </footer>
  </div>;
}
