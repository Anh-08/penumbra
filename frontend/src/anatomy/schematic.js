// 2D schematic of the illustrative bus (T37, for T38's 2D / paper tiles): an ultra-minimal front cross-section.
// Pure data plus two tiny renderers (SVG markup, Canvas 2D), so it works in a tile, in a test and on paper.
// Coordinates are in a 120 x 80 box; y grows downwards.

// Which part shows the fault cue for each hypothesis (H0 = nominal: no cue).
export const FAULT_PART = { H0: null, H1: 'wings', H2: 'aux', H3: 'battery', H4: 'pcdu', H5: 'wings' };

const SHAPES = [
  ['bus', 'rect', 40, 14, 40, 52],
  ['wings', 'rect', 4, 34, 32, 6], ['wings', 'line', 36, 37, 40, 37],
  ['wings', 'rect', 84, 34, 32, 6], ['wings', 'line', 80, 37, 84, 37],
  ['battery', 'rect', 47, 50, 26, 10],
  ['pcdu', 'rect', 47, 38, 26, 7],
  ['obc', 'rect', 50, 27, 20, 6],
  ['adcs', 'circle', 47, 20, 3],
  ['sband', 'line', 66, 66, 66, 72], ['sband', 'line', 62, 72, 70, 72],
  ['payload', 'rect', 50, 66, 10, 8],
  ['heater', 'line', 47, 62, 73, 62],
  ['aux', 'rect', 76, 18, 6, 6],
];
const ANCHORS = { bus: [60, 40], wings: [20, 37], battery: [60, 55], pcdu: [60, 41.5], obc: [60, 30], adcs: [47, 20],
  sband: [66, 72], payload: [55, 70], heater: [60, 62], aux: [79, 21] };

export function createSchematic() {
  const shapes = SHAPES.map(([part, kind, ...v]) => (kind === 'rect' ? { part, kind, x: v[0], y: v[1], w: v[2], h: v[3] }
      : kind === 'circle' ? { part, kind, cx: v[0], cy: v[1], r: v[2] }
        : { part, kind, x1: v[0], y1: v[1], x2: v[2], y2: v[3] }));
  return { width: 120, height: 80, shapes, anchors: { ...ANCHORS }, parts: [...new Set(shapes.map((s) => s.part))] };
}

// Where to put the red fault dot for a hypothesis, or null (H0, unknown).
export function faultDot(schematic, hypothesis) {
  const part = FAULT_PART[hypothesis];
  return part ? { part, x: schematic.anchors[part][0], y: schematic.anchors[part][1] } : null;
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// SVG markup for one schematic; `fault` is a hypothesis id (H1..H5) or a part id.
export function schematicSvg(schematic, { fault = null, ink = '#e9eff7', alert = '#ff5a4f', stroke = 1 } = {}) {
  const dot = FAULT_PART[fault] !== undefined ? faultDot(schematic, fault)
    : fault && schematic.anchors[fault] ? { part: fault, x: schematic.anchors[fault][0], y: schematic.anchors[fault][1] } : null;
  const body = schematic.shapes.map((s) => {
    const c = dot && s.part === dot.part ? alert : ink;
    if (s.kind === 'rect') return `<rect x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" stroke="${esc(c)}"/>`;
    if (s.kind === 'circle') return `<circle cx="${s.cx}" cy="${s.cy}" r="${s.r}" stroke="${esc(c)}"/>`;
    return `<line x1="${s.x1}" y1="${s.y1}" x2="${s.x2}" y2="${s.y2}" stroke="${esc(c)}"/>`;
  }).join('');
  const mark = dot ? `<circle cx="${dot.x}" cy="${dot.y}" r="2.6" fill="${esc(alert)}" stroke="none" data-fault="${esc(dot.part)}"/>` : '';
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${schematic.width} ${schematic.height}" fill="none" stroke-width="${stroke}">${body}${mark}</svg>`;
}

// Paint into a Canvas 2D context at (x, y) with a uniform scale; same colour rules as schematicSvg.
export function drawSchematic(ctx, schematic, { x = 0, y = 0, scale = 1, fault = null, ink = '#e9eff7', alert = '#ff5a4f' } = {}) {
  const dot = faultDot(schematic, fault);
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  ctx.lineWidth = 1 / scale;
  for (const s of schematic.shapes) {
    ctx.strokeStyle = dot && s.part === dot.part ? alert : ink;
    ctx.beginPath();
    if (s.kind === 'rect') ctx.rect(s.x, s.y, s.w, s.h);
    else if (s.kind === 'circle') ctx.arc(s.cx, s.cy, s.r, 0, Math.PI * 2);
    else { ctx.moveTo(s.x1, s.y1); ctx.lineTo(s.x2, s.y2); }
    ctx.stroke();
  }
  if (dot) {
    ctx.fillStyle = alert;
    ctx.beginPath();
    ctx.arc(dot.x, dot.y, 2.6, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
  return dot;
}
