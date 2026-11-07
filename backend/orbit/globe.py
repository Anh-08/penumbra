"""Geodetic fleet positions, orbit tracks, and Earth-fixed Sun direction."""

from datetime import datetime, timezone
import math

import numpy as np
from skyfield.api import wgs84
from skyfield.framelib import itrs

from .eclipse import DAY_S, visible_solar_fraction


def _time(model, t: float):
    if not math.isfinite(float(t)):
        raise ValueError("t must be a finite POSIX timestamp")
    return model.assets.timescale.from_datetime(datetime.fromtimestamp(float(t), timezone.utc))


def positions(model, t: float) -> dict[str, dict[str, float]]:
    """Return each satellite's geodetic position and solar-disc visibility."""
    sky_time = _time(model, t)
    grid_index = round((t - model.t[0]) / model.step_s)
    on_grid = (
        0 <= grid_index < len(model.t)
        and abs(float(t) - float(model.t[grid_index])) < 1e-6
    )
    sun_km = None
    if not on_grid:
        sun_km = (
            model.assets.ephemeris["earth"]
            .at(sky_time)
            .observe(model.assets.ephemeris["sun"])
            .position.km
        )
    fleet = {}
    for sat_id, satellite in model.assets.satellites.items():
        geocentric = satellite.at(sky_time)
        point = wgs84.subpoint(geocentric)
        if on_grid:
            illumination = float(model.illumination(sat_id)[grid_index])
        else:
            illumination = float(
                visible_solar_fraction(
                    geocentric.position.km.reshape(3, 1), sun_km.reshape(3, 1)
                )[0]
            )
        fleet[sat_id] = {
            "lat_deg": float(point.latitude.degrees),
            "lon_deg": float(point.longitude.degrees),
            "alt_km": float(point.elevation.km),
            "illum": illumination,
        }
    return fleet


def orbit_path(model, sat_id: str, t: float, minutes: float | None = None) -> list[list[float]]:
    """Sample one orbit ahead by default, or a requested number of minutes."""
    satellite = model.assets.satellites[sat_id]
    if minutes is None:
        minutes = 2 * math.pi / float(satellite.model.no_kozai)
    if not math.isfinite(minutes) or minutes <= 0:
        raise ValueError("minutes must be positive and finite")
    count = math.ceil(minutes) + 1
    offsets = np.linspace(0.0, minutes * 60.0, count)
    start = _time(model, t)
    times = model.assets.timescale.tt_jd(start.tt + offsets / DAY_S)
    points = wgs84.subpoint(satellite.at(times))
    return [
        [float(lat), float(lon), float(alt)]
        for lat, lon, alt in zip(
            points.latitude.degrees, points.longitude.degrees, points.elevation.km
        )
    ]


def sun_direction(model, t: float) -> list[float]:
    """Unit Earth-to-Sun vector in Earth-centered Earth-fixed coordinates."""
    sky_time = _time(model, t)
    vector = (
        model.assets.ephemeris["earth"]
        .at(sky_time)
        .observe(model.assets.ephemeris["sun"])
        .apparent()
        .frame_xyz(itrs)
        .km
    )
    unit = vector / np.linalg.norm(vector)
    return [float(component) for component in unit]
