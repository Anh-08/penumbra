# Recorded demo runs

| File | What | Model |
|---|---|---|
| `live-single-stuck-load.jsonl` | T22 gate run of scenario `single-stuck-load` (seed 0): alert, agent, Flight Director approval, execution | Gemini `gemini-3.5-flash-lite`, live, recorded 2026-11-07 |

Replay offline (no key, no network): `PENUMBRA_REPLAY=runs/demo/live-single-stuck-load.jsonl PENUMBRA_REPLAY_PACE_MS=0 python -m backend.fleet.demo --run-id replay-check --runs-dir /tmp`.
The test `backend/eps/tests/test_e2e_single.py` does this and checks the truth SOC through the next eclipse.

## Real vs simulated
- **Real:** satellite orbits (cached public OMM elements, renamed SAT-01..SAT-24), Sun/Earth geometry (JPL DE421), ground station sites and 10° contact windows; battery OCV curve, internal resistance, coulombic efficiency and fade rate inside the simulator (fitted from NASA PCoE, Saha & Goebel 2007).
- **Simulated:** all EPS telemetry, the fault (SAT-17's AUX relay stuck on from T0 + 2 h), sensor noise and bias.
- The Flight Director approval in these files is the scripted approver of `backend/fleet/demo.py`, not a person.
