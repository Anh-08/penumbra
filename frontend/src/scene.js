import {
  ArcType,
  Cartesian2,
  Cartesian3,
  Color,
  DirectionalLight,
  Ion,
  JulianDate,
  Matrix4,
  Matrix3,
  Quaternion,
  TileMapServiceImageryProvider,
  Transforms,
  Viewer,
  buildModuleUrl,
} from 'cesium';
import { markerAlpha, orbitTrack, STATIONS } from './mockFleet.js';

const EARTH_RADIUS_M = 6_371_000;
const SHADOW_LENGTH_M = 15_000_000;
const teal = Color.fromCssColorString('#65e9df');
const amber = Color.fromCssColorString('#f6ad58');
const red = Color.fromCssColorString('#f26f75');

function colorFor(sat) {
  if (sat.illum < 0.05) return red.withAlpha(markerAlpha(sat.illum));
  if (sat.illum < 0.95) return amber.withAlpha(markerAlpha(sat.illum));
  return teal.withAlpha(markerAlpha(sat.illum));
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
  viewer.scene.sun.show = false;
  viewer.scene.moon.show = false;
  viewer.scene.skyBox.show = false;
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
  const markers = new Map();
  const tracks = new Map();
  for (const sat of initialState.sats) {
    const marker = viewer.entities.add({
      name: sat.sat_id,
      position: Cartesian3.fromDegrees(sat.lon_deg, sat.lat_deg, sat.alt_km * 1000),
      point: {
        pixelSize: 7,
        color: colorFor(sat),
        outlineColor: Color.fromCssColorString('#091522'),
        outlineWidth: 2,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
      label: {
        text: sat.sat_id,
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

  let previousFrame = performance.now();
  let previousTrackTime = initialState.t;
  const moveCamera = () => {
    const now = performance.now();
    const elapsed = Math.min((now - previousFrame) / 1000, 0.1);
    previousFrame = now;
    viewer.camera.rotate(Cartesian3.UNIT_Z, elapsed * 0.018);
  };
  viewer.scene.preRender.addEventListener(moveCamera);

  const update = (state) => {
    viewer.clock.currentTime = JulianDate.fromDate(new Date(state.t * 1000));
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
    for (const sat of state.sats) {
      const marker = markers.get(sat.sat_id);
      if (!marker) continue;
      marker.position = Cartesian3.fromDegrees(sat.lon_deg, sat.lat_deg, sat.alt_km * 1000);
      marker.point.color = colorFor(sat);
      marker.label.fillColor = colorFor(sat).withAlpha(Math.max(0.4, sat.illum));
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
  return { viewer, update, destroy: () => { viewer.scene.preRender.removeEventListener(moveCamera); viewer.destroy(); } };
}
