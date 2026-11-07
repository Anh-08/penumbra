// Deterministic local preview. These positions are simulated until the API is connected.
const EARTH_RADIUS_KM = 6371;
const ORBIT_SECONDS = 95 * 60;
const EARTH_DAY_SECONDS = 86164;
const DEG = 180 / Math.PI;
const RAD = Math.PI / 180;
const SAT_IDS = Array.from({ length: 24 }, (_, index) => `SAT-${String(index + 1).padStart(2, '0')}`);

export const STATIONS = Object.freeze([
  { id: 'SVALBARD', lat_deg: 78.2298, lon_deg: 15.4078 },
  { id: 'TROLL', lat_deg: -72.0117, lon_deg: 2.5350 },
  { id: 'FAIRBANKS', lat_deg: 64.8378, lon_deg: -147.7164 },
]);

const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));
const wrapLon = (degrees) => ((degrees + 540) % 360) - 180;

export function sunDirection(t) {
  // A moving, unit-length ECEF direction for the offline preview.
  const dayAngle = 2 * Math.PI * (t % EARTH_DAY_SECONDS) / EARTH_DAY_SECONDS;
  const season = 2 * Math.PI * (t / (365.2422 * 86400));
  const declination = 23.44 * RAD * Math.sin(season);
  return [
    Math.cos(declination) * Math.cos(dayAngle),
    Math.cos(declination) * Math.sin(dayAngle),
    Math.sin(declination),
  ];
}

function orbitVector(index, t) {
  const plane = Math.floor(index / 4);
  const slot = index % 4;
  const inclination = (97.2 + plane * 0.18) * RAD;
  const raan = plane * Math.PI / 3;
  const phase = 2 * Math.PI * (t / (ORBIT_SECONDS + plane * 11) + slot / 4 + plane / 24);
  const x0 = Math.cos(phase);
  const y0 = Math.sin(phase) * Math.cos(inclination);
  const z0 = Math.sin(phase) * Math.sin(inclination);
  const x1 = x0 * Math.cos(raan) - y0 * Math.sin(raan);
  const y1 = x0 * Math.sin(raan) + y0 * Math.cos(raan);
  const spin = 2 * Math.PI * t / EARTH_DAY_SECONDS;
  return [x1 * Math.cos(spin) + y1 * Math.sin(spin),
    -x1 * Math.sin(spin) + y1 * Math.cos(spin), z0];
}

function position(index, t, sun) {
  const [x, y, z] = orbitVector(index, t);
  const alt_km = 500 + Math.floor(index / 4) * 4;
  const radius = EARTH_RADIUS_KM + alt_km;
  const towardSun = x * sun[0] + y * sun[1] + z * sun[2];
  const shadowAxis = radius * Math.sqrt(Math.max(0, 1 - towardSun * towardSun));
  // A short transition zone makes partial illumination visible at the shadow rim.
  const illum = towardSun >= 0 ? 1 : clamp((shadowAxis - EARTH_RADIUS_KM + 75) / 150, 0, 1);
  return {
    lat_deg: Math.asin(z) * DEG,
    lon_deg: wrapLon(Math.atan2(y, x) * DEG),
    alt_km,
    illum,
  };
}

export function orbitTrack(satId, t, minutes = 95, sampleSeconds = 120) {
  const index = SAT_IDS.indexOf(satId);
  if (index < 0) throw new Error(`Unknown satellite ${satId}`);
  const duration = minutes * 60;
  const points = [];
  for (let elapsed = 0; elapsed <= duration; elapsed += sampleSeconds) {
    const p = position(index, t + elapsed, sunDirection(t + elapsed));
    points.push([p.lat_deg, p.lon_deg, p.alt_km]);
  }
  return points;
}

export function buildFleetState(t, speed = 120) {
  const sun_direction = sunDirection(t);
  return {
    t,
    speed,
    sun_direction,
    sats: SAT_IDS.map((sat_id, index) => {
      const p = position(index, t, sun_direction);
      return {
        sat_id,
        ...p,
        soc: clamp(0.74 + 0.08 * Math.sin(index * 1.7 + t / 4600), 0, 1),
        status: p.illum < 0.05 ? 'ECLIPSE' : p.illum < 0.95 ? 'PENUMBRA' : 'NOMINAL',
      };
    }),
  };
}
