"""Eclipse geometry and offline disk-cache acceptance tests."""

from datetime import datetime, timezone
import math

import numpy as np

from backend.orbit.eclipse import OrbitModel, visible_solar_fraction
from backend.orbit.loader import load_fleet


def test_circular_500km_beta_zero_has_expected_shadow_and_penumbra():
    earth_radius_km = 6371.0
    orbit_radius_km = earth_radius_km + 500.0
    period_s = 2 * math.pi * math.sqrt(orbit_radius_km**3 / 398600.4418)
    times = np.arange(0.0, period_s, 10.0)
    angle = 2 * math.pi * times / period_s
    satellite = np.stack(
        (orbit_radius_km * np.cos(angle), orbit_radius_km * np.sin(angle), np.zeros_like(angle))
    )
    sun = np.broadcast_to(np.array([[149_597_870.7], [0.0], [0.0]]), satellite.shape)

    fraction = visible_solar_fraction(satellite, sun, earth_radius_km=earth_radius_km)
    shadow_minutes = np.count_nonzero(fraction == 0.0) * 10.0 / 60.0
    assert 34.8 <= shadow_minutes <= 36.8
    assert np.all((0.0 <= fraction) & (fraction <= 1.0))
    assert np.count_nonzero((fraction > 0.0) & (fraction < 1.0)) > 0


def test_illumination_uses_ten_second_grid_and_disk_cache(tmp_path, monkeypatch):
    assets = load_fleet()
    t0 = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)
    model = OrbitModel(assets, t0=t0, horizon_s=600, cache_dir=tmp_path)
    fraction = model.illumination("SAT-01")

    assert fraction.shape == (60,)
    assert np.all((0.0 <= fraction) & (fraction <= 1.0))
    assert len(list(tmp_path.glob("SAT-01-*.npy"))) == 1

    second_model = OrbitModel(assets, t0=t0, horizon_s=600, cache_dir=tmp_path)
    monkeypatch.setattr(second_model, "_calculate_illumination", lambda *_: 1 / 0)
    np.testing.assert_array_equal(second_model.illumination("SAT-01"), fraction)
