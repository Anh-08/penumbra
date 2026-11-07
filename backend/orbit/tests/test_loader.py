"""The fleet loader must work entirely from the committed local assets."""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from backend.orbit.loader import load_fleet


DATA = Path(__file__).resolve().parents[3] / "data"
NOW = datetime(2026, 11, 7, tzinfo=timezone.utc)


def test_loads_anonymized_fleet_stations_and_local_ephemeris(monkeypatch, caplog):
    monkeypatch.setenv("PENUMBRA_OFFLINE", "1")
    with caplog.at_level(logging.INFO, logger="backend.orbit.loader"):
        fleet = load_fleet(now=NOW)

    expected_ids = [f"SAT-{number:02d}" for number in range(1, 25)]
    assert list(fleet.satellites) == expected_ids
    assert [sat.name for sat in fleet.satellites.values()] == expected_ids
    assert set(fleet.stations) == {"SVALBARD", "TROLL", "FAIRBANKS"}
    assert all(station.position is not None for station in fleet.stations.values())
    assert fleet.ephemeris["earth"] is not None
    assert fleet.ephemeris["sun"] is not None
    assert set(fleet.epoch_age_days) == set(expected_ids)
    assert "OMM epoch age" in caplog.text

    real_names = {row["OBJECT_NAME"] for row in json.loads((DATA / "omm/fleet.json").read_text())}
    assert all(name not in caplog.text for name in real_names)


def test_selection_does_not_depend_on_omm_row_order(tmp_path):
    original = load_fleet(now=NOW)
    rows = json.loads((DATA / "omm/fleet.json").read_text())
    reversed_omm = tmp_path / "reversed.json"
    reversed_omm.write_text(json.dumps(list(reversed(rows))))

    reordered = load_fleet(omm_path=reversed_omm, now=NOW)
    assert [sat.model.satnum for sat in reordered.satellites.values()] == [
        sat.model.satnum for sat in original.satellites.values()
    ]
    assert [sat.name for sat in reordered.satellites.values()] == list(original.satellites)
