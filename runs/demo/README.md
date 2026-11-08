# Recorded demo runs

| File | What | Model |
|---|---|---|
| `live-single-stuck-load.jsonl` | T22 gate: scenario `single-stuck-load` (seed 0). SAT-09's AUX line stuck on. Gemini tried AUX off (with duty 0.5) and AUX off: both failed verification (the relay is stuck); SAFE_MODE passed, the Flight Director approved it (Tier 2), executed | Gemini `gemini-3.5-flash-lite`, live, recorded 2026-11-07 on the T26 agent loop |
| `live-fault-storm.jsonl` | T27: scenario `fault-storm` (seed 0). SAT-12 Tier 1 auto-executed (flight rule), SAT-07 Tier 2 approved and executed, SAT-19 infeasible with SAFE_MODE recommended | Gemini `gemini-3.5-flash-lite`, live, recorded 2026-11-07 on the T26 agent loop |

Replay offline (no key, no network), for example:
`PENUMBRA_REPLAY=runs/demo/live-fault-storm.jsonl PENUMBRA_REPLAY_PACE_MS=0 python -m backend.fleet.demo --scenario fault-storm --run-id replay-check --runs-dir /tmp`.
Tests `backend/eps/tests/test_e2e_single.py` and `backend/fleet/tests/test_scenarios.py` replay both and check the outcomes.

## Real vs simulated
- **Real:** satellite orbits (cached public OMM elements, renamed SAT-01..SAT-24), Sun/Earth geometry (JPL DE421), ground station sites and 10° contact windows; battery OCV curve, internal resistance, coulombic efficiency and fade rate inside the simulator (fitted from NASA PCoE, Saha & Goebel 2007).
- **Simulated:** all EPS telemetry, the faults (see `backend/fleet/scenarios.py`), sensor noise and bias, the payload tasking.
- The Flight Director approval in these files is the scripted approver of `backend/fleet/demo.py`, not a person.
