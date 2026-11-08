import {
  ArcType,
  Cartesian2,
  Cartesian3,
  Color,
  DirectionalLight,
  HeadingPitchRoll,
  HeightReference,
  Ion,
  JulianDate,
  Matrix4,
  Matrix3,
  Material,
  Quaternion,
  PolylineCollection,
  ScreenSpaceEventType,
  TileMapServiceImageryProvider,
  Transforms,
  Viewer,
  VerticalOrigin,
  buildModuleUrl,
} from 'cesium';
import { orbitTrack, STATIONS } from './mockFleet.js';
import { createPinState, reducePin } from './pinState.js';
import { glyphStyle, markerStyle } from './satGlyph.js';
import constellations from './data/constellations.json' with { type: 'json' };
import buffaloPinUrl from './assets/buffalo-pin.svg';

const EARTH_RADIUS_M = 6_371_000;
const SHADOW_LENGTH_M = 15_000_000;
const teal = Color.fromCssColorString('#65e9df');
const amber = Color.fromCssColorString('#f6ad58');
const red = Color.fromCssColorString('#f26f75');
const incidentAmber = Color.fromCssColorString('#ffb224');  // --c-alert (T47 incident satellites)
const TINTS = { teal, amber, red, incident: incidentAmber };
const INITIAL_FOCUS = 'SAT-07';
const BUFFALO = { lat: 42.886, lon: -78.878 };
const CELESTIAL_RADIUS_M = 1_000_000_000;

function addConstellations(viewer) {
  // J2000 positions: HYG v3.8; line sets: Stellarium western sky culture.
  // Full source URLs and transformations are recorded in data/constellations.json.
  const lines = viewer.scene.primitives.add(new PolylineCollection());
  const white = Color.WHITE.withAlpha(0.24);
  for (const constellation of constellations.constellations) {
    for (const line of constellation.lines) {
      lines.add({
        positions: line.map((hip) => {
          const [raDeg, decDeg] = constellation.stars[hip];
          const ra = raDeg * Math.PI / 180;
          const dec = decDeg * Math.PI / 180;
          return new Cartesian3(
            CELESTIAL_RADIUS_M * Math.cos(dec) * Math.cos(ra),
            CELESTIAL_RADIUS_M * Math.cos(dec) * Math.sin(ra),
            CELESTIAL_RADIUS_M * Math.sin(dec),
          );
        }),
        width: 1,
        material: Material.fromType('Color', { color: white }),
      });
    }
  }
  return lines;
}

function addBuffaloPin(viewer) {
  return viewer.entities.add({
    id: 'buffalo-pin',
    name: 'BUFFALO',
    position: Cartesian3.fromDegrees(BUFFALO.lon, BUFFALO.lat),
    billboard: {
      image: buffaloPinUrl,
      width: 22,
      height: 34,
      verticalOrigin: VerticalOrigin.BOTTOM,
      heightReference: HeightReference.CLAMP_TO_GROUND,
      disableDepthTestDistance: Number.POSITIVE_INFINITY,
    },
    label: {
      text: 'BUFFALO',
      font: '600 12px "JetBrains Mono", monospace',
      fillColor: Color.WHITE,
      outlineColor: Color.BLACK,
      outlineWidth: 2,
      pixelOffset: new Cartesian2(0, -45),
      disableDepthTestDistance: Number.POSITIVE_INFINITY,
    },
  });
}

function colorFor(sat, style = markerStyle(sat)) {
  return TINTS[style.tint].withAlpha(style.alpha);
}

function orientationFor(direction) {
  const up = Cartesian3.UNIT_Z;
  const axis = Cartesian3.cross(up, direction, new Cartesian3());
  const dot = Cartesian3.dot(up, direction);
  if (Cartesian3.magnitudeSquared(axis) < 1e-10) {
    return Quaternion.fromAxisAngle(Cartesian3.UNIT_X, dot < 0 ? Math.PI : 0);
  }
  return Quaternion.fromAxisAngle(Cartesian3.normalize(axis, axis), Math.acos(Math.max(-1, Math.min(1, dot))));
}

