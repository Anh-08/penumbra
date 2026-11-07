# Penumbra backlog (UB Hacking 2026)

Seed with `python3 scripts/seed_issues.py --dry-run`. Review together, then run with `--approve`.
Agents pick the lowest-numbered approved, unblocked issue for their own code. "Due" is a target time in US Eastern.

Gates from the plan:
- Sat 18:00: orbit
- Sat 22:00: truth sim + verifier
- Sun 02:00: single-sat loop end-to-end
- Sun 06:00: feature freeze
- Sun 09:00: numbers locked

## Human tasks (not seeded)
- H0 (A+B): approve CONTRACTS.md and this backlog; set up environments, keys and data (see docs/PRE-EVENT.md). B: sign up for Vultr via mlh.link/vultr-signup, claim the credits, add an SSH key. Do not create an instance yet.
- H0-DATA (A, right after cloning): download once into `data/pcoe/` a NASA PCoE Li-ion battery aging dataset (from the NASA Prognostics repository or its Kaggle mirror) and, if time allows, the BIRDS dataset into `data/birds/`. Commit on a `human/` branch with a `data/README.md` entry (source, license, fetch time). Data only, no code.
- H-VULTR, Sun 03:30–04:00 (B): create one Vultr compute instance, run the T32 deploy scripts, start the T33 benchmark, then sleep. Destroy the instance after judging.
- H1, Sat 13:00–14:00 (A+B):
  - Create a public repo and push the pre-event commit (docs, tooling, data only).
  - `data/omm/fleet.json`, `data/ephem/de421.bsp` and `data/stations.json` were fetched before the event (data, no code).
  - Turn on branch protection.
  - Each human sets their own `GEMINI_API_KEY`, `GEMINI_MODEL` and `ELEVENLABS_API_KEY` locally.
  - Run the 30-minute drill.
- H2, Sat 23:00 (B): Devpost draft with these mandatory sections: (1) Real vs simulated data; (2) Built during the hackathon vs existed before (from docs/PRE-EVENT.md); (3) Sense → Understand → Act mapping for the Radial track; (4) one short paragraph per MLH track (Gemini, ElevenLabs, Vultr).
- H3, Sun 02:00 (A+B): gate review of the single-sat loop. Apply the plan's 2 AM rule if it fails.
- H4, Sun 06:00–09:00 (A): record a 90 s fallback video; take screenshots of the matrix and the B1 numbers.
- H5, Sun 09:00–11:30 (A+B): rehearse the pitch 5 times; finalize Devpost.
- H6, Sun 12:00: submit.

### T01 [B1] Repo bootstrap: contracts package, CI, path-guard, dependencies
- Paths: contracts/, .github/, scripts/, backend/__init__.py, backend/conftest.py, backend/requirements.txt, pyproject.toml, docs/, .gitignore, README.md
- Blocked by: none
- Due: Sat 14:00
- Accept:
  - `contracts/` pydantic v2 models match the approved CONTRACTS.md exactly (names, fields, units).
  - `backend/requirements.txt` pins skyfield, sgp4, numpy, ortools, fastapi, uvicorn, websockets, pydantic, google-genai, httpx, pytest (resolve the versions at event start).
  - CI is green on a trivial PR. A test PR from branch `A2/test-guard` that touches `backend/eps/x.py` fails path-guard; close it afterwards.
  - `.gitignore` excludes `runs/*` except `runs/demo/`, plus `.env`, `node_modules` and caches.

### T02 [B2] Fleet loader from cached OMM + ground stations
- Paths: backend/orbit/, data/
- Blocked by: T01
- Due: Sat 15:30
- Accept:
  - Selects 24 satellites deterministically from `data/omm/fleet.json` (a full CelesTrak group) and loads them with Skyfield `EarthSatellite.from_omm`, renamed to SAT-01..SAT-24. Real names never leave `backend/orbit`.
  - Uses `data/stations.json` (3 stations, including Svalbard) and the offline ephemeris `data/ephem/de421.bsp`.
  - Logs the OMM epoch age. No network anywhere; tests pass with PENUMBRA_OFFLINE=1.

