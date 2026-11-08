// One shared satellite glyph for the 24 fleet markers on the globe (T40).
// Pure (no Cesium import) so node --test can load it; scene.js turns the tint key into a Cesium Color.
import { markerAlpha } from './mockFleet.js';

// Billboard size in screen pixels (not meters), so the glyph does not change with zoom.
export const GLYPH_WIDTH_PX = 22;
export const GLYPH_HEIGHT_PX = 12;
export const FOCUS_SCALE = 1.3;

// Body + two solar panels, flat white so the billboard color tints it; the dark outline stays dark
// under any tint and keeps the glyph readable on the lit globe (the old dots had the same outline).
// Drawn at twice the display size so it stays crisp at devicePixelRatio 2.
export const SAT_GLYPH_SVG = [
  '<svg xmlns="http://www.w3.org/2000/svg" width="44" height="24" viewBox="0 0 44 24">',
  '<g fill="#fff" stroke="#091522" stroke-width="1.5" stroke-linejoin="round">',
  '<rect x="13" y="10.5" width="18" height="3"/>',
  '<rect x="1" y="5" width="13" height="14" rx="1"/>',
  '<rect x="30" y="5" width="13" height="14" rx="1"/>',
  '<rect x="17" y="2" width="10" height="20" rx="2"/>',
  '</g>',
  '<path d="M7.5 5v14M36.5 5v14M1 12h13M30 12h13" stroke="#091522" stroke-width="1"/>',
  '</svg>',
].join('');

export const SAT_GLYPH_URI = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(SAT_GLYPH_SVG)}`;

// Same thresholds as the dots had: red in eclipse, amber in penumbra, teal sunlit.
export function tintKey(illum) {
  if (illum < 0.05) return 'red';
  if (illum < 0.95) return 'amber';
  return 'teal';
}

export function glyphScale(satId, focusedId) {
  return satId === focusedId ? FOCUS_SCALE : 1;
}

// Everything the billboard needs for one satellite. The image is the same string for all 24.
export function glyphStyle(sat, focusedId) {
  return {
    image: SAT_GLYPH_URI,
    width: GLYPH_WIDTH_PX,
    height: GLYPH_HEIGHT_PX,
    scale: glyphScale(sat.sat_id, focusedId),
    tint: tintKey(sat.illum),
    alpha: markerAlpha(sat.illum),
  };
}
