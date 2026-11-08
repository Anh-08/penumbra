// MONTE CARLO grid renderer (T38). One WebGL canvas, one shared skeleton from ../anatomy, drawn once per tile with
// scissor viewports, every tile from the same camera. A 2D canvas draws the paper cross-section mode.
// Illustration only: per-tile state comes from mcState (hypothesis, sampled outcome); nothing here is physics.
import { animatePowerFlow, createSatelliteSkeleton, createSchematic, drawSchematic } from '../anatomy/index.js';
import { socCue, sunlit } from './mcState.js';

const NEAR = 0.32;          // a pulse this close to the faulty part tints it red
const RED_DECAY = 6;        // 1/s: how fast the tint fades after the pulse has passed
const BATTERY_CENTER = [0, -0.42, 0];
const H3_SCALE = 0.72;      // H3: the battery outline shrinks
const H5_TILT = 0.95;       // rad: H5 tilts the Sun arrow
const FLOW_SPEED = 0.45;

function cssColor(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch { return fallback; }
}

export function palette() {
  return {
    ink: cssColor('--c-ink', '#e9eff7'), ink2: cssColor('--c-ink-2', '#a3b1c4'), ink3: cssColor('--c-ink-3', '#74839a'),
    current: cssColor('--c-alert', '#ffb224'), alert: cssColor('--c-refused', '#ff5468'),
    panel: cssColor('--c-panel-solid', '#0b111a'),
  };
}

// The 2D cross-section: connectors between schematic anchors (horizontal, then vertical).
const SCHEMATIC_LOADS = ['obc', 'sband', 'adcs', 'payload', 'heater'];
function schematicPaths(sch, { sun, aux }) {
  const a = sch.anchors;
  const pcdu = a.pcdu;
  const out = [];
  if (sun) {
    out.push([[a.wings[0], a.wings[1]], [pcdu[0], a.wings[1]], [pcdu[0], pcdu[1]]]);
    out.push([[120 - a.wings[0], a.wings[1]], [pcdu[0], a.wings[1]], [pcdu[0], pcdu[1]]]);
    out.push([pcdu, a.battery]);
  } else {
    out.push([a.battery, pcdu]);
  }
  for (const id of [...SCHEMATIC_LOADS, ...(aux ? ['aux'] : [])]) {
    const p = a[id];
    out.push([pcdu, [p[0], pcdu[1]], p]);
  }
  return out;
}

