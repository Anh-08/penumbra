# CONTRACTS (DRAFT: A and B approve before 13:00, then this file is frozen)

Python types live in `contracts/` (pydantic v2, owner B1). This file is the human-readable spec. A change needs a `contract-change` PR approved by the human who did not author it.
Tunable numbers (limits, thresholds, σ values) are not contracts. They live in `backend/verify/constants.py` (B1).

## Time, ids, units
- `t`: float POSIX seconds, UTC. Sim grid step `DT = 10 s`. Scenario horizon = 24 h from `T0`.
- `sat_id`: `SAT-01`..`SAT-24`. `station_id`: `SVALBARD`, `TROLL`, `FAIRBANKS` (uppercase, no digits).
- `plan_id` `P-<n>`, `sim_id` `S-<n>`, `approval_id` `AP-<n>`, alerts `AL-<n>`, hypotheses `H0`..`H5`.

## Orbit (`backend/orbit`, B2)
- `illumination(sat_id) -> ndarray[float]` on the grid: 1 = full sun, 0 = umbra, values in (0,1) = fraction of the solar disc visible (penumbra).
- `positions(t) -> {sat_id: {lat_deg, lon_deg, alt_km, illum}}` for the globe.
- `orbit_path(sat_id, t, minutes) -> [[lat_deg, lon_deg, alt_km], ...]` for drawing the track (default: one orbit ahead).
- `sun_direction(t) -> [x, y, z]` unit vector in ECEF, for the terminator and the shadow volume.
- `beta_deg(sat_id) -> float`
- `contacts() -> list[Contact]`, where `Contact{sat_id, station_id, aos, los, max_el_deg}` (min elevation 10°).
- `next_contact(sat_id, t) -> Contact | None`

## EPS (`backend/eps`, B1)
- Loads: `BUS`, `COMMS`, `ADCS` (all essential); `PAYLOAD`, `HEATER`, `AUX` (non-essential).
- Telemetry channels: `soc`, `v_bus`, `i_bat`, `i_array_<panel>`, `p_load`, `t_bat_c`, `load_<LOAD>`. Values are downlinked only inside contacts and are NaN elsewhere.
- `FaultSpec{kind, target: sat_id | "FLEET", params, onset_t}`.
- Fault kinds: `array_degradation`, `stuck_on_load`, `capacity_fade`, `sensor_bias`, `beta_season`.
- Fault specs and seeds never leave `backend/eps`. The only exception is `reveal()`, used by `POST /api/reveal` after execution.
- Hypotheses: `H0` = nominal, `H1`..`H5` = the 5 fault kinds in the order above. Each has parameter ranges and a human label (`labels[H]`, e.g. "Stuck-on load").

## Detect (`backend/fleet`, B1)
- `detect(t) -> list[Alert]`, `Alert{alert_id, sat_id, severity: "watch" | "warning" | "critical", time_to_critical_s, next_contact_aos, reason_key}`.
- `time_to_critical_s` = time until SOC reaches SOC_MIN if nothing is done, under the worst plausible hypothesis. `None` if SOC never reaches it inside the horizon.
- Detection uses residuals of downlinked telemetry against the planning model; it never reads fault specs.
- Every new `warning` or `critical` alert produces one `alert` message and one `callout`.

## Commands and plans
- Allowlist:
  - `LOAD_OFF(load)` (non-essential loads only)
  - `LOAD_ON(load)`
  - `SET_PAYLOAD_DUTY(0..1)`
  - `SET_HEATER_SETPOINT(-5..20 °C)`
  - `SAFE_MODE()`
- `Command{t_exec, name, args}`.
- `Plan{plan_id, sat_id, kind: "plan" | "diagnostic", uplink: Contact, commands, created_by}`.
- Rule: the uplink must lie inside a contact window of that satellite, and `t_exec ≥ uplink.aos` (time-tagged commands may execute later, for example during eclipse).
- `plan_hash = sha256(canonical_json({sat_id, kind, uplink, commands}))`.

## Verification (`backend/verify`, B1)
- `simulate(plan_id, hypotheses = "all" | list[H], monte_carlo = N) -> SimResult`
- `SimResult` fields:
  - `sim_id`, `plan_id`, `plan_hash`
  - `per_hyp: {H: {pass_rate, min_soc_p5, min_v_bus, t_bat_min_c, t_bat_max_c, payload_uptime}}`
  - `plausible: list[H]`, `passes_all`, `worst_margin_soc`, `payload_uptime_worst`, `n_commands`
