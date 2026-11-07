# Penumbra motion spec (GSAP 3)

Motion exists to show cause and effect: an alert arrives, a cell is verified, a plan is refused, the truth is revealed. One authored moment per state; everything else is still.

## Global rules
1. **No number ever animates.** No count-up, no rolling digits, no flicker. A value appears in its final form, or not at all.
2. Animate only `transform`, `opacity`, `clip-path`, `stroke-dashoffset`, `outline`/`box-shadow`. Never `width`/`height`/`top`/`left`.
3. Entrances: `expo.out`. Exits: `power2.in` at 60-70% of the entrance duration. Time-driven drawing: `none` (linear), because it represents time passing.
4. Every tween is killable. On a `demo_state` change: `gsap.killTweensOf(panel)` and then `gsap.set` the final state explicitly. Never rely on an `onComplete` for correctness.
5. Message queue: if two animated messages arrive less than 90 ms apart, queue them with 90 ms spacing so the eye can follow. Never drop one.
6. Reduced motion (`prefers-reduced-motion`): slides become fades, the shake becomes a single red flash, the draw is instant, the camera orbit stops on a still frame.
7. The globe camera only moves in attract, storm and fastforward. In agent, approval and reveal it is still.

## Duration tokens
| Token | ms | Use |
|---|---|---|
| `--dur-1` | 120 | colour swaps, button press, hover |
| `--dur-2` | 240 | cell fill, row insert, crossfades |
| `--dur-3` | 400 | panels sliding in, best-plan frame |
| `--dur-4` | 600 | overlay open |
| `--dur-shake` | 420 | refusal shake |
| `--dur-draw` | 2000 | reveal truth line |

## Per state

### attract (clock 120x)
| What | Motion | Duration / ease |
|---|---|---|
| Camera | orbits the Earth around its axis, one turn per 90 s | continuous, linear |
| Sat markers | opacity follows `illum`; tween between `fleet_state` ticks | 500 ms, `none` |
| Shadow volume | static geometry; it moves only because the Sun direction does | n/a |
| FAULT STORM button | none. No pulse, no glow. | |

**Never animates:** the Real/Simulated legend and the stage stepper. The judge reads these first.

### storm (clock 600x until the first alert)
| What | Motion | Duration / ease |
|---|---|---|
| FAULT STORM press | `scale: .98` then back | 120 ms, `power2.out` |
| Speed chip | text swaps to `600x`; 3px amber underline grows (`scaleX 0 -> 1`) | 240 ms, `expo.out` |
| Triage + hypotheses + trace strip | `x: 24 -> 0, opacity 0 -> 1`, stagger 80 ms; fleet ticker exits `opacity -> 0` | 400 ms `expo.out` / 240 ms `power2.in` |
| New alert row | inserted at its backend priority; other rows move with GSAP Flip; new row background flashes `--c-alert-12 -> transparent`, severity chip fades in | Flip 240 ms `expo.out`, flash 600 ms `power2.out` |
| Alerting sat on globe | one amber ring expands `scale 1 -> 2.4, opacity 1 -> 0`, then a steady ring stays | 800 ms `expo.out`, once |
| Camera | flies to frame the first alerting sat inside the free stage | Cesium `flyTo` 1.6 s, `QUADRATIC_IN_OUT` |
| Caption line | crossfade old -> new | 240 ms |

**Never animates:** the countdown digits in the triage rows. They change once per second by plain text swap.

### agent (sim time frozen)
| What | Motion | Duration / ease |
|---|---|---|
| Speed chip | swaps to `PAUSED` | instant |
| Stage stepper | active bar slides from `AI PROPOSES` to `PHYSICS VERIFIES` on the first `simulate` tool_call | 320 ms `expo.out` |
| Trace rows | new row `y: 8 -> 0, opacity 0 -> 1`; older rows shift up by transform | 240 ms `expo.out` |
| Hypothesis bars | `scaleX` to the new posterior when `hypotheses` arrives (value label swaps instantly) | 400 ms `expo.out` |
| Overlay open (first `matrix` message) | hypotheses panel `opacity -> 0`; triage and trace exit; overlay `y: 24 -> 0, opacity 0 -> 1` | exits 240 ms `power2.in`, overlay 600 ms `expo.out` |
| **Matrix cells** | each `matrix` message is one plan column. Its cells light up top to bottom, one by one: fill `--c-raise -> pass/fail tint`, frame opacity `0 -> 1`, then one pulse ring (`outline-offset 0 -> 6px`, `opacity 1 -> 0`) | stagger 70 ms per cell, fill 240 ms `expo.out`, pulse 500 ms `power2.out` |
| Best plan (when `complete: true`) | 2px green column frame draws in (`clip-path: inset(100% 0 0 0) -> inset(0)`), then the `BEST` tag fades in | 400 ms `expo.out` + 200 ms |

