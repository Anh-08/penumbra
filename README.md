# Penumbra

Verified AI mission ops for a 24-satellite LEO fleet. **AI proposes, physics verifies, humans approve.**
Built at UB Hacking 2026.

## Layout
| Path | What |
|---|---|
| `CONTRACTS.md`, `contracts/` | Frozen interface spec and its pydantic v2 models |
| `backend/orbit/` | Orbit, eclipse with penumbra, contact windows (Skyfield, offline data) |
| `backend/eps/` | Truth EPS simulator and seeded faults |
| `backend/verify/` | Planning model, Monte Carlo verifier, ranking |
| `backend/fleet/` | Detection, triage, tier policy, demo scenarios |
| `backend/agent/` | Agent loop and safety layer |
| `backend/recorder/`, `backend/sched/` | Flight recorder/replay, uplink scheduler |
| `backend/api/`, `frontend/` | FastAPI + WebSocket server, Cesium UI |
| `bench/`, `tests/` | Benchmarks, end-to-end tests |
| `data/` | Cached OMM, ground stations, ephemeris (fetched once, no network at runtime) |

## Real vs simulated data
- **Real:** satellite orbits (public OMM elements, renamed `SAT-01`..`SAT-24`), ground station locations, Sun/Earth ephemeris (DE421).
- **Simulated:** all power-system telemetry, faults and battery behavior.
The UI always shows a Real / Simulated legend, and every results file says which inputs are which.

## Run the tests
```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
PENUMBRA_OFFLINE=1 python -m pytest -q
```
Secrets come only from env: `GEMINI_API_KEY`, `GEMINI_MODEL`, `ELEVENLABS_API_KEY`.
