# Penumbra layout

All boxes are `x, y, w, h` in CSS px from the top-left of the viewport. Tokens are in [tokens.css](tokens.css).
Coverage = share of the full-bleed globe canvas hidden by HUD panels. **Limit: 40% in every state** (DESIGN.md).

## Z-order (low to high)
| z | Layer | Notes |
|---|---|---|
| 0 | Cesium canvas | full-bleed, `pointer-events` on only in attract |
| 10 | Globe labels | DOM labels and leader lines for alerting / focused sats |
| 20 | HUD panels | triage, hypotheses, trace strip, legend |
| 30 | Top bar | |
| 40 | Overlay | matrix + SOC chart, fast-forward chart, reveal |
| 50 | Flight Director bar | above the overlay; the one place the judge clicks |
| 60 | Banners | refusal banner, `RECONNECTING` badge |
| 90 | Presenter hint | hotkey hint, hidden by default |

## 1920x1080 expo screen

```
0                                                       1448 1464                 1904 1920
+--------------------------------------------------------------------------------------+ 0
| PENUMBRA   [AI PROPOSES] -> [PHYSICS VERIFIES] -> [HUMAN APPROVES]  REPLAY 14:32 600x | 64
+------------------------------------------------------------------+-------------------+ 80
|                                                                  | TRIAGE QUEUE  SIM |
|                                                                  |  SAT-07 ...       |
|                        GLOBE STAGE (free)                        |  SAT-19 ...       |
|                        Earth centre (732, 480)                   |  SAT-12 ...       |
|                                                                  +-------------------+ 528/544
|                                                                  | HYPOTHESES    SIM |
|                                                                  |  H3 Stuck-on load |
| +-------------------------+                                      |  H1 Array degr.   |
| | REAL / SIMULATED legend |                                      |  ...              |
| +-------------------------+                                      +-------------------+ 880
+------------------------------------------------------------------+-------------------+ 896
| caption line (callout, 22px)                                     | FLIGHT DIRECTOR   |
| 14:32:08  PROPOSES  propose_plan  SAT-07 [LOAD_OFF AUX]  -> P-3  | [ APPROVE P-3 ]   |
| 14:32:09  VERIFIES  simulate      P-3 all x MC        -> PASS    | hash a3f9c1 T2    |
+------------------------------------------------------------------+-------------------+ 1064
```

### Boxes, storm and agent states
| Element | x | y | w | h | Area |
|---|---|---|---|---|---|
| Top bar | 0 | 0 | 1920 | 64 | 122,880 |
| Triage queue | 1464 | 80 | 440 | 448 | 197,120 |
| Hypothesis bars | 1464 | 544 | 440 | 336 | 147,840 |
| Trace strip | 16 | 896 | 1432 | 168 | 240,576 |
| Flight Director bar | 1464 | 896 | 440 | 168 | 73,920 |
| Legend | 32 | 808 | 420 | 72 | 30,240 |
| **Total** | | | | | **812,576 = 39.2%** |

Free globe stage: `16, 80, 1432, 800`. Camera frustum offset puts the Earth's centre at (732, 480).

### Inside each panel (1920)
- **Top bar** (h 64, padding 0 24): wordmark `PENUMBRA` 22px cond 600 +0.2em, then fleet line 15px ink-2. Centre: stage stepper, three 30px cond 600 labels with a 3px bar under the active one, chevrons drawn in SVG, 32px between. Right: mode chip (`REPLAY` / `LIVE · Gemini`), sim clock `HH:MM:SS UTC` 22px mono, speed chip `600x` / `PAUSED`, link dot.
- **Triage queue** (padding 16): label row 40px. Rows 72px each, 5 visible, scroll beyond: rank 15px mono ink-3 | `SAT-07` 30px cond 600 | severity chip (watch ink-2 outline, warning amber outline, critical amber fill) | status label 15px | right: `AOS 04:12` countdown 22px mono + `TTC 3h12m` 15px mono ink-2. The focused row has a 1px ink frame and `--c-raise` fill.
- **Hypothesis bars** (padding 16): label row 40px with the sat id. Six rows of 44px: `H3` 15px mono ink-3, `labels[H]` 18px, bar track 140px wide x 8px, value 17px mono. Leading hypothesis bar in ink, others ink-2, not in `plausible` -> 30% opacity + `RULED OUT`.
- **Trace strip** (padding 12 24): caption line 22px ink, 40px tall (current `callout.text`). Then 4 rows of 28px, 17px mono: time (ink-3, 96px) | stage tag (88px: `PROPOSES` ink, `VERIFIES` green when the result passed / red when it failed, `APPROVES` amber) | tool name 600 (200px) | args (flex, ellipsis) | `->` result summary (320px). Newest row at the bottom; a 24px mask fades the oldest row out at the top.
- **Flight Director bar** (padding 16): label `FLIGHT DIRECTOR` 15px. Body depends on state (see MOTION.md): FAULT STORM button (attract), `Waiting for a verified plan` (agent), approve block (approval). Primary button 64px tall, full width, 30px cond 600 label.
- **Legend** (padding 12 16): two rows of 24px, swatch 28x10 (solid ink line / hatch), 15px text.

### Approval state (overlay open)
Triage, hypotheses and the trace strip leave; the hypotheses reappear as the matrix row headers. The overlay sits right, directly above the Flight Director bar, so evidence and button share one column of attention.

| Element | x | y | w | h | Area |
|---|---|---|---|---|---|
| Top bar | 0 | 0 | 1920 | 64 | 122,880 |
| Overlay: matrix + SOC chart | 904 | 280 | 1000 | 600 | 600,000 |
| Flight Director bar | 1464 | 896 | 440 | 168 | 73,920 |
| Legend | 32 | 808 | 420 | 72 | 30,240 |
| **Total** | | | | | **827,040 = 39.9%** |