### T03 [B2] Eclipse with penumbra fraction
- Paths: backend/orbit/
- Blocked by: T02
- Due: Sat 17:00
- Accept:
  - `illumination(sat_id)` on the 10 s grid over 24 h gives the visible fraction of the solar disc. Compute it from the apparent angular radii of Sun and Earth and their angular separation (disc-overlap area). Do not use `is_sunlit()` for the fraction.
  - The §8 eclipse test passes: 500 km, beta 0 → 35.8 ± 1 min; values in [0,1]; penumbra intervals have nonzero duration.
  - Runtime for 24 sats × 24 h is recorded in progress/B2.md; results are cached to disk.

### T04 [B2] Contact windows, next contact, beta angle
- Paths: backend/orbit/
- Blocked by: T02
- Due: Sat 18:00
- Accept:
  - `contacts()`, `next_contact()` and `beta_deg()` match CONTRACTS, use min elevation 10°, and are built on Skyfield `find_events`.
  - Tests check AOS < LOS, durations of 2–15 min, sorted output, and no overlap within one sat–station pair.

### T05 [A2] FastAPI + WebSocket server + sim clock (mock-backed)
- Paths: backend/api/
- Blocked by: T01
- Due: Sat 19:00
- Order: A2 does T09 first (the globe is the first-15-seconds wow and needs the most iteration); A1 calls backend modules directly, so the API can come later.
- Accept:
  - Every WS message and REST endpoint in CONTRACTS.md exists and has a schema test.
  - Mock providers sit behind one import switch and are replaced by the real modules as they merge.
  - Sim clock runs at 1×–600× via `POST /api/clock`; `fleet_state` is sent at 1–2 Hz.

### T06 [A1] Agent loop skeleton with stub model
- Paths: backend/agent/
- Blocked by: T01
- Due: Sat 17:00
- Accept:
  - Function-calling schemas for the 8 tools match CONTRACTS.md exactly.
  - A dispatcher calls the backend interfaces (mocks until they merge).
  - A scripted stub model runs Detect → Diagnose → Plan → Verify → Schedule → Approve on mock data, with a step cap of 25.
  - Trace events go through the recorder interface (a no-op until T13 merges).

### T07 [B1] Truth EPS simulator
- Paths: backend/eps/
- Blocked by: T01
- Due: Sat 19:00
- Accept:
  - Models:
    - solar array: area, efficiency, incidence, degradation;
    - Li-ion battery: capacity, SOC, OCV curve, internal resistance, charge efficiency, temperature effect;
    - loads with essential flags;
    - undervoltage load shedding;
    - a one-node thermal model.
  - Sensor noise, bias and quantization are applied; telemetry is NaN outside contacts.
  - Every parameter lives in `backend/eps/params.py`, marked "assumed" or with its source. Where `data/pcoe/` is present, the OCV curve and the capacity-fade rate are fitted from the NASA PCoE battery data and cited; otherwise they are marked assumed. The §8 EPS tests pass.

### T08 [B1] Seeded faults (5 kinds)
- Paths: backend/eps/
- Blocked by: T07
- Due: Sat 20:00
- Accept:
  - Implements the 5 kinds:
    - array_degradation;
    - stuck_on_load (ignores LOAD_OFF);
    - capacity_fade;
    - sensor_bias (the reading is wrong, the system is fine);
    - beta_season (fleet-wide environment).
  - The same seed gives identical telemetry.
  - No agent tool can reach a fault spec; `reveal()` is used only by `/api/reveal`.

### T09 [A2] Offline Cesium globe (the first-15-seconds wow)
- Paths: frontend/
- Blocked by: T01
- Due: Sat 17:30
- Accept:
  - Vite + React + CesiumJS with no ion token (`Ion.defaultAccessToken` empty), NaturalEarthII imagery from Cesium's own assets, geocoder and baseLayerPicker off. Dark mission-control theme.
  - Boots in under 3 s to a live globe with no loading screen, fed by a local mock `fleet_state` until T05 merges.
  - Draws: 24 sat markers with one-orbit-ahead tracks, day/night lighting from `sun_direction`, the Earth shadow volume (umbra cylinder plus a soft penumbra ring) behind the night side, station cones, and each marker fading with `illum` as it crosses the penumbra. A bottom ticker shows fleet stats.
  - Attract mode: slow camera orbit at 120× sim speed; the scene must look alive before anyone touches it.
  - A permanent "Real / Simulated" legend: orbits, Sun geometry, stations, contacts = real; power, thermal and faults = simulated. Required by the Radial track rules.
  - Works with Wi-Fi off; check this by hand and note it in progress/A2.md.