- Hard constraints: SOC ≥ SOC_MIN, V_bus ≥ V_MIN, T_bat inside [T_MIN, T_MAX], essential loads on.
- Ranking, computed by the backend only:
  1. passes_all over the plausible hypotheses;
  2. highest `worst_margin_soc`;
  3. highest `payload_uptime_worst`;
  4. fewest commands.
- `rank(sat_id) -> {ordered: [plan_id], status: "feasible" | "infeasible"}`. Status is `infeasible` when the only passing plan is `SAFE_MODE`; the agent must then say so and recommend SAFE_MODE.
- `trajectory(plan_id, hypothesis) -> {t: [], soc_p5: [], soc_p50: [], soc_p95: []}` for one plan, used only for drawing.
- Batch arrays have shape `(sat, hyp, plan, mc)`. Batch mode keeps only running min/max, never the full time history.

## Fleet (`backend/fleet`, B1)
- `triage() -> list[{sat_id, severity, time_to_critical_s, next_contact_aos, priority, status: "queued" | "working" | "auto_executed" | "awaiting_approval" | "executed" | "safe_mode_recommended"}]`
- `peer_compare(sat_id) -> {env_score, hw_score, explanation_keys}`
- Tier policy: Tier 1 = pre-approved low-risk command classes (flight rules `FR-<n>`, listed in `backend/fleet/flight_rules.py`) whose `worst_margin_soc` ≥ TIER1_MARGIN; they auto-execute and the trace shows the flight rule id. Everything else is Tier 2 and needs a human.
- Demo scenarios (`backend/fleet/scenarios.py`): `single-stuck-load`, `fault-storm`, `beta-season`.
  - `fault-storm` has three outcomes by design: `SAT-12` mild array degradation → Tier 1 auto-execute; `SAT-07` stuck AUX load → Tier 2 human approval; `SAT-19` severe capacity fade plus sensor bias → infeasible, SAFE_MODE recommended.
  - The agent handles alerts sequentially in triage order.

## Scheduler (`backend/sched`, B2)
- `schedule(requests: list[{plan_id, sat_id, priority, deadline_t, duration_s}])` returns:
  - `assignments: [{plan_id, station_id, start_t, end_t}]`
  - `unscheduled: [plan_id]`
  - `solver: "cpsat" | "greedy"`, `optimal: bool`, `solve_ms`

## Agent tools (`backend/agent`, A1): exact names
| Tool | Returns |
|---|---|
| `get_fleet_status()` | triage list + alerts |
| `get_telemetry(sat_id, t_from, t_to)` | |
| `score_hypotheses(sat_id)` | `{H: posterior}` and `plausible` |
| `propose_diagnostic_test(sat_id, commands)` | `{plan_id}` or `{error}` |
| `propose_plan(sat_id, commands)` | `{plan_id}` or `{error}` |
| `simulate(plan_id, hypotheses, monte_carlo)` | `SimResult` |
| `schedule_uplink(plan_id)` | |
| `request_approval(plan_id)` | `{approval_id, tier}` |

## Narration rule
- In the agent's free text, digits may appear only:
  - inside placeholders `{<sim_id | plan_id | approval_id | alert_id>.<field>}`, which the backend renders;
  - in ids matching `SAT-\d{2}`, `[PS]-\d+`, `AP-\d+`, `AL-\d+`, `FR-\d+`, `H\d`, `T\d{2}`.
- Anything else is rejected and the model is re-asked, at most 2 times. After that, the UI shows tool results only.

## Approval and execution
- `POST /api/approve {approval_id, plan_hash}` executes only if the hash equals the hash of the latest SimResult for that plan, the plan passed verification, and the uplink window is still open.
- A plan that failed verification can never be approved, by anyone. The attempt is refused with `{refused: true, reason_key, failing_hypotheses}`, shown in the UI, and written to the flight recorder. There is no override.

## Flight recorder (`backend/recorder`, B2)
- JSONL file at `runs/<run_id>.jsonl`, one event per line:
  `{ts, run_id, seq, kind: alert | model_request | model_response | tool_call | tool_result | approval | approval_refused | execution, payload, plan_hash?, tokens?, latency_ms?}`
- Replay: `PENUMBRA_REPLAY=runs/<id>.jsonl` makes the model return the recorded responses in order. Tools re-execute deterministically with the same seeds.
- Pacing: `PENUMBRA_REPLAY_PACE_MS` (default 700) is the delay between replayed events, so the trace reads at human speed. `0` means as fast as possible (benchmarks).

