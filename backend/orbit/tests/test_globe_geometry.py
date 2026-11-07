"""The Orbit contract's ECEF and globe data methods."""

from datetime import datetime, timezone

import numpy as np

from backend.orbit import OrbitModel, load_fleet


T0 = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)


def test_positions_have_geodetic_ranges_and_grid_illumination(tmp_path):
    model = OrbitModel(load_fleet(), t0=T0, cache_dir=tmp_path)
    positions = model.positions(T0.timestamp())
    assert set(positions) == set(model.assets.satellites)
    for sat_id, position in positions.items():
        assert -90 <= position["lat_deg"] <= 90
        assert -180 <= position["lon_deg"] <= 180
        # The cached group spans roughly 400-600 km; a few states fall just outside.
        assert 375 <= position["alt_km"] <= 625
        assert position["illum"] == model.illumination(sat_id)[0]


def test_orbit_path_defaults_to_one_orbit_and_accepts_minutes(tmp_path):
    model = OrbitModel(load_fleet(), t0=T0, cache_dir=tmp_path)
    path = model.orbit_path("SAT-01", T0.timestamp())
    assert 80 <= len(path) <= 120
    assert all(len(point) == 3 for point in path)
    assert all(-90 <= lat <= 90 and -180 <= lon <= 180 and 400 <= alt <= 600 for lat, lon, alt in path)
    short_path = model.orbit_path("SAT-01", T0.timestamp(), minutes=5)
    assert len(short_path) == 6
    np.testing.assert_allclose(path[0], short_path[0], atol=1e-6)


def test_sun_direction_is_unit_vector_in_ecef(tmp_path):
    model = OrbitModel(load_fleet(), t0=T0, cache_dir=tmp_path)
    direction = model.sun_direction(T0.timestamp())
    assert len(direction) == 3
    assert np.isclose(np.linalg.norm(direction), 1.0, atol=1e-12)
    later = model.sun_direction(T0.timestamp() + 6 * 3600)
    assert np.linalg.norm(np.array(direction) - np.array(later)) > 0.5
