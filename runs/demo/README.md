# Recorded demo runs

| File | What | Model |
|---|---|---|
| `live-single-stuck-load.jsonl` | T22 gate: scenario `single-stuck-load` (seed 0). SAT-09's AUX line stuck on. Gemini tried AUX off (with duty 0.5) and AUX off: both failed verification (the relay is stuck); SAFE_MODE passed, the Flight Director approved it (Tier 2), executed | Gemini `gemini-3.5-flash-lite`, live, recorded 2026-11-07 on the T26 agent loop |
| `live-fault-storm.jsonl` | T27: scenario `fault-storm` (seed 0). SAT-12 Tier 1 auto-executed (flight rule), SAT-07 Tier 2 approved and executed, SAT-19 infeasible with SAFE_MODE recommended | Gemini `gemini-3.5-flash-lite`, live, recorded 2026-11-07 on the T26 agent loop |
| `live-conjunction.jsonl` | T31: scenario `conjunction` (seed 0). Simulated debris DEB-01 passes SAT-17 0.3 km away about 5.2 h after T0; SAT-17 starts at SOC 0.74 and a burn costs 1800 s of slew + burn power. Gemini proposed one burn per window (W1, W2, W3); on this draw the verifier passed only W2 (97/100 samples, worst margin +2.1 % SOC), while W1 (88/100, -4.5 % SOC) and W3 (94/100, -0.6 % SOC) failed. Gemini chose W2 (P-2); Tier 2 approved and executed about 2.3 h before closest approach; the conjunction alert cleared; lowest downlinked SOC afterwards 0.50. Conjunction times are whole seconds and the scenario's contacts are quantized, so the plan hash replays on any machine | Gemini `gemini-3.5-flash-lite`, live, recorded 2026-11-08 on B1/T31-cam-power-retune (A1 takeover) |

Replay offline (no key, no network), for example:
`PENUMBRA_REPLAY=runs/demo/live-fault-storm.jsonl PENUMBRA_REPLAY_PACE_MS=0 python -m backend.fleet.demo --scenario fault-storm --run-id replay-check --runs-dir /tmp`.
Tests `backend/eps/tests/test_e2e_single.py`, `backend/fleet/tests/test_scenarios.py` and `backend/fleet/tests/test_conjunction_replay.py` replay them and check the outcomes.

## Real vs simulated
- **Real:** satellite orbits (cached public OMM elements, renamed SAT-01..SAT-24), Sun/Earth geometry (JPL DE421), ground station sites and 10° contact windows; battery OCV curve, internal resistance, coulombic efficiency and fade rate inside the simulator (fitted from NASA PCoE, Saha & Goebel 2007).
- **Simulated:** all EPS telemetry, the faults (see `backend/fleet/scenarios.py`), sensor noise and bias, the payload tasking; the debris object DEB-01 and its conjunction (`data/debris/`), burn power and slew time.
- The Flight Director approval in these files is the scripted approver of `backend/fleet/demo.py`, not a person.
