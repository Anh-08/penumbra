// Power-path animation (T37, reused by T38): pulses travel along right-angle paths of the skeleton.
// Sunlit: wings -> PCDU -> battery (charging) and PCDU -> every load that is on.
// Eclipse: battery -> PCDU -> loads. Essential loads (BUS, COMMS, ADCS) cannot be switched off.
// This is an illustration of where current goes, not a physics model: no numbers come out of it.
import { LOAD_PARTS } from './skeleton.js';

export const ESSENTIAL_LOADS = ['BUS', 'COMMS', 'ADCS'];
const DEFAULTS = { sunlit: true, loadsOn: { PAYLOAD: true, HEATER: true, AUX: false }, color: '#ffb020',
  pulses: 3, speed: 0.45, pointSize: 0.05, lineOpacity: 0.28 };

// Manhattan polyline a -> b (x first, then y, then z): the blueprint look, no curves.
export function manhattan(a, b) {
  const p1 = [b[0], a[1], a[2]];
  const p2 = [b[0], b[1], a[2]];
  return [a, p1, p2, b].filter((p, i, all) => i === 0 || p.some((v, k) => v !== all[i - 1][k]));
}

function lengths(path) {
  const seg = [];
  for (let i = 1; i < path.length; i += 1) {
    seg.push(Math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1], path[i][2] - path[i - 1][2]));
  }
  return seg;
}

// Point at fraction u (0..1) of the polyline.
export function pointAt(path, u) {
  const seg = lengths(path);
  const total = seg.reduce((s, v) => s + v, 0) || 1;
  let d = (((u % 1) + 1) % 1) * total;
  for (let i = 0; i < seg.length; i += 1) {
    if (d <= seg[i] || i === seg.length - 1) {
      const t = seg[i] ? Math.min(1, d / seg[i]) : 0;
      return path[i].map((v, k) => v + (path[i + 1][k] - v) * t);
    }
    d -= seg[i];
  }
  return path[path.length - 1];
}

export function animatePowerFlow(THREE, skeleton, options = {}) {
  const opt = { ...DEFAULTS, ...options, loadsOn: { ...DEFAULTS.loadsOn, ...(options.loadsOn ?? {}) } };
  const color = new THREE.Color(opt.color);
  const group = new THREE.Group();
  group.name = 'power-flow';
  skeleton.group.add(group);
  let sunlit = opt.sunlit;
  const loadsOn = { BUS: true, COMMS: true, ADCS: true, ...opt.loadsOn };
  let clock = 0;
  let paths = [];
  let points = null;
  let lines = null;

  const local = (id) => {
    const v = skeleton.anchor(id);
    if (!v) return null;
    skeleton.group.worldToLocal(v);
    return [v.x, v.y, v.z];
  };

  function buildPaths() {
    const pcdu = local('pcdu');
    const battery = local('battery');
    const out = [];
    if (!pcdu || !battery) return out;
    if (sunlit) {
      for (const wing of ['wing_py', 'wing_my']) {
        const w = local(wing);
        if (w) out.push({ from: wing, to: 'pcdu', path: manhattan(w, pcdu) });
      }
      out.push({ from: 'pcdu', to: 'battery', path: manhattan(pcdu, battery) });
    } else {
      out.push({ from: 'battery', to: 'pcdu', path: manhattan(battery, pcdu) });
    }
    for (const [load, partId] of Object.entries(LOAD_PARTS)) {
      if (!loadsOn[load] || !skeleton.hasAnchor(partId)) continue;
      const target = local(partId);
      if (target) out.push({ from: 'pcdu', to: partId, load, path: manhattan(pcdu, target) });
    }
    return out;
  }

  function rebuild() {
    if (points) { points.geometry.dispose(); points.material.dispose(); group.remove(points); }
    if (lines) { lines.geometry.dispose(); lines.material.dispose(); group.remove(lines); }
    paths = buildPaths();
    const segs = [];
    for (const { path } of paths) for (let i = 1; i < path.length; i += 1) segs.push(...path[i - 1], ...path[i]);
    const lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute('position', new THREE.Float32BufferAttribute(segs, 3));
    lines = new THREE.LineSegments(lineGeo, new THREE.LineBasicMaterial({ color, transparent: true,
      opacity: opt.lineOpacity }));
    const pointGeo = new THREE.BufferGeometry();
    pointGeo.setAttribute('position', new THREE.Float32BufferAttribute(new Array(paths.length * opt.pulses * 3).fill(0), 3));
    points = new THREE.Points(pointGeo, new THREE.PointsMaterial({ color, size: opt.pointSize, sizeAttenuation: true }));
    group.add(lines, points);
    for (const [load, partId] of Object.entries(LOAD_PARTS)) skeleton.setPartOn(partId, loadsOn[load] !== false);
    place();
  }

  function place() {
    const pos = points.geometry.getAttribute('position');
    let k = 0;
    for (const { path } of paths) {
      for (let j = 0; j < opt.pulses; j += 1) {
        const p = pointAt(path, clock * opt.speed + j / opt.pulses);
        pos.setXYZ(k, p[0], p[1], p[2]);
        k += 1;
      }
    }
    pos.needsUpdate = true;
  }

  rebuild();

  return {
    group,
    get paths() { return paths.map(({ from, to, load }) => ({ from, to, load })); },
    get sunlit() { return sunlit; },
    update(dtSeconds) {
      clock += Math.max(0, dtSeconds || 0);
      place();
    },
    pulsePositions() {
      const pos = points.geometry.getAttribute('position');
      return Array.from({ length: pos.count }, (_, i) => [pos.getX(i), pos.getY(i), pos.getZ(i)]);
    },
    setSunlit(value) {
      sunlit = Boolean(value);
      rebuild();
    },
    isLoadOn: (load) => loadsOn[load] !== false,
    setLoad(load, on) {
      if (!(load in LOAD_PARTS)) return false;
      if (ESSENTIAL_LOADS.includes(load) && !on) return false;  // essential loads are never off
      loadsOn[load] = Boolean(on);
      rebuild();
      return true;
    },
    dispose() {
      for (const obj of [points, lines]) { if (obj) { obj.geometry.dispose(); obj.material.dispose(); } }
      skeleton.group.remove(group);
      paths = [];
    },
  };
}