export function createMcRenderer(THREE, { canvas3d, canvas2d, reducedMotion = false }) {
  const colors = palette();
  let renderer = null;
  try {
    renderer = new THREE.WebGLRenderer({ canvas: canvas3d, antialias: true, alpha: true, powerPreference: 'low-power' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setScissorTest(true);
    renderer.autoClear = true;
  } catch (error) {
    console.warn('MC grid: WebGL unavailable, 2D only', error);
    renderer = null;
  }
  const ctx = canvas2d.getContext('2d');
  const sch = createSchematic();

  const scene = new THREE.Scene();
  const skeleton = createSatelliteSkeleton(THREE, { detail: 'simple', ink: colors.ink, accent: colors.current });
  scene.add(skeleton.group);
  const flows = {};
  for (const sun of [true, false]) {
    for (const aux of [false, true]) {
      const f = animatePowerFlow(THREE, skeleton, { sunlit: sun, color: colors.current, pointSize: 0.045,
        speed: FLOW_SPEED, loadsOn: { PAYLOAD: true, HEATER: true, AUX: aux } });
      flows[`${sun ? 's' : 'e'}${aux ? 'a' : ''}`] = f;
    }
  }
  const sunDir = new THREE.Vector3(1, -0.55, -0.35).normalize();
  const sunOrigin = new THREE.Vector3(-1.9, 1.35, 0.7);
  const arrow = new THREE.ArrowHelper(sunDir, sunOrigin, 0.8, colors.current, 0.2, 0.12);
  scene.add(arrow);
  const tilted = sunDir.clone().applyAxisAngle(new THREE.Vector3(0, 0, 1), H5_TILT);

  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 60);
  camera.position.set(3.0, 1.95, 4.4);
  camera.lookAt(0, -0.05, 0);

  // Local anchors of the parts that can carry a fault cue (the skeleton is not rotated yet).
  const local = (id) => { const v = skeleton.anchor(id); skeleton.group.worldToLocal(v); return v; };
  const faultAnchors = { wings: [local('wing_py'), local('wing_my')], aux: [local('aux')],
    battery: [local('battery')], pcdu: [local('pcdu')] };
  const ink = new THREE.Color(colors.ink);
  const red = new THREE.Color(colors.alert);
  const amber = new THREE.Color(colors.current);
  const tmp = new THREE.Color();
  const panel = new THREE.Color(colors.panel);

  let model = { tiles: [], run: 0 };
  let layout = { tiles: [], spot: null, width: 1, height: 1 };
  let mode = renderer ? '3d' : '2d';
  let expanded = null;
  let startedAt = 0;
  let resolvedAt = null;
  let lastNow = null;
  const redLevel = new Map();   // tile index -> current red tint (decays)
  const stats = { frames: 0, totalMs: 0, maxMs: 0, lastMs: 0 };

  function near(flowKey, part) {
    const anchors = faultAnchors[part];
    if (!anchors) return 0;
    let best = 0;
    for (const p of flows[flowKey].pulsePositions()) {
      for (const a of anchors) {
        const d = Math.hypot(p[0] - a.x, p[1] - a.y, p[2] - a.z);
        best = Math.max(best, 1 - d / NEAR);
      }
    }
    return best;
  }

  function paintTile(tile, sun, tint) {
    const h = tile.hyp;
    for (const [id, p] of skeleton.parts) {
      let opacity = 0.85;
      tmp.copy(ink);
      if (id === 'aux') opacity = h === 'H2' ? 1 : 0.14;           // AUX is normally off; H2: stuck on, glowing
      if (id === 'aux' && h === 'H2') tmp.copy(amber);
      if (id === 'wings' && h === 'H1') opacity = 0.32;             // H1: wings dim
      if (id === 'wings' && !sun) opacity *= 0.55;                  // eclipse: no current from the wings
      if (tile.fault === id && tint > 0) { tmp.lerp(red, tint); opacity = Math.max(opacity, 0.6 + 0.4 * tint); }
      for (const l of p.lines) { l.material.color.copy(tmp); l.material.opacity = opacity; }
      for (const f of p.fills) { f.material.color.copy(red); f.material.opacity = tile.fault === id ? 0.22 * tint : 0; }
      if (id === 'battery') {
        const s = h === 'H3' ? H3_SCALE : 1;
        p.group.scale.setScalar(s);
        p.group.position.set(BATTERY_CENTER[0] * (1 - s), BATTERY_CENTER[1] * (1 - s), BATTERY_CENTER[2] * (1 - s));
      }
    }
    arrow.visible = sun;
    arrow.setDirection(h === 'H5' ? tilted : sunDir);
    arrow.setColor(h === 'H5' && tint > 0 ? tmp.copy(amber).lerp(red, tint) : amber);
    const key = `${sun ? 's' : 'e'}${h === 'H2' ? 'a' : ''}`;
    for (const [k, f] of Object.entries(flows)) f.group.visible = k === key;
    return key;
  }

  function drawTile3d(tile, rect, t, dt, steady) {
    const sun = sunlit(tile.hyp, t);
    const key = `${sun ? 's' : 'e'}${tile.hyp === 'H2' ? 'a' : ''}`;
    const hit = tile.fault ? near(key, tile.fault) : 0;
    const prev = redLevel.get(tile.index) ?? 0;
    let tint = Math.max(hit > 0 ? 1 : 0, prev * Math.exp(-RED_DECAY * dt));
    if (reducedMotion && tile.fault) tint = 1;           // static tiles: the faulty part stays red
    redLevel.set(tile.index, tint);
    if (steady && tile.fault) tint = Math.max(tint, 0.6);  // spotlight: the faulty part is always highlighted
    paintTile(tile, sun, tint);
    const y = layout.height - rect.y - rect.h;
    renderer.setViewport(rect.x, y, rect.w, rect.h);
    renderer.setScissor(rect.x, y, rect.w, rect.h);
    renderer.setClearColor(panel, 0.92);
    camera.aspect = rect.w / Math.max(1, rect.h);
    camera.updateProjectionMatrix();
    renderer.render(scene, camera);
  }

  function drawTile2d(tile, rect, t) {
    const sun = sunlit(tile.hyp, t);
    ctx.save();
    ctx.fillStyle = colors.panel;
    ctx.globalAlpha = 0.94;
    ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
    ctx.globalAlpha = 1;
    const scale = Math.min(rect.w / sch.width, rect.h / sch.height) * 0.86;
    const x = rect.x + (rect.w - sch.width * scale) / 2;
    const y = rect.y + (rect.h - sch.height * scale) / 2 + rect.h * 0.04;
    drawSchematic(ctx, sch, { x, y, scale, fault: tile.hyp, ink: colors.ink2, alert: colors.alert });
    ctx.translate(x, y);
    ctx.scale(scale, scale);
    ctx.strokeStyle = colors.current;
    ctx.lineWidth = 1.2 / scale;
    ctx.setLineDash([2.2, 2.8]);
    ctx.lineDashOffset = reducedMotion ? 0 : -t * 9;
    for (const path of schematicPaths(sch, { sun, aux: tile.hyp === 'H2' })) {
      ctx.beginPath();
      ctx.moveTo(path[0][0], path[0][1]);
      for (const p of path.slice(1)) ctx.lineTo(p[0], p[1]);
      ctx.stroke();
    }
    ctx.restore();
  }

  function frame(now, socEls = []) {
    const t0 = performance.now();
    const dt = lastNow === null ? 0 : Math.min(0.1, (now - lastNow) / 1000);
    lastNow = now;
    const t = reducedMotion ? 1.0 : Math.max(0, (now - startedAt) / 1000);
    if (!reducedMotion) for (const f of Object.values(flows)) f.update(dt);
    const rotation = reducedMotion ? -0.35 : -0.35 + 0.18 * Math.sin(t * 0.35);
    skeleton.group.rotation.y = rotation;   // the whole grid breathes together: same orientation in every tile
    const spot = expanded !== null && layout.spot;
    const draws = spot ? [[model.tiles[expanded], layout.spot]] : model.tiles.map((tile, i) => [tile, layout.tiles[i]]);
    if (mode === '3d' && renderer) {
      for (const [tile, rect] of draws) if (tile && rect && rect.w > 2 && rect.h > 2) drawTile3d(tile, rect, t, dt, spot);
    } else {
      ctx.clearRect(0, 0, layout.width, layout.height);
      for (const [tile, rect] of draws) if (tile && rect && rect.w > 2 && rect.h > 2) drawTile2d(tile, rect, t);
    }
    // SOC bars and current gauges: plain DOM transforms, no React render per frame.
    const resolvedFor = resolvedAt === null ? null : Math.max(0, (now - resolvedAt) / 1000);
    model.tiles.forEach((tile, i) => {
      const el = socEls[i];
      if (!el) return;
      const v = socCue(tile.hyp, t, tile.outcome, reducedMotion && resolvedAt !== null ? 99 : resolvedFor);
      el.bar?.style.setProperty('transform', `scaleY(${v.toFixed(3)})`);
      if (el.gauge) {
        const sun = sunlit(tile.hyp, t);
        const offset = tile.hyp === 'H4' ? 0.32 : 0;               // H4: the current gauge reads offset
        el.gauge.style.setProperty('transform', `translateX(${(((sun ? 0.3 : -0.3) + offset) * 50).toFixed(1)}%)`);
      }
    });
    const ms = performance.now() - t0;
    stats.frames += 1; stats.totalMs += ms; stats.maxMs = Math.max(stats.maxMs, ms); stats.lastMs = ms;
    return ms;
  }

  function clearAll() {
    if (renderer) {
      renderer.setScissorTest(false);
      renderer.setClearColor(0x000000, 0);
      renderer.clear();
      renderer.setScissorTest(true);
    }
    ctx.clearRect(0, 0, canvas2d.width, canvas2d.height);
  }

  return {
    get mode() { return mode; },
    get webgl() { return Boolean(renderer); },
    stats,
    setModel(next, { runStartedAt, outcomeAt }) {
      if (next.run !== model.run) redLevel.clear();
      model = next;
      startedAt = runStartedAt ?? startedAt;
      resolvedAt = outcomeAt ?? null;
    },
    setMode(next) {
      mode = next === '2d' || !renderer ? '2d' : '3d';
      clearAll();
    },
    setExpanded(index) { expanded = index; clearAll(); },
    setLayout(next) {
      layout = next;
      if (renderer) renderer.setSize(next.width, next.height, false);
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
      canvas2d.width = Math.round(next.width * ratio);
      canvas2d.height = Math.round(next.height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      clearAll();
    },
    clear: clearAll,
    frame,
    dispose() {
      for (const f of Object.values(flows)) f.dispose();
      skeleton.dispose();
      arrow.dispose?.();
      renderer?.dispose();
    },
  };
}
