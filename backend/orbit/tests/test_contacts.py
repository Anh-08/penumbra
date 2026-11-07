"""Skyfield 10-degree contact and beta-angle acceptance tests."""

from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from backend.orbit import OrbitModel, load_fleet
from backend.orbit.contacts import beta_from_vectors


def test_beta_angle_matches_simple_orbit_planes():
    position = np.array([1.0, 0.0, 0.0])
    velocity = np.array([0.0, 1.0, 0.0])
    assert beta_from_vectors(position, velocity, np.array([1.0, 0.0, 0.0])) == 0.0
    assert beta_from_vectors(position, velocity, np.array([0.0, 0.0, 1.0])) == 90.0


def test_contacts_are_sorted_complete_and_nonoverlapping(tmp_path):
    assets = load_fleet()
    t0 = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)
    model = OrbitModel(assets, t0=t0, cache_dir=tmp_path)
    contacts = model.contacts()

    assert contacts
    assert contacts == sorted(contacts, key=lambda c: (c.aos, c.sat_id, c.station_id))
    per_pair = defaultdict(list)
    useful_passes = 0
    for contact in contacts:
        assert contact.aos < contact.los
        duration = contact.los - contact.aos
        assert duration <= 900
        # Grazing passes just above 10 degrees can be shorter than two minutes.
        # Passes peaking above 15 degrees have the expected 2-15 minute duration.
        if contact.max_el_deg >= 15:
            useful_passes += 1
            assert 120 <= duration <= 900
        assert contact.max_el_deg >= 10.0
        assert contact.station_id in {"SVALBARD", "TROLL", "FAIRBANKS"}
        per_pair[contact.sat_id, contact.station_id].append(contact)
    for windows in per_pair.values():
        assert all(left.los <= right.aos for left, right in zip(windows, windows[1:]))
    assert useful_passes > 0

    first = next(c for c in contacts if c.sat_id == "SAT-01")
    assert model.next_contact("SAT-01", first.aos - 1) == first
    assert model.next_contact("SAT-01", (first.aos + first.los) / 2) == first
    assert 0.0 <= model.beta_deg("SAT-01") <= 90.0