### T10 [A1] Safety layer
- Paths: backend/agent/
- Blocked by: T06
- Due: Sat 19:30
- Accept:
  - Command allowlist; LOAD_OFF on an essential load is rejected.
  - The uplink must fall inside a contact window (orbit contract; a mock is fine until it merges).
  - Canonical plan hash.
  - Narration filter (digits only in placeholders and allowed ids) and placeholder renderer.
  - The §8 agent tests pass.

### T11 [B1] Vectorized planning model
- Paths: backend/eps/
- Blocked by: T07
- Due: Sat 21:00
- Accept:
  - A simpler model with deliberately mismatched parameters; document the differences from the truth model.
  - Batch shape (sat, hyp, plan, mc); keeps only running min/max, no time history; float32 option.
  - Logs evals/s.

### T12 [B1] Verifier: simulate, rank, infeasible
- Paths: backend/verify/
- Blocked by: T11, T03, T04
- Due: Sat 22:30
- Accept:
  - `simulate()` matches CONTRACTS. Monte Carlo defaults to N=100, with documented σ on array efficiency, capacity, load power, initial SOC and sensor bias.
  - Filters to plausible hypotheses, ranks in the order 1–4 from CONTRACTS, and returns infeasible with SAFE_MODE when nothing passes.
  - The §8 verifier tests pass.

### T13 [B2] Flight recorder + replay
- Paths: backend/recorder/
- Blocked by: T01
- Due: Sat 19:30
- Accept:
  - JSONL format as in CONTRACTS.
  - `ReplayModel` returns recorded model responses in order.
  - Replaying a recorded run reproduces identical tool results and the same final plan hash.
  - Switched on by the `PENUMBRA_REPLAY` env var; `PENUMBRA_REPLAY_PACE_MS` paces events (0 for benchmarks).

### T14 [B2] Uplink scheduler (CP-SAT + greedy)
- Paths: backend/sched/
- Blocked by: T04
- Due: Sat 21:30
- Accept:
  - CP-SAT with optional intervals, NoOverlap per station, priorities and deadlines, and a 5–10 s time limit. Greedy fallback.
  - On instances of 6 requests or fewer, the CP-SAT objective equals brute force.
  - Returns `solver`, `optimal` and `solve_ms`; solve time for 24 sats / 3 stations is logged.

### T15 [A2] Panels: triage queue, tool-call trace, hypothesis bars
- Paths: frontend/
- Blocked by: T09
- Due: Sat 22:00
- Accept:
  - The trace streams tool name, args and a result summary, with no chat bubbles.
  - The triage queue counts down to each sat's next contact.
  - Hypothesis bars update from `hypotheses` messages.
  - The layout leaves room for the matrix.

### T16 [B1] Hypothesis scoring + diagnostic update
- Paths: backend/eps/, backend/verify/
- Blocked by: T07, T08, T11
- Due: Sun 00:00
- Accept:
  - Residual-based likelihoods per hypothesis, comparing the planning model with downlinked telemetry and handling data gaps. Produces a posterior and the plausible set.
  - Telemetry from a diagnostic test updates the posterior.
  - On 20 seeds, measure how often the true fault ranks first after one diagnostic. Report the number; the target is ≥ 0.8.

### T17 [A1] Real Gemini integration
- Paths: backend/agent/
- Blocked by: T10, T13
- Due: Sat 23:00
- Accept:
  - The model comes from `GEMINI_MODEL` (a pinned version). Retries with backoff on 429/5xx.
  - Tokens and latency are written to the recorder.
  - The system prompt says: orchestrate tools, never compute physics, numbers only through placeholders.
  - Every live run is recorded.

### T18 [A2] Plan × hypothesis matrix + schedule conflict view
- Paths: frontend/
- Blocked by: T15
- Due: Sun 00:30
- Accept:
  - Green/red cells show worst-case SOC from SimResult; the UI never computes it. Cells light up one by one as `matrix` messages arrive, with a short pulse on each.
  - Shows the Monte Carlo pass rate, highlights the plan the backend ranked best, and shows `infeasible` with the SAFE_MODE recommendation when that is the status.
  - A conflict view (from `schedule`) appears when two sats want the same station.