function addStations(viewer) {
  for (const station of STATIONS) {
    const ground = Cartesian3.fromDegrees(station.lon_deg, station.lat_deg, 0);
    const normal = Cartesian3.normalize(ground, new Cartesian3());
    const coneCenter = Cartesian3.add(ground, Cartesian3.multiplyByScalar(normal, 340_000, new Cartesian3()), new Cartesian3());
    const frame = Transforms.eastNorthUpToFixedFrame(ground);
    const orientation = Quaternion.fromRotationMatrix(Matrix4.getMatrix3(frame, new Matrix3()));
    viewer.entities.add({
      name: `${station.id} station cone`,
      position: coneCenter,
      orientation,
      cylinder: {
        length: 680_000,
        topRadius: 230_000,
        bottomRadius: 0,
        material: teal.withAlpha(0.045),
        outline: true,
        outlineColor: teal.withAlpha(0.28),
        numberOfVerticalLines: 6,
      },
    });
    viewer.entities.add({
      name: station.id,
      position: ground,
      point: { pixelSize: 5, color: amber, outlineColor: Color.BLACK, outlineWidth: 1 },
      label: {
        text: station.id,
        font: '10px monospace',
        fillColor: amber,
        outlineColor: Color.BLACK,
        outlineWidth: 2,
        pixelOffset: new Cartesian2(0, -18),
        scale: 1,
      },
    });
  }
}

function addShadow(viewer) {
  const makeVolume = (name, radius, opacity, outlineAlpha) => viewer.entities.add({
    name,
    position: Cartesian3.ZERO,
    cylinder: {
      length: SHADOW_LENGTH_M,
      topRadius: radius * 0.83,
      bottomRadius: radius,
      material: Color.fromCssColorString('#050914').withAlpha(opacity),
      outline: true,
      outlineColor: Color.fromCssColorString('#b6a8ed').withAlpha(outlineAlpha),
      numberOfVerticalLines: 12,
    },
  });
  return {
    umbra: makeVolume('Earth umbra', EARTH_RADIUS_M, 0.27, 0.4),
    penumbra: makeVolume('Penumbra boundary', EARTH_RADIUS_M + 240_000, 0.1, 0.3),
  };
}

