import assert from 'node:assert/strict';
import test from 'node:test';
import table from './constellations.json' with { type: 'json' };

test('all twelve constellations have finite celestial coordinates and connected lines', () => {
  assert.equal(table.constellations.length, 12);
  assert.deepEqual(table.constellations.map((row) => row.name), [
    'Aquila', 'Canis Major', 'Cassiopeia', 'Crux', 'Cygnus', 'Gemini',
    'Leo', 'Lyra', 'Orion', 'Scorpius', 'Taurus', 'Ursa Major',
  ]);
  for (const constellation of table.constellations) {
    assert.ok(constellation.lines.length > 0);
    for (const [hip, coordinates] of Object.entries(constellation.stars)) {
      assert.match(hip, /^\d+$/);
      assert.equal(coordinates.length, 2);
      const [ra, dec] = coordinates;
      assert.ok(Number.isFinite(ra) && ra >= 0 && ra < 360);
      assert.ok(Number.isFinite(dec) && dec >= -90 && dec <= 90);
    }
    for (const line of constellation.lines) {
      assert.ok(line.length >= 2);
      for (const hip of line) assert.ok(Object.hasOwn(constellation.stars, hip));
    }
  }
});
