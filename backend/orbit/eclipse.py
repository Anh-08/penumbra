"""Solar-disc visibility from Earth occultation, with offline disk caching."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

import numpy as np

from .loader import FleetAssets


EARTH_RADIUS_KM = 6371.0
SUN_RADIUS_KM = 695_700.0
DAY_S = 86_400
GRID_STEP_S = 10


def visible_solar_fraction(
    satellite_km: np.ndarray,
    sun_km: np.ndarray,
    *,
    earth_radius_km: float = EARTH_RADIUS_KM,
    sun_radius_km: float = SUN_RADIUS_KM,
) -> np.ndarray:
    """Fraction of the apparent solar disc not overlapped by Earth's disc.

    Both vector arrays have shape (3, N) in the same geocentric frame. The
    circle-overlap formula uses apparent angular radii and separation at the
    spacecraft. It does not use Skyfield's binary ``is_sunlit`` result.
    """
    satellite = np.asarray(satellite_km, dtype=float)
    sun = np.asarray(sun_km, dtype=float)
    if satellite.shape != sun.shape or satellite.ndim != 2 or satellite.shape[0] != 3:
        raise ValueError("satellite_km and sun_km must both have shape (3, N)")
    earth_distance = np.linalg.norm(satellite, axis=0)
    sun_vector = sun - satellite
    sun_distance = np.linalg.norm(sun_vector, axis=0)
    if np.any(earth_distance <= earth_radius_km) or np.any(sun_distance <= sun_radius_km):
        raise ValueError("spacecraft must be outside Earth and Sun")

    earth_angle = np.arcsin(earth_radius_km / earth_distance)
    sun_angle = np.arcsin(sun_radius_km / sun_distance)
    cosine = np.sum(-satellite * sun_vector, axis=0) / (earth_distance * sun_distance)
    separation = np.arccos(np.clip(cosine, -1.0, 1.0))

    fraction = np.ones_like(separation)
    sun_covered = (earth_angle >= sun_angle) & (separation <= earth_angle - sun_angle)
    fraction[sun_covered] = 0.0
    earth_inside_sun = (sun_angle > earth_angle) & (separation <= sun_angle - earth_angle)
    fraction[earth_inside_sun] = 1.0 - (earth_angle[earth_inside_sun] / sun_angle[earth_inside_sun]) ** 2

    partial = (
        (separation < earth_angle + sun_angle)
        & (separation > np.abs(earth_angle - sun_angle))
    )
    if np.any(partial):
        distance = separation[partial]
        earth = earth_angle[partial]
        solar = sun_angle[partial]
        earth_segment = earth**2 * np.arccos(
            np.clip((distance**2 + earth**2 - solar**2) / (2 * distance * earth), -1, 1)
        )
        sun_segment = solar**2 * np.arccos(
            np.clip((distance**2 + solar**2 - earth**2) / (2 * distance * solar), -1, 1)
        )
        chord = 0.5 * np.sqrt(
            np.maximum(
                0.0,
                (-distance + earth + solar)
                * (distance + earth - solar)
                * (distance - earth + solar)
                * (distance + earth + solar),
            )
        )
        overlap = earth_segment + sun_segment - chord
        fraction[partial] = 1.0 - overlap / (np.pi * solar**2)
    return np.clip(fraction, 0.0, 1.0)


class OrbitModel:
    """A 10-second UTC/POSIX grid with cached illumination for each satellite."""

    def __init__(
        self,
        assets: FleetAssets,
        *,
        t0: datetime | float,
        horizon_s: int = DAY_S,
        step_s: int = GRID_STEP_S,
        cache_dir: str | Path | None = None,
    ) -> None:
        if isinstance(t0, datetime):
            if t0.tzinfo is None or t0.utcoffset() is None:
                raise ValueError("t0 must be timezone-aware")
            start_t = t0.timestamp()
        else:
            start_t = float(t0)
        if not np.isfinite(start_t) or horizon_s <= 0 or step_s <= 0 or horizon_s % step_s:
            raise ValueError("t0 and grid dimensions must be finite and positive")
        self.assets = assets
        self.t = start_t + np.arange(horizon_s // step_s, dtype=float) * step_s
        self.step_s = step_s
        default_cache = Path(tempfile.gettempdir()) / "penumbra-orbit-cache"
        self.cache_dir = Path(cache_dir or os.environ.get("PENUMBRA_ORBIT_CACHE", default_cache))
        self._skyfield_times = None
        self._sun_km = None
        self._contacts = None

    def _times(self):
        if self._skyfield_times is None:
            start = self.assets.timescale.from_datetime(
                datetime.fromtimestamp(float(self.t[0]), timezone.utc)
            )
            self._skyfield_times = self.assets.timescale.tt_jd(
                start.tt + (self.t - self.t[0]) / DAY_S
            )
        return self._skyfield_times

    def _sun_positions(self) -> np.ndarray:
        if self._sun_km is None:
            earth = self.assets.ephemeris["earth"]
            sun = self.assets.ephemeris["sun"]
            self._sun_km = earth.at(self._times()).observe(sun).position.km
        return self._sun_km

    def _cache_path(self, sat_id: str) -> Path:
        sat = self.assets.satellites[sat_id]
        model = sat.model
        key = {
            "t0": float(self.t[0]),
            "count": len(self.t),
            "step_s": self.step_s,
            "epoch_tt": float(sat.epoch.tt),
            "elements": [
                float(getattr(model, name))
                for name in ("ecco", "inclo", "nodeo", "argpo", "mo", "no_kozai", "bstar")
            ],
        }
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
        return self.cache_dir / f"{sat_id}-{digest}.npy"

    def _calculate_illumination(self, sat_id: str) -> np.ndarray:
        satellite_km = self.assets.satellites[sat_id].at(self._times()).position.km
        return visible_solar_fraction(satellite_km, self._sun_positions())

    def illumination(self, sat_id: str) -> np.ndarray:
        """Return the solar visibility fraction for one SAT id on ``self.t``."""
        path = self._cache_path(sat_id)
        if path.exists():
            try:
                cached = np.load(path, allow_pickle=False)
                if cached.shape == self.t.shape:
                    return cached
            except (OSError, ValueError):
                pass
        result = self._calculate_illumination(sat_id)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.cache_dir, suffix=".npy", delete=False) as stream:
            temporary = Path(stream.name)
            np.save(stream, result)
        os.replace(temporary, path)
        return result

    def beta_deg(self, sat_id: str) -> float:
        """Return the absolute Sun-to-orbit-plane angle at the grid start."""
        from .contacts import beta_from_vectors

        state = self.assets.satellites[sat_id].at(self._times()[0])
        return beta_from_vectors(
            state.position.km, state.velocity.km_per_s, self._sun_positions()[:, 0]
        )

    def contacts(self):
        """Return globally sorted complete 10-degree contact windows."""
        from .contacts import find_contacts

        if self._contacts is None:
            self._contacts = find_contacts(self)
        return list(self._contacts)

    def next_contact(self, sat_id: str, t: float):
        """Return the first contact whose LOS is after t, including one underway."""
        if sat_id not in self.assets.satellites:
            raise KeyError(sat_id)
        return next((c for c in self.contacts() if c.sat_id == sat_id and c.los > t), None)
