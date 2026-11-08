# Debris (SIMULATED) for T31

`debris.json` holds **simulated** objects, not real catalog entries: a list of
`{object_id: "DEB-nn", epoch, r_km, v_km_s, simulated: true, note}`, an inertial (GCRS) state at `epoch`
(POSIX seconds), propagated two-body by `backend/orbit/conjunctions.py`.

- `DEB-01`: planted by `backend.orbit.conjunctions.plant_debris(model, "SAT-09", T0 + 5 h, 0.3 km, 60 deg, "DEB-01")`
  with T0 = 2026-11-07 12:00 UTC, so that it passes SAT-09 (a real orbit from the cached OMM) 5 h after the scenario
  epoch with a 0.3 km miss distance.

The fleet's orbits, the Sun geometry and the ground contacts are real; this file is not. Results and the UI must
say "simulated debris".