## Demo director (`backend/api`, A2)
- Demo states: `attract → storm → agent → approval → fastforward → reveal`.
- Clock per state: `attract` 120×, `storm` 600× until the first alert, `agent` paused (sim time frozen while the agent works), `approval` paused, `fastforward` 600× through the next eclipse, `reveal` paused.
- Default mode is paced replay of a recorded run. A "run live" control switches the model to Gemini for the next scenario; everything else is identical.
- `POST /api/demo {state}` moves the state machine; `demo_state` is broadcast on every change.

## API / WebSocket (`backend/api`, A2)
- WS `/ws`, server → client messages:

| Message | Content |
|---|---|
| `fleet_state` | 1–2 Hz; `t`, `speed`, `sun_direction`, per sat `{sat_id, lat_deg, lon_deg, alt_km, illum, soc, status}` |
| `demo_state` | `{state, speed}` |
| `alert` | an Alert |
| `callout` | `{text, priority, audio_url?}` |
| `trace_event` | a recorder event |
| `hypotheses` | `{sat_id, scores, labels, plausible}` |
| `matrix` | `{sat_id, results: SimResult[], complete: bool}`; sent once per finished SimResult, so cells can light up one by one |
| `trajectory` | output of `trajectory()` for the plan being shown |
| `schedule` | scheduler output |
| `approval_request` | `{approval_id, plan_id, plan_hash, sat_id, tier, sim_id}` |
| `approval_refused` | `{approval_id, reason_key, failing_hypotheses}` |
| `execution` | `{plan_id, sat_id, status, t, flight_rule?}` |
| `reveal` | `{sat_id, truth: FaultSpec, diagnosed: {H, label, posterior}, truth_soc: {t: [], soc: []}}` |

- REST endpoints:
  - `POST /api/scenario {name, seed}`
  - `POST /api/fault-storm`
  - `POST /api/demo {state}`
  - `POST /api/approve`
  - `POST /api/clock {speed}`
  - `GET /api/bench` (serves `bench/results/*.json`)
  - `POST /api/reveal`

## Conjunctions and collision-avoidance burns (T31, additive)
Debris is **simulated** (`data/debris/*.json`, ids `DEB-01`..); orbits, Sun geometry and contacts stay real.
- `Conjunction{conj_id: "CJ-<n>", sat_id, object_id: "DEB-<nn>", tca, miss_km, windows: [ManeuverWindow]}`, with
  `ManeuverWindow{window_id: "W1".."W3", t_start, t_end, dv_mps, burn_s, power_w, miss_after_km}`. Every window ends at
  least `CAM_LEAD_S` (1800 s) before `tca`.
- Orbit (`backend/orbit`): `conjunctions() -> list[Conjunction]`, offline and deterministic.
- Command `CAM_BURN {"window_id": "W<n>"}`. The model picks a window; it never writes a Δv (the window carries dv, burn and power).
  It is listed in `CAM_COMMAND_ARGS`; `COMMAND_ARGS` / `COMMAND_NAMES` (the original five) are unchanged, and
  `ALL_COMMAND_ARGS` is the union the `Command` model validates.
- `Plan.kind` gains `"maneuver"`: exactly one `CAM_BURN` plus optional power commands. `CAM_BURN` is allowed in no other kind.
  The burn's `t_exec` must lie inside its window and before `tca - CAM_LEAD_S`. The model checks the count and the kind;
  safety checks the window time against `conjunctions()`.
- `Alert.reason_key` gains `"conjunction"`. Severity comes from the time to TCA: critical < 2 h, warning < 6 h, else watch.
  `time_to_critical_s` = time to TCA.
- Agent tool `get_conjunctions(sat_id) -> list[Conjunction]`. It is listed in `AGENT_TOOLS_CAM`; `AGENT_TOOLS` (the original eight) is
  unchanged, so code built on it keeps working until it adds the new tool. `propose_plan` accepts `CAM_BURN`.
- Flight rules: a plan containing `CAM_BURN` is **never Tier 1**.
- Verification: kind `maneuver` is judged from its first command to `tca` plus one orbit.
- Scenario `conjunction` is added to `DEMO_SCENARIOS`; the other three are unchanged.
- Narration: `{CJ-<n>.<field>}` placeholders and the ids `CJ-\d+`, `DEB-\d{2}`, `W1`..`W3` are allowed.
