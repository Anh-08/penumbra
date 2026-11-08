// Procedural wireframe "blueprint" of the illustrative bus (T37). No textures, no external files: every part is
// EdgesGeometry line segments plus an almost invisible fill mesh used for picking and the highlight tint.
// THREE is passed in so callers can lazy-load it (import('three')) and tests can run headless in Node.

export const PART_IDS = [
  'bus', 'wings', 'battery', 'pcdu', 'obc', 'adcs', 'star_tracker', 'gnss', 'sband', 'payload', 'heater', 'aux',
  'propulsion',
];
// Parts kept in the 'simple' detail level (T38 tiles): structure, power chain and the switchable loads.
export const SIMPLE_PART_IDS = ['bus', 'wings', 'battery', 'pcdu', 'obc', 'sband', 'adcs', 'payload', 'heater', 'aux'];
// Which part carries each EPS load (backend/eps/params.py load names).
export const LOAD_PARTS = { BUS: 'obc', COMMS: 'sband', ADCS: 'adcs', PAYLOAD: 'payload', HEATER: 'heater', AUX: 'aux' };

const DEFAULTS = { detail: 'full', ink: '#d6e0ea', accent: '#ffb020', baseOpacity: 0.85, dimOpacity: 0.2, offOpacity: 0.12 };

