import assert from 'node:assert/strict';
import test from 'node:test';
import {
  FOCUS_SCALE, GLYPH_HEIGHT_PX, GLYPH_WIDTH_PX, RECORDED_INCIDENT_SATS, SAT_GLYPH_SVG, SAT_GLYPH_URI, glyphScale,
  glyphStyle, markerStyle, tintKey,
} from './satGlyph.js';

test('the glyph is one inline SVG data URI with no external reference', () => {
  assert.match(SAT_GLYPH_URI, /^data:image\/svg\+xml;charset=utf-8,/);
  const svg = decodeURIComponent(SAT_GLYPH_URI.split(',')[1]);
  assert.equal(svg, SAT_GLYPH_SVG);
  assert.match(svg, /^<svg [^>]*width="44" height="24"/);
  assert.doesNotMatch(svg, /href|url\(|<image|<text/);
  assert.equal(glyphStyle({ sat_id: 'SAT-01', illum: 1 }).image, glyphStyle({ sat_id: 'SAT-24', illum: 0 }).image);
});

test('the glyph is sized in screen pixels, about twenty wide', () => {
  assert.ok(GLYPH_WIDTH_PX >= 18 && GLYPH_WIDTH_PX <= 22);
  assert.equal(GLYPH_WIDTH_PX / GLYPH_HEIGHT_PX, 44 / 24);
});

test('tint keeps the dot colors: red in eclipse, amber in penumbra, teal sunlit, faded in shadow', () => {
  assert.equal(tintKey(0), 'red');
  assert.equal(tintKey(0.049), 'red');
  assert.equal(tintKey(0.05), 'amber');
  assert.equal(tintKey(0.5), 'amber');
  assert.equal(tintKey(0.95), 'teal');
  assert.equal(tintKey(1), 'teal');
  const eclipse = glyphStyle({ sat_id: 'SAT-03', illum: 0 });
  const sunlit = glyphStyle({ sat_id: 'SAT-03', illum: 1 });
  assert.deepEqual([eclipse.tint, eclipse.alpha], ['red', 0.4]);
  assert.deepEqual([sunlit.tint, sunlit.alpha], ['teal', 1]);
  assert.ok(glyphStyle({ sat_id: 'SAT-03', illum: 0.5 }).alpha < 1);
});

test('only the focused satellite is scaled up', () => {
  assert.equal(FOCUS_SCALE, 1.3);
  assert.equal(glyphScale('SAT-07', 'SAT-07'), 1.3);
  assert.equal(glyphScale('SAT-19', 'SAT-07'), 1);
  assert.equal(glyphScale('SAT-07', undefined), 1);
  assert.equal(glyphStyle({ sat_id: 'SAT-07', illum: 1 }, 'SAT-07').scale, 1.3);
});

test('T47: an incident satellite is alert amber at full alpha, scaled and labelled, whatever its light', () => {
  const opts = { incident: ['SAT-12', 'SAT-07'], focused: 'SAT-19' };
  for (const illum of [0, 0.5, 1]) {
    assert.deepEqual(markerStyle({ sat_id: 'SAT-12', illum }, opts), { tint: 'incident', alpha: 1, scale: 1.3, label: true });
  }
  assert.deepEqual(markerStyle({ sat_id: 'SAT-19', illum: 0 }, opts), { tint: 'red', alpha: 0.4, scale: 1.3, label: true });
  assert.deepEqual(markerStyle({ sat_id: 'SAT-03', illum: 1 }, opts), { tint: 'teal', alpha: 1, scale: 1, label: false });
});

test('T47: clearing the incident restores the illumination tint; the recorded run is SAT-12, SAT-07, SAT-19', () => {
  const sat = { sat_id: 'SAT-07', illum: 0.5 };
  assert.equal(markerStyle(sat, { incident: ['SAT-07'] }).tint, 'incident');
  const cleared = markerStyle(sat, { incident: [] });
  assert.deepEqual([cleared.tint, cleared.scale, cleared.label], ['amber', 1, false]);
  assert.ok(cleared.alpha < 1);
  assert.deepEqual(markerStyle(sat), cleared);
  assert.deepEqual(RECORDED_INCIDENT_SATS, ['SAT-12', 'SAT-07', 'SAT-19']);
});