### T19 [B2] Bench harness: B1 safety + ablations
- Paths: bench/
- Blocked by: T12, T13, T17
- Due: Sun 01:00
- Accept:
  - `bench/run.py --seeds 100` runs 4 arms:
    - brute force over the allowlist (upper bound);
    - a random valid plan;
    - Gemini without the verifier, judged by the truth sim;
    - Gemini with the verifier.
  - Gemini responses are cached, so reruns cost nothing.
  - Writes `bench/results/b1.json` with n, violations and margins per arm.

### T20 [A1] Diagnostic-test flow
- Paths: backend/agent/
- Blocked by: T16, T17
- Due: Sun 01:30
- Accept:
  - The agent proposes a diagnostic during the current pass (e.g. LOAD_OFF(AUX) for 120 s); the result updates the hypothesis scores before the agent plans.
  - A recorded run shows a stuck-on load confirmed by the diagnostic.

### T21 [A2] Flight Director approval, fast-forward, reveal
- Paths: frontend/
- Blocked by: T18
- Due: Sun 02:00
- Accept:
  - The Approve button shows the plan_hash prefix and the tier, and calls `/api/approve`. Red (failed) plans also have a button; pressing one shows the refusal with the failing hypotheses, so the judge sees "physics has veto".
  - Fast-forwards through the next eclipse. The SOC chart shows the verifier's p5–p95 band from `trajectory`, the red SOC_MIN line, and downlinked truth points only inside contacts (operators are blind between passes).
  - "Reveal" after execution: agent diagnosis and the true fault side by side, and the hidden true SOC line drawn over the band. No score, no game timer.

### T22 [B1] Single-satellite scenario end-to-end (02:00 gate)
- Paths: backend/fleet/, runs/
- Blocked by: T05, T12, T16, T17, T34
- Due: Sun 02:00
- Accept:
  - Scenario `single-stuck-load` reaches approval → execution both in a live (Gemini) run and in replay.
  - After the approved plan, the truth sim keeps SOC above the limit through the next eclipse.
  - The demo run is saved to `runs/demo/`. Integration bugs go to the owners as REQUESTs.

### T23 [B1] Fleet: triage, peer comparison, tier policy
- Paths: backend/fleet/
- Blocked by: T12, T14
- Due: Sun 04:00
- Accept:
  - Triage ranks by time-to-critical versus the next contact and carries the per-sat `status` from CONTRACTS. Flight rules live in `backend/fleet/flight_rules.py` with ids `FR-<n>`.
  - On test seeds, peer comparison separates a fleet-wide environmental cause (beta season) from a single-sat hardware fault.
  - Tier 1 / Tier 2 policy as in CONTRACTS, with tests.

### T24 [B2] B4 scale benchmark
- Paths: bench/
- Blocked by: T11, T14
- Due: Sun 03:00
- Accept:
  - Measures evals/s and peak RAM at 24, 1,000 and 10,000 sats. The 10k run uses float32, a 60 s step, a 3 h horizon and MC 20; state these settings in the results.
  - Measures scheduler solve time at 24 and 200 sats.
  - Writes `bench/results/b4.json`.

### T25 [B2] E2E smoke test from a clean checkout
- Paths: tests/
- Blocked by: T05, T13, T22
- Due: Sun 04:00
- Accept:
  - `tests/e2e/` starts the API in replay mode, runs the demo scenario, and asserts that matrix, approval and execution messages appear.
  - Runs offline in CI.

### T26 [A1] Fleet-mode agent for the fault storm
- Paths: backend/agent/
- Blocked by: T20, T23, T14
- Due: Sun 05:30
- Accept:
  - The agent works the triage queue for 3 sats sequentially in triage order, calls `schedule_uplink`, handles station conflicts, and separates Tier 1 auto-execution (trace shows the flight rule id) from Tier 2 approval.
  - When nothing passes, it says infeasible and recommends SAFE_MODE.
  - Produces a recorded run.

### T27 [B1] Fault-storm and beta-season scenarios
- Paths: backend/fleet/, runs/
- Blocked by: T08, T23
- Due: Sun 05:30
- Accept:
  - Scenario `fault-storm` is tuned for three outcomes: SAT-12 mild array degradation → Tier 1 auto-execute; SAT-07 stuck AUX load → Tier 2 approval; SAT-19 severe capacity fade plus sensor bias → infeasible, SAFE_MODE recommended. Scenario `beta-season` is fleet-wide. Both deterministic.
  - Peer comparison classifies both scenarios correctly.
  - The storm demo run is saved to `runs/demo/`.

