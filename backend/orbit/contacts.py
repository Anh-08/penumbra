"""Ten-degree Skyfield contact windows and orbital beta angle."""

from datetime import datetime, timezone

import numpy as np

from contracts import Contact


MIN_ELEVATION_DEG = 10.0


def beta_from_vectors(position_km: np.ndarray, velocity_km_s: np.ndarray, sun_km: np.ndarray) -> float:
    """Absolute angle between sunlight and the spacecraft's orbital plane."""
    normal = np.cross(position_km, velocity_km_s)
    normal_length = float(np.linalg.norm(normal))
    sun_length = float(np.linalg.norm(sun_km))
    if normal_length == 0 or sun_length == 0:
        raise ValueError("orbit normal and Sun direction must be nonzero")
    sine_beta = float(np.dot(normal, sun_km) / (normal_length * sun_length))
    return float(np.degrees(np.arcsin(np.clip(abs(sine_beta), 0.0, 1.0))))


def find_contacts(model) -> list[Contact]:
    """Pair Skyfield rise and set events and retain their peak elevation."""
    start = model.assets.timescale.from_datetime(
        datetime.fromtimestamp(float(model.t[0]), timezone.utc)
    )
    end = model.assets.timescale.from_datetime(
        datetime.fromtimestamp(float(model.t[-1] + model.step_s), timezone.utc)
    )
    windows = []
    for sat_id, satellite in model.assets.satellites.items():
        for station_id, station in model.assets.stations.items():
            event_times, event_kinds = satellite.find_events(
                station.position, start, end, altitude_degrees=MIN_ELEVATION_DEG
            )
            aos = None
            peak = MIN_ELEVATION_DEG
            for event_time, kind in zip(event_times, event_kinds):
                timestamp = event_time.utc_datetime().timestamp()
                if kind == 0:
                    aos = timestamp
                    peak = MIN_ELEVATION_DEG
                elif kind == 1 and aos is not None:
                    elevation, _, _ = (satellite - station.position).at(event_time).altaz()
                    peak = max(peak, float(elevation.degrees))
                elif kind == 2 and aos is not None:
                    if timestamp > aos:
                        windows.append(
                            Contact(
                                sat_id=sat_id,
                                station_id=station_id,
                                aos=aos,
                                los=timestamp,
                                max_el_deg=peak,
                            )
                        )
                    aos = None
    return sorted(windows, key=lambda contact: (contact.aos, contact.sat_id, contact.station_id))