Free globe stage: `0, 64, 888, 1016`; frustum offset moves the Earth's centre to (444, 540) and the camera frames the focused sat.

Overlay inside (padding 24):
- Header 56: `SAT-07` 30px cond + `Plan x hypothesis` 18px ink-2 + right `SIM` tag and `MC n = {sim.n}` 15px mono (from backend).
- Matrix 308: header row 44 (plan ids `P-1`..`P-n`, 22px cond, plus `SAFE_MODE` column when present), 6 hypothesis rows of 44. Row header 320 wide: `H3` + `labels[H]` + posterior bar 96x6 + value. Plan columns 124 wide, up to 5. Cell: `PASS`/`FAILS` 15px cond + `min_soc_p5` 18px mono; pass = `--c-verified-14` fill, 1px green frame, green text; fail = `--c-refused-14`, red; pending = `--c-raise`, em dash; implausible row = hatch at 30%, `RULED OUT`. Footer line under the grid: per plan `pass_rate` and `worst_margin_soc` 15px mono.
- Best plan (from `rank().ordered[0]`): 2px green frame around the whole column + `BEST · ranked by verifier` tag above it. Selected plan: column header underlined in ink. Clicking a column header selects it.
- Infeasible: a 48px red-tinted row replaces the header line: `INFEASIBLE · no plan survives every plausible cause · Recommend SAFE_MODE`; the SAFE_MODE column gets the frame.
- SOC chart 200: x = sim time, y = SOC 0-100%. Layers bottom to top: eclipse bands (`--c-eclipse`, penumbra edges as 8px gradients), contact windows (`--c-contact` + `AOS` tick labels), p5-p95 band (`--c-band`), p50 line 1.5px ink-2, SOC_MIN 2px dashed red with label `SOC_MIN`, downlinked truth points (white 5px dots, only inside contacts), time cursor 1px ink. Axis labels 15px. Tag `SIM` top-right.
- Conflict view (T18, from `schedule`): when two sats want one station, a 40px row under the chart: station id, the two plan ids, the assignment, `unscheduled` in amber.

Flight Director bar in approval: `TIER 2 · HUMAN REQUIRED` 15px amber | button `APPROVE P-3` (green fill when the selected plan passed; red 2px outline + `FAILED VERIFICATION` sub-label when it failed, still clickable per T21) | `hash a3f9c1e0 · uplink SVALBARD AOS 14:52:10Z` 15px mono.

### Fast-forward and reveal
| State | Elements | Coverage |
|---|---|---|
| attract | top bar, legend (moved to y 920), fleet ticker `16, 1008, 1432, 56`, FD bar with FAULT STORM | 307,232 = 14.8% |
| fastforward | top bar, triage, SOC chart panel `904, 560, 1000, 320`, FD bar, legend | 744,160 = 35.9% |
| reveal | top bar, reveal overlay `904, 280, 1000, 600` (diagnosis vs truth 220 + SOC chart 300), FD bar, legend | 827,040 = 39.9% |

Reveal overlay inside: two equal columns, `AGENT DIAGNOSED` (H, `label`, posterior) and `TRUE FAULT` (`truth.kind`, target, onset as UTC), 22px values, no score and no match badge. Below, the SOC chart with the truth line drawn over the band.

## 1366x768 laptop variant
Same structure, smaller boxes. Inset and gaps 12. Type tiers from `tokens.css` media query.

| Element | x | y | w | h | Area |
|---|---|---|---|---|---|
| Top bar | 0 | 0 | 1366 | 48 | 65,568 |
| Triage queue (4 rows of 56) | 1034 | 60 | 320 | 316 | 101,120 |
| Hypothesis bars (6 rows of 32) | 1034 | 388 | 320 | 244 | 78,080 |
| Trace strip (caption 30 + 3 rows of 22) | 12 | 644 | 1010 | 112 | 113,120 |
| Flight Director bar | 1034 | 644 | 320 | 112 | 35,840 |
| Legend | 24 | 580 | 300 | 52 | 15,600 |
| **Total** | | | | | **409,328 = 39.0%** |

Approval at 1366: overlay `642, 212, 712, 420` (header 40, matrix 32 + 6 x 32, chart 132; row header 220, plan columns 88) + FD bar + top bar + legend = 416,048 = 39.7%.
At 1366 the stage stepper drops the arrows and uses 22px labels; the fleet line in the top bar is hidden.

## Data binding (what feeds what)
| Component | WS message / endpoint | Fields used |
|---|---|---|
| Globe | `fleet_state` | `t, speed, sun_direction, sats[].lat_deg/lon_deg/alt_km/illum/status` |
| Top bar | `demo_state`, `fleet_state` | `state, speed`, `t` |
| Triage queue | triage list in `fleet_state.sats[].status` + `alert` | `sat_id, severity, time_to_critical_s, next_contact_aos, status` |
| Caption line | `callout` | `text` (already rendered by backend), `audio_url` |
| Trace strip | `trace_event` | `ts, kind, payload` (tool name, args, result summary), `plan_hash` |
| Hypothesis bars / matrix row headers | `hypotheses` | `scores, labels, plausible` |
| Matrix | `matrix` | `results[].per_hyp[H].min_soc_p5/pass_rate`, `passes_all`, `worst_margin_soc`, `complete` |
| SOC chart | `trajectory`, `fleet_state` (truth points) | `t, soc_p5, soc_p50, soc_p95` |
| Conflict view | `schedule` | `assignments, unscheduled` |
| FD bar | `approval_request`, `approval_refused`, `execution`, `POST /api/approve` | `approval_id, plan_hash, tier`, `reason_key, failing_hypotheses` |
| Reveal | `reveal` | `truth, diagnosed, truth_soc` |