export function createScene(container, initialState, onSelect) {
  Ion.defaultAccessToken = '';
  const viewer = new Viewer(container, {
    baseLayer: false,
    geocoder: false,
    baseLayerPicker: false,
    homeButton: false,
    sceneModePicker: false,
    navigationHelpButton: false,
    animation: false,
    timeline: false,
    infoBox: false,
    selectionIndicator: false,
    fullscreenButton: false,
    requestRenderMode: false,
    shouldAnimate: true,
  });
  viewer.scene.backgroundColor = Color.fromCssColorString('#030914');
  viewer.scene.globe.baseColor = Color.fromCssColorString('#081523');
  viewer.scene.globe.enableLighting = true;
  viewer.scene.globe.showGroundAtmosphere = true;
  viewer.scene.fog.enabled = false;
  viewer.scene.skyAtmosphere.show = true;
  viewer.scene.sun.show = true;
  viewer.scene.moon.show = true;
  viewer.scene.skyBox.show = true;
  viewer.scene.highDynamicRange = false;
  viewer.scene.postProcessStages.bloom.enabled = false;
  viewer.clock.shouldAnimate = false;
  const cameraPosition = Cartesian3.fromDegrees(-22, 18, 12_000_000);
  viewer.camera.setView({
    destination: cameraPosition,
    orientation: {
      direction: Cartesian3.negate(Cartesian3.normalize(cameraPosition, new Cartesian3()), new Cartesian3()),
      up: Cartesian3.UNIT_Z,
    },
  });

  // Cesium ships this tile set with its own static assets. It never requests ion imagery.
  TileMapServiceImageryProvider.fromUrl(buildModuleUrl('Assets/Textures/NaturalEarthII'))
    .then((imagery) => { if (!viewer.isDestroyed()) viewer.imageryLayers.addImageryProvider(imagery); })
    .catch((error) => console.warn('Offline imagery unavailable; Cesium base globe remains visible.', error));

  addStations(viewer);
  const shadow = addShadow(viewer);
  const skyLines = addConstellations(viewer);
  skyLines.show = true;
  const buffaloPin = addBuffaloPin(viewer);
  const markers = new Map();
  const tracks = new Map();
  for (const sat of initialState.sats) {
    const glyph = glyphStyle(sat, INITIAL_FOCUS);
    const marker = viewer.entities.add({
      name: sat.sat_id,
      position: Cartesian3.fromDegrees(sat.lon_deg, sat.lat_deg, sat.alt_km * 1000),
      billboard: {
        image: glyph.image,  // one shared SVG data URI; the per-frame update never changes it
        width: glyph.width,
        height: glyph.height,
        scale: glyph.scale,
        color: colorFor(sat),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
      label: {
        text: sat.sat_id,
        show: sat.sat_id === INITIAL_FOCUS,
        font: '10px monospace',
        fillColor: Color.WHITE.withAlpha(0.7),
        outlineColor: Color.BLACK,
        outlineWidth: 2,
        pixelOffset: new Cartesian2(0, -16),
        scale: 0.9,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
    });
    markers.set(sat.sat_id, marker);
    const track = viewer.entities.add({
      name: `${sat.sat_id} predicted orbit`,
      polyline: {
        positions: orbitTrack(sat.sat_id, initialState.t).map(([lat, lon, alt]) =>
          Cartesian3.fromDegrees(lon, lat, alt * 1000)),
        width: 1,
        material: teal.withAlpha(0.21),
        arcType: ArcType.NONE,
      },
    });
    tracks.set(sat.sat_id, track);
  }

  viewer.selectedEntityChanged.addEventListener((entity) => {
    if (entity && markers.has(entity.name)) onSelect(entity.name);
  });

  let pinState = createPinState();
  let returningToBuffalo = false;
  const flyToBuffalo = (duration, complete = () => {}) => viewer.camera.flyTo({
    destination: Cartesian3.fromDegrees(BUFFALO.lon, BUFFALO.lat, 8_000_000),
    orientation: new HeadingPitchRoll(0, -Math.PI / 2, 0),
    duration,
    complete,
    cancel: complete,
  });
  const togglePin = () => {
    const wasParked = pinState.parked;
    pinState = reducePin(pinState, 'toggle', performance.now());
    if (wasParked) {
      returningToBuffalo = true;
      flyToBuffalo(0.8, () => { returningToBuffalo = false; });
    } else flyToBuffalo(1.5);
  };
  const previousClick = viewer.screenSpaceEventHandler.getInputAction(ScreenSpaceEventType.LEFT_CLICK);
  viewer.screenSpaceEventHandler.setInputAction((click) => {
    if (viewer.scene.pick(click.position)?.id === buffaloPin) togglePin();
    else previousClick?.(click);
  }, ScreenSpaceEventType.LEFT_CLICK);
  const onKeyDown = (event) => {
    if (event.key.toLowerCase() === 'b' && !event.repeat && !event.altKey && !event.ctrlKey && !event.metaKey
      && !['INPUT', 'TEXTAREA'].includes(event.target?.tagName)) togglePin();
    else if (pinState.parked) pinState = reducePin(pinState, 'activity', performance.now());
  };
  const onPointerActivity = (event) => {
    if (pinState.parked && event.buttons) pinState = reducePin(pinState, 'activity', performance.now());
  };
  const onInputActivity = () => {
    if (pinState.parked) pinState = reducePin(pinState, 'activity', performance.now());
  };
  window.addEventListener('keydown', onKeyDown);
  viewer.scene.canvas.addEventListener('pointermove', onPointerActivity);
  viewer.scene.canvas.addEventListener('pointerdown', onInputActivity);
  viewer.scene.canvas.addEventListener('wheel', onInputActivity);
  const idleTimer = window.setInterval(() => {
    if (pinState.parked && performance.now() >= pinState.idleAt) togglePin();
  }, 250);

  let previousFrame = performance.now();
  let previousTrackTime = initialState.t;
  let motionActive = true;
  const moveCamera = () => {
    const now = performance.now();
    const elapsed = Math.min((now - previousFrame) / 1000, 0.1);
    previousFrame = now;
    if (motionActive && !pinState.parked && !returningToBuffalo) viewer.camera.rotate(Cartesian3.UNIT_Z, elapsed * 0.018);
  };
  viewer.scene.preRender.addEventListener(moveCamera);

  // Marker look: illumination tint, plus the focused satellite and the incident's satellites (T47) scaled up and
  // labelled; alerted satellites keep their label. The per-frame update only moves and repaints.
  let lastSats = initialState.sats;
  let focusedId = INITIAL_FOCUS;
  let alertedIds = new Set();
  let incidentIds = [];
  const styleOf = (sat) => markerStyle(sat, { incident: incidentIds, focused: focusedId });
  const paint = (marker, sat, style = styleOf(sat)) => {
    marker.billboard.color = colorFor(sat, style);
    marker.label.fillColor = TINTS[style.tint].withAlpha(style.tint === 'incident' ? 1 : Math.max(0.4, sat.illum));
  };
  const restyle = () => {
    for (const sat of lastSats) {
      const marker = markers.get(sat.sat_id);
      if (!marker) continue;
      const style = styleOf(sat);
      marker.billboard.scale = style.scale;
      marker.label.show = style.label || alertedIds.has(sat.sat_id);
      paint(marker, sat, style);
    }
  };

  const update = (state) => {
    viewer.clock.currentTime = JulianDate.fromDate(new Date(state.t * 1000));
    const inertialToFixed = Transforms.computeIcrfToFixedMatrix(viewer.clock.currentTime)
      ?? Transforms.computeTemeToPseudoFixedMatrix(viewer.clock.currentTime);
    skyLines.modelMatrix = Matrix4.fromRotationTranslation(inertialToFixed, Cartesian3.ZERO, new Matrix4());
    const sun = Cartesian3.fromElements(...state.sun_direction, new Cartesian3());
    viewer.scene.light = new DirectionalLight({
      direction: Cartesian3.negate(sun, new Cartesian3()),
      color: Color.WHITE,
      intensity: 2.1,
    });
    const night = Cartesian3.negate(sun, new Cartesian3());
    const center = Cartesian3.multiplyByScalar(night, EARTH_RADIUS_M + SHADOW_LENGTH_M / 2, new Cartesian3());
    const orientation = orientationFor(night);
    for (const volume of Object.values(shadow)) {
      volume.position = center;
      volume.orientation = orientation;
    }
    lastSats = state.sats;
    for (const sat of state.sats) {
      const marker = markers.get(sat.sat_id);
      if (!marker) continue;
      marker.position = Cartesian3.fromDegrees(sat.lon_deg, sat.lat_deg, sat.alt_km * 1000);
      paint(marker, sat);
    }
    if (state.t - previousTrackTime > 120) {
      previousTrackTime = state.t;
      for (const sat of state.sats) {
        tracks.get(sat.sat_id).polyline.positions = orbitTrack(sat.sat_id, state.t)
          .map(([lat, lon, alt]) => Cartesian3.fromDegrees(lon, lat, alt * 1000));
      }
    }
  };
  update(initialState);
  return {
    viewer,
    update,
    setHighlights: (focused, alerted) => {
      focusedId = focused;
      alertedIds = new Set(alerted);
      restyle();
    },
    setIncidentSats: (ids) => {
      incidentIds = Array.isArray(ids) ? [...ids] : [];
      restyle();
    },
    setMotionActive: (active) => { motionActive = active; },
    setSkyVisible: (visible) => { skyLines.show = visible; },
    destroy: () => {
      window.clearInterval(idleTimer);
      window.removeEventListener('keydown', onKeyDown);
      viewer.scene.canvas.removeEventListener('pointermove', onPointerActivity);
      viewer.scene.canvas.removeEventListener('pointerdown', onInputActivity);
      viewer.scene.canvas.removeEventListener('wheel', onInputActivity);
      viewer.scene.preRender.removeEventListener(moveCamera);
      viewer.destroy();
    },
  };
}