### T28 [A2] Voice callouts + scale slider
- Paths: frontend/, backend/api/
- Blocked by: T15, T24
- Due: Sun 05:30
- Accept:
  - ElevenLabs TTS for `callout` messages, with cached audio that works offline; a mute toggle.
  - A slider (24 → 1k → 10k) shows `bench/results/b4.json`, labeled "measured offline (headless)".

### T29 [B2] B2, B3, B5 benchmarks
- Paths: bench/
- Blocked by: T19, T23, T27
- Due: Sun 08:30
- Accept:
  - B2: diagnosis accuracy and tool calls, with and without peer comparison and the diagnostic test.
  - B3: FCFS versus triage + scheduler, with a simulated operator at X min per decision; state X.
  - B5: payload uptime versus "safe mode immediately".
  - Every results file reports n and the seeds.

### T30 [B2] (stretch) M1 step 1: real-data sanity check (PCoE battery, BIRDS)
- Paths: bench/, data/
- Blocked by: T08
- Due: Sun 08:30
- Accept:
  - Uses the datasets a human downloaded into `data/pcoe/` and, if present, `data/birds/`.
  - PCoE: our capacity_fade fault versus a real cell's capacity curve, one plot and one metric. BIRDS (if present): the real panel-failure signature versus our array_degradation fault.
  - Writes `bench/results/m1.json` and a PNG, labeled "sanity check, not validation".

### T31 [B2] (stretch, proposed) M2 conjunction avoidance coupled with power
- Paths: backend/orbit/, bench/
- Blocked by: T23
- Due: only if T01–T29 are merged by Sun 04:00 and A+B approve
- Accept:
  - The humans define the criteria at approval time. EPS changes go to B1 as a REQUEST.

### T32 [A2] Public replay demo on Vultr (MLH Best Use of Vultr)
- Paths: deploy/, backend/api/, frontend/
- Blocked by: T21, T22, T35
- Due: Sun 03:30
- Accept:
  - `deploy/` holds a reproducible setup (a single script, plus systemd or Docker) that serves the API and the built frontend from one Vultr instance. A2 writes it; a human runs it on the server.
  - The public instance runs in replay mode only (`PENUMBRA_REPLAY`), with no Gemini or ElevenLabs keys on the server. The firewall allows only SSH (key-only) and web.
  - The public URL plays the demo scenario end to end and goes on Devpost. The local laptop demo stays the primary demo.

### T33 [B2] Run the B4 scale benchmark on Vultr compute
- Paths: bench/
- Blocked by: T24, T32
- Due: Sun 05:30
- Accept:
  - The same `bench` command runs on the Vultr instance; a human starts it over SSH. Results go to `bench/results/b4_vultr.json` with the instance's vCPU and RAM recorded.
  - The laptop results from T24 stay for comparison, and the slider labels which machine produced each number.

### T34 [B1] Detector, time-to-critical and alerts
- Paths: backend/fleet/
- Blocked by: T11, T16
- Due: Sun 00:30
- Accept:
  - `detect(t)` per CONTRACTS, built on the residual machinery from T16; never reads fault specs.
  - `time_to_critical_s` comes from the planning model under the worst plausible hypothesis.
  - Each new warning/critical alert emits one `alert` and one `callout` with the text "Flight, SAT-nn battery trending low, {AL-n.time_to_critical_min} minutes to critical" (rendered by the backend).
  - Test: in `single-stuck-load`, the alert fires at the first contact after onset and not before.

### T35 [A2] Demo director: pacing, Fault storm button, run-live switch
- Paths: backend/api/, frontend/
- Blocked by: T13, T21
- Due: Sun 03:00
- Accept:
  - State machine `attract → storm → agent → approval → fastforward → reveal` per CONTRACTS, with the clock speeds listed there; sim time is frozen while the agent works.
  - A single large "Fault storm" button starts the recorded storm in paced replay; a small "run live" toggle uses Gemini for the next run instead.
  - The whole storm plays in under 150 s of wall time in replay, with the judge's Approve as the only required click. Measure and note the timing in progress/A2.md.
  - If the WebSocket drops, the page keeps the last state and shows a reconnect badge instead of a blank screen.