export function createSatelliteSkeleton(THREE, options = {}) {
  const opt = { ...DEFAULTS, ...options };
  const simple = opt.detail === 'simple';
  const keep = new Set(simple ? SIMPLE_PART_IDS : PART_IDS);
  const ink = new THREE.Color(opt.ink);
  const accent = new THREE.Color(opt.accent);
  const group = new THREE.Group();
  group.name = 'satellite-skeleton';
  const parts = new Map();
  const anchors = new Map();
  const pickables = [];
  const disposables = [];
  let highlighted = null;

  function part(id) {
    if (!parts.has(id)) {
      const g = new THREE.Group();
      g.name = `part:${id}`;
      group.add(g);
      parts.set(id, { id, group: g, lines: [], fills: [], on: true });
    }
    return parts.get(id);
  }

  function addShape(id, geometry, position, rotation) {
    if (!keep.has(id)) { geometry.dispose(); return; }
    const p = part(id);
    const lineMat = new THREE.LineBasicMaterial({ color: ink.clone(), transparent: true, opacity: opt.baseOpacity });
    const edges = new THREE.EdgesGeometry(geometry, 20);
    const lines = new THREE.LineSegments(edges, lineMat);
    const fillMat = new THREE.MeshBasicMaterial({ color: accent.clone(), transparent: true, opacity: 0.0,
      depthWrite: false, side: THREE.DoubleSide });
    const fill = new THREE.Mesh(geometry, fillMat);
    for (const obj of [lines, fill]) {
      obj.position.set(...position);
      if (rotation) obj.rotation.set(...rotation);
      obj.userData.partId = id;
      p.group.add(obj);
    }
    p.lines.push(lines);
    p.fills.push(fill);
    pickables.push(fill);
    disposables.push(geometry, edges, lineMat, fillMat);
  }

  function addLines(id, points) {
    // Plain line pairs (cell grids, fins, leader stubs): drawn, not picked.
    if (!keep.has(id) || points.length === 0) return;
    const p = part(id);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(points.flat(), 3));
    const mat = new THREE.LineBasicMaterial({ color: ink.clone(), transparent: true, opacity: opt.baseOpacity });
    const lines = new THREE.LineSegments(geometry, mat);
    lines.userData.partId = id;
    p.group.add(lines);
    p.lines.push(lines);
    disposables.push(geometry, mat);
  }

  const box = (w, h, d) => new THREE.BoxGeometry(w, h, d);
  const cyl = (r, h, seg = 12) => new THREE.CylinderGeometry(r, r, h, seg, 1);
  const half = Math.PI / 2;

  // Bus: 1.0 x 1.3 x 1.0 (illustrative units).
  addShape('bus', box(1.0, 1.3, 1.0), [0, 0, 0]);
  anchors.set('bus', [0.5, 0.55, 0.5]);

  // Solar wings along +-X (the spacecraft's +Y / -Y wings): yoke + panel + cell grid.
  for (const side of [1, -1]) {
    const cx = side * 1.42;
    addShape('wings', box(1.5, 0.04, 0.85), [cx, 0.1, 0]);
    addShape('wings', box(0.16, 0.03, 0.06), [side * 0.58, 0.1, 0]);
    const cells = [];
    const x0 = cx - 0.75, x1 = cx + 0.75, z0 = -0.425, z1 = 0.425, y = 0.125;
    const nx = simple ? 3 : 6, nz = simple ? 1 : 3;
    for (let i = 1; i < nx; i += 1) { const x = x0 + (x1 - x0) * i / nx; cells.push([x, y, z0], [x, y, z1]); }
    for (let j = 1; j <= nz; j += 1) { const z = z0 + (z1 - z0) * j / (nz + 1); cells.push([x0, y, z], [x1, y, z]); }
    addLines('wings', cells);
    anchors.set(side > 0 ? 'wing_py' : 'wing_my', [cx, 0.1, 0]);
  }
  anchors.set('wings', [1.42, 0.1, 0.42]);

  // Battery pack (4s3p) low in the bus, with its cell divisions.
  addShape('battery', box(0.5, 0.3, 0.4), [0, -0.42, 0]);
  const cellLines = [];
  for (let i = 1; i < 4; i += 1) { const x = -0.25 + 0.125 * i; cellLines.push([x, -0.27, -0.2], [x, -0.27, 0.2]); }
  for (let j = 1; j < 3; j += 1) { const z = -0.2 + (0.4 / 3) * j; cellLines.push([-0.25, -0.27, z], [0.25, -0.27, z]); }
  addLines('battery', cellLines);
  anchors.set('battery', [0, -0.42, 0.2]);

  addShape('pcdu', box(0.45, 0.14, 0.34), [0, -0.08, 0.12]);
  anchors.set('pcdu', [0, -0.08, 0.29]);
  addShape('obc', box(0.34, 0.1, 0.28), [0, 0.16, 0.18]);
  anchors.set('obc', [0, 0.16, 0.32]);

  // ADCS: three orthogonal reaction wheels and two magnetorquer rods.
  addShape('adcs', cyl(0.09, 0.05), [-0.28, 0.4, -0.22], [0, 0, half]);
  addShape('adcs', cyl(0.09, 0.05), [-0.06, 0.4, -0.3]);
  addShape('adcs', cyl(0.09, 0.05), [0.16, 0.4, -0.22], [half, 0, 0]);
  if (!simple) {
    addShape('adcs', cyl(0.015, 0.7, 6), [-0.44, 0, 0.44]);
    addShape('adcs', cyl(0.015, 0.7, 6), [0.44, 0, -0.44], [0, 0, half]);
  }
  anchors.set('adcs', [-0.06, 0.4, -0.3]);

  addShape('star_tracker', box(0.13, 0.13, 0.13), [0.33, 0.715, -0.3]);
  addShape('star_tracker', new THREE.ConeGeometry(0.09, 0.16, 8, 1, true), [0.33, 0.86, -0.3], [Math.PI, 0, 0]);
  anchors.set('star_tracker', [0.33, 0.86, -0.3]);

  addShape('gnss', cyl(0.09, 0.025, 14), [-0.3, 0.665, 0.3]);
  anchors.set('gnss', [-0.3, 0.68, 0.3]);

  addShape('sband', new THREE.ConeGeometry(0.13, 0.08, 12, 1, true), [0.3, -0.69, 0.3]);
  addShape('sband', cyl(0.02, 0.06, 6), [0.3, -0.63, 0.3]);
  anchors.set('sband', [0.3, -0.72, 0.3]);

  addShape('payload', cyl(0.16, 0.34, 16), [0, -0.82, -0.15]);
  addShape('payload', cyl(0.11, 0.02, 16), [0, -1.0, -0.15]);
  anchors.set('payload', [0, -1.0, -0.15]);

  // Heater mat under the battery and the radiator on the -Z face, with fins.
  addShape('heater', box(0.5, 0.015, 0.4), [0, -0.58, 0]);
  addShape('heater', box(0.85, 1.1, 0.02), [0, 0, -0.52]);
  const fins = [];
  for (let i = 1; i < 6; i += 1) { const y = -0.55 + (1.1 / 6) * i; fins.push([-0.42, y, -0.535], [0.42, y, -0.535]); }
  addLines('heater', fins);
  anchors.set('heater', [-0.42, -0.2, -0.53]);

  addShape('aux', box(0.18, 0.12, 0.12), [0.3, 0.35, 0.57]);
  anchors.set('aux', [0.39, 0.35, 0.63]);

  addShape('propulsion', new THREE.SphereGeometry(0.15, 10, 6), [0.24, -0.08, -0.26]);
  addShape('propulsion', new THREE.ConeGeometry(0.07, 0.14, 10, 1, true), [0.3, -0.45, -0.62], [-half, 0, 0]);
  anchors.set('propulsion', [0.3, -0.45, -0.69]);

  function paint() {
    for (const p of parts.values()) {
      const active = highlighted === p.id;
      const dimmed = highlighted !== null && !active;
      const opacity = !p.on ? opt.offOpacity : active ? 1 : dimmed ? opt.dimOpacity : opt.baseOpacity;
      for (const l of p.lines) {
        l.material.opacity = opacity;
        l.material.color.copy(active ? accent : ink);
      }
      for (const f of p.fills) f.material.opacity = active ? 0.16 : 0.0;
    }
  }

  return {
    group,
    parts,
    pickables,
    partIds: [...parts.keys()],
    anchor(id, target = new THREE.Vector3()) {
      const a = anchors.get(id);
      if (!a) return null;
      return group.localToWorld(target.set(a[0], a[1], a[2]));
    },
    hasAnchor: (id) => anchors.has(id),
    setHighlight(id) {
      highlighted = id && parts.has(id) ? id : null;
      paint();
      return highlighted;
    },
    get highlighted() { return highlighted; },
    setPartOn(id, on) {
      const p = parts.get(id);
      if (!p) return false;
      p.on = Boolean(on);
      paint();
      return true;
    },
    isPartOn: (id) => parts.get(id)?.on ?? false,
    dispose() {
      for (const d of disposables) d.dispose();
      group.clear();
      parts.clear();
      pickables.length = 0;
    },
  };
}