**Never animates:** a trace row after it lands. No typewriter on tool names, args or results, ever.

### approval (paused)
| What | Motion | Duration / ease |
|---|---|---|
| Stage stepper | active bar moves to `HUMAN APPROVES`, label turns amber | 320 ms `expo.out` |
| Approve block in FD bar | `y: 12 -> 0, opacity 0 -> 1` when `approval_request` arrives | 320 ms `expo.out` |
| Column select | column header underline slides to the clicked plan; FD button label swaps | 240 ms `expo.out`, label instant |
| Button hover / press | fill lightens / `scale .98` | 120 ms |
| **Refused** (`approval_refused`) | FD bar shakes **once**: `keyframes: { x: [0,-10,10,-6,6,0] }`; frame and fill flash to red and stay red-tinted; failing hypothesis rows in the matrix get a red outline (`opacity 0 -> 1`) and a `FAILS` tag; stepper flashes `PHYSICS VERIFIES` red once; refusal text fades in under the button | shake 420 ms `power1.inOut`; outline 200 ms; text 200 ms |
| Accepted (`execution`) | button fill turns green and its label swaps to `EXECUTED` with a drawn check (`stroke-dashoffset`); all three stage bars turn green | 240 ms `expo.out`, check 320 ms |

```js
// refusal: shake once, then hold the red state
gsap.timeline()
  .to(fdBar, { keyframes: { x: [0, -10, 10, -6, 6, 0] }, duration: 0.42, ease: "power1.inOut" })
  .to(fdBar, { borderColor: "var(--c-refused)", backgroundColor: "var(--c-refused-14)", duration: 0.12 }, 0)
  .fromTo(failingRows, { outlineColor: "rgba(255,84,104,0)" }, { outlineColor: "rgba(255,84,104,1)", duration: 0.2 }, 0.1)
  .from(refusalText, { opacity: 0, duration: 0.2 }, 0.2);
```

**Never animates:** the matrix values and the `plan_hash` prefix. They are the evidence the judge is reading; no pulse, no shimmer, no highlight sweep after they land.

### fastforward (600x through the next eclipse)
| What | Motion | Duration / ease |
|---|---|---|
| Overlay | matrix collapses (`clip-path` from bottom), leaving the SOC chart panel in place | 400 ms `expo.out` |
| Triage | returns `x: 24 -> 0, opacity` | 400 ms `expo.out` |
| Camera | follows the executed sat into the shadow | Cesium `trackedEntity` or `lookAt` each tick |
| Sat marker | opacity follows `illum` through penumbra into umbra and out | per tick, 500 ms linear |
| Time cursor on chart | moves with sim `t` | per tick, linear |
| Truth points | appear only inside contact windows, `scale 0 -> 1` | 160 ms `back.out(2)` |

**Never animates:** the p5-p95 band and the SOC_MIN line. They are the verifier's promise and are drawn once, static.

### reveal (paused)
| What | Motion | Duration / ease |
|---|---|---|
| Overlay | crossfade to the reveal layout | 240 ms |
| Diagnosis vs truth | `AGENT DIAGNOSED` column fades in, then `TRUE FAULT` 120 ms later | 320 ms `expo.out` each |
| **True SOC line** | drawn left to right over the band: `stroke-dashoffset: length -> 0` | 2000 ms, `none` |

**Never animates:** the diagnosis and truth text. It lands once and stays; nothing else moves while the line draws.

## Implementation notes for A2
- One `motion.ts` with the tokens above as JS constants and one function per moment: `insertAlertRow`, `lightColumn(planId)`, `markBest(planId)`, `refuse(failingH)`, `approve()`, `drawTruth(path)`.
- Matrix cells exist in the DOM from the start (pending state), so lighting a column never shifts layout.
- Use `gsap.matchMedia()` with `(prefers-reduced-motion: reduce)` for the reduced variants.
- Plugins: core + Flip only. No SplitText, no ScrollTrigger.
