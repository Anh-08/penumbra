"""Offline Skyfield assets for the anonymized Penumbra fleet."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from skyfield.api import EarthSatellite, load, load_file, wgs84


LOGGER = logging.getLogger(__name__)
DATA = Path(__file__).resolve().parents[2] / "data"
SATELLITE_COUNT = 24
STATION_IDS = frozenset({"SVALBARD", "TROLL", "FAIRBANKS"})


@dataclass(frozen=True)
class GroundStation:
    station_id: str
    lat_deg: float
    lon_deg: float
    alt_m: float
    position: object


@dataclass(frozen=True)
class FleetAssets:
    satellites: dict[str, EarthSatellite]
    stations: dict[str, GroundStation]
    ephemeris: object
    timescale: object
    epoch_age_days: dict[str, float]


def load_fleet(
    *,
    omm_path: Path | None = None,
    stations_path: Path | None = None,
    ephemeris_path: Path | None = None,
    now: datetime | None = None,
) -> FleetAssets:
    """Load local assets and assign stable SAT-01..SAT-24 aliases.

    Selection sorts by numeric catalog id, so the source file's row order has
    no effect. Only aliased OMM rows are passed to Skyfield or retained.
    """
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    omm_file = Path(omm_path) if omm_path is not None else DATA / "omm/fleet.json"
    station_file = Path(stations_path) if stations_path is not None else DATA / "stations.json"
    ephemeris_file = (
        Path(ephemeris_path) if ephemeris_path is not None else DATA / "ephem/de421.bsp"
    )

    rows = json.loads(omm_file.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) < SATELLITE_COUNT:
        raise ValueError("OMM cache must contain at least 24 satellites")
    catalog_ids = [int(row["NORAD_CAT_ID"]) for row in rows]
    if len(set(catalog_ids)) != len(catalog_ids):
        raise ValueError("OMM cache contains duplicate catalog ids")

    timescale = load.timescale(builtin=True)
    satellites = {}
    epoch_age_days = {}
    for number, row in enumerate(sorted(rows, key=lambda item: int(item["NORAD_CAT_ID"]))[:24], 1):
        sat_id = f"SAT-{number:02d}"
        sanitized_row = {**row, "OBJECT_NAME": sat_id}
        satellite = EarthSatellite.from_omm(timescale, sanitized_row)
        satellites[sat_id] = satellite
        epoch_age_days[sat_id] = (
            current_time - satellite.epoch.utc_datetime()
        ).total_seconds() / 86400.0

    station_rows = json.loads(station_file.read_text(encoding="utf-8"))
    station_ids = {row["station_id"] for row in station_rows}
    if station_ids != STATION_IDS or len(station_rows) != len(STATION_IDS):
        raise ValueError("station cache must contain Svalbard, Troll, and Fairbanks")
    stations = {}
    for row in station_rows:
        station_id = row["station_id"]
        latitude = float(row["lat_deg"])
        longitude = float(row["lon_deg"])
        altitude = float(row["alt_m"])
        stations[station_id] = GroundStation(
            station_id=station_id,
            lat_deg=latitude,
            lon_deg=longitude,
            alt_m=altitude,
            position=wgs84.latlon(latitude, longitude, elevation_m=altitude),
        )

    ephemeris = load_file(str(ephemeris_file))
    LOGGER.info(
        "OMM epoch age (days) for %d satellites: min=%.2f max=%.2f",
        len(satellites),
        min(epoch_age_days.values()),
        max(epoch_age_days.values()),
    )
    return FleetAssets(satellites, stations, ephemeris, timescale, epoch_age_days)
