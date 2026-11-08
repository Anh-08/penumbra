# Debris (SIMULATED) for T31

`debris.json` holds **simulated** objects, not real catalog entries: a list of
`{object_id: "DEB-nn", epoch, r_km, v_km_s, simulated: true, note}`, an inertial (GCRS) state at `epoch`
(POSIX seconds), propagated two-body by `backend/orbit/conjunctions.py`.

- `DEB-01`: planted by `backend.orbit.conjunctions.plant_debris(model, "SAT-17", T0 + 5.2 h, 0.3 km, 60 deg, "DEB-01")`
  with T0 = 2026-11-07 12:00 UTC, so that it passes SAT-17 (a real orbit from the cached OMM) 5.2 h after the scenario
  epoch with a 0.3 km miss distance. `window_leads_s` [3.13 h, 2.13 h, 0.83 h] puts W1 inside SAT-17's second
  eclipse and W2, W3 in sunlight: the T31 `conjunction` scenario's power trade.

The fleet's orbits, the Sun geometry and the ground contacts are real; this file is not. Results and the UI must
say "simulated debris".
