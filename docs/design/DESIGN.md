# Penumbra design direction

Status: design session output, Sat. Owner of `docs/`: B1. Implementer: A2 (T09, T15, T18, T21, T35).
Companion files: [LAYOUT.md](LAYOUT.md), [MOTION.md](MOTION.md), [STORYBOARD.md](STORYBOARD.md), [tokens.css](tokens.css), [mock/index.html](mock/index.html).
Figma: see "Figma" at the end of this file.

## Context (assumed from CLAUDE.md and CONTRACTS.md; there is no PRODUCT.md)
- Primary viewer: a hackathon judge standing 1 to 2 m from a 27"+ 1080p screen in a bright, loud hall. Attention budget: 15 s to stop, 3 min to understand.
- Secondary: the presenter (A or B), who drives the demo with one big button and talks.
- Story the screen must tell without the presenter: **AI proposes, physics verifies, humans approve.**
- Mode: Operate (a mission-control tool), with an attract state that has to work like a poster.

## Visual direction: "quiet console, loud verdicts"
Dark mission control. The Cesium globe and Earth's shadow are the hero; everything else is a thin instrument layer on top of space. Panels are near-black, hairline-framed and nearly silent. Color appears only when it carries a verdict, so a judge 2 m away can read the state of the demo from color alone, and then confirm it from the words next to it.

What we refuse: chat bubbles, glowing neon borders, gradient text, glass as decoration, icon-heading-text cards, hero metrics, count-up numbers, emoji.

## Color grammar (one meaning per accent)
| Token | Hex | Means | Used on | Never used for |
|---|---|---|---|---|
| `--c-alert` amber | `#FFB224` | Needs attention | warning/critical alerts, alerting sat ring, "needs approval", HUMAN APPROVES stage while waiting, FAULT STORM button | anything passed or failed |
| `--c-verified` green | `#35D49A` | Physics verified it / it ran | passing matrix cells, best-plan frame, APPROVE button on a passing plan, executed, auto-executed | decoration, "online" dots |
| `--c-refused` red | `#FF5468` | Physics says no | failing matrix cells, refused approval, SOC_MIN line, SAFE_MODE recommended, failing hypotheses | alerts (alerts are amber, even critical) |

- Everything else is ink on space: `--c-ink` / `--c-ink-2` / `--c-ink-3` on `--c-panel`.
- The AI has no color. Model output is neutral ink, so it cannot look like a verdict. Hypothesis bars, the SOC band and p50 line are neutral too.
- Color is never alone: every colored state also has a word (PASS, FAILS, REFUSED, NEEDS APPROVAL) or a shape (check, cross, hatch).
- Contrast on `--c-panel-solid`: ink 16:1, ink-2 8:1, ink-3 4.9:1, amber 11:1, green 10:1, red 6:1.

## Typography
- **Barlow / Barlow Semi Condensed** for UI. Barlow is drawn after highway signage, built to be read at distance and at a glance. Semi Condensed for uppercase labels, the stage stepper and sat ids, so long words fit narrow columns.
- **JetBrains Mono** only for data: the tool-call trace (it is code), numbers, timestamps, hashes. Always `font-variant-numeric: tabular-nums slashed-zero` so countdowns do not jitter.
- Self-host all three with `@fontsource/*` in `frontend/` (T09 must work with Wi-Fi off). The mock uses Google Fonts only for convenience.

Reading tiers at 1920x1080:
| Tier | Size | Weight | What lives here |
|---|---|---|---|
| Poster | 48px | 600 | attract statement only |
| 2 m | 30px | 600 cond | stage stepper, sat ids in alerts, APPROVE label, REFUSED, INFEASIBLE |
| Caption | 22px | 500 | the narration caption line, panel focus value |
| 1 m | 18px body / 17px mono | 400-500 | triage rows, matrix labels and values, trace rows |
| Label | 15px | 600 cond, uppercase, +0.08em | panel labels, legend, axis ticks. **Nothing on screen is smaller than 15px.** |

At 1366x768 (laptop fallback, ~1 m) the tiers drop to 34 / 22 / 17 / 15 / 13 (in `tokens.css`).

## Spacing, shape, depth
- 4 px base: 4, 8, 12, 16, 24, 32, 48, 64 (`--sp-1`..`--sp-8`). Screen inset and panel gaps are 16 (12 at 1366).
- Radius 4 px on panels and buttons, 2 px on chips and cells. Nothing pill-shaped except the link status dot.
- Panels: `--c-panel` + `backdrop-filter: var(--blur-panel)` + 1px `--c-line-strong` frame + `--shadow-panel`. The blur is functional (keeps text legible over a moving globe), not decoration. Fallback `--c-panel-solid` when blur is unsupported.
- Panel label row: 15px uppercase label left, data-source tag right (see legend). No eyebrow text above headings, no section numbers.

## CSS tokens
`docs/design/tokens.css` is the source of truth. Copy it to `frontend/src/styles/tokens.css` unchanged. Core of it:

```css
:root {
  --c-space:#05080d; --c-panel:rgba(9,14,22,.86); --c-panel-solid:#0b111a;
  --c-raise:#121b27; --c-line:#1e2a3a; --c-line-strong:#2c3b50;
  --c-ink:#e9eff7; --c-ink-2:#a3b1c4; --c-ink-3:#74839a;
  --c-alert:#ffb224; --c-verified:#35d49a; --c-refused:#ff5468;
  --c-band:rgba(163,177,196,.22); --c-truth:#fff;
  --font-ui:"Barlow"; --font-cond:"Barlow Semi Condensed"; --font-data:"JetBrains Mono";
  --fs-hero:48px; --fs-2m:30px; --fs-title:22px; --fs-body:18px; --fs-data:17px; --fs-label:15px;
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:24px; --sp-6:32px; --sp-7:48px; --sp-8:64px;
  --inset:16px; --gap:16px; --top-h:64px; --rc-w:440px; --strip-h:168px;
  --z-globe:0; --z-labels:10; --z-hud:20; --z-top:30; --z-overlay:40; --z-fd:50; --z-banner:60;
}
```

## The permanent "Real / Simulated" legend (Radial track rule)
- Always visible, in every demo state, at both resolutions. It is never covered by the overlay, never collapsed, never animated.
- Position: bottom-left of the globe stage, 16 px above the lowest left panel (exact boxes in LAYOUT.md).
- Content, two rows, 15px:
  - solid-line swatch **REAL**: orbits from public elements, Sun geometry, ground stations, contact windows
  - hatched swatch **SIMULATED**: power, battery, thermal, faults
- The encoding carries through the whole screen: every panel's label row ends with a small `REAL` (solid) or `SIM` (hatched) tag. Triage, hypotheses, matrix and SOC chart are `SIM`. The trace is tagged with the run mode instead: `REPLAY · recorded run` or `LIVE · Gemini` (also in the top bar). We never let replay pass as live.

## The globe is the hero
- The Cesium canvas is full-bleed (1920x1080) at z 0. All HUD sits on top of it.
- **HUD panels never cover more than 40% of the globe canvas, in any demo state.** LAYOUT.md has the area budget per state; the worst case is 39.9%. If a panel grows, another one must collapse in the same state.
- The Earth's disc is kept inside the free area with `camera.frustum.xOffset/yOffset` (Cesium `PerspectiveFrustum`), so the planet centers in the free region instead of behind a panel.
- The alerting satellite and its shadow crossing are never under a panel. On every focus change, the camera frames the focused sat inside the free area.
- Only satellites that matter get labels (alerting, focused). The other 22 are dots whose opacity follows `illum`.

## Numbers only come from the backend
- The UI formats numbers (%, h:mm:ss, hash prefix). It may count states it received (sats in eclipse) and subtract two backend timestamps for a countdown (`next_contact_aos - fleet_state.t`).
- It never computes SOC, margins, pass/fail, rank, posteriors, time-to-critical or the "best" plan. If the backend did not send a value, the cell shows an em dash in `--c-ink-3`, never a guess.
- No number ever tweens, counts up or rolls. It is either the backend's value or absent.

## No chat
- The agent's narration (`callout.text`) is one caption line at the top of the trace strip, like broadcast subtitles. A new callout replaces the old one; history lives in the trace as rows.
- The trace is a log: time, stage tag, tool name, args, result summary. No avatars, no bubbles, no "Assistant:" labels, no typing indicator.

## Copy (UI strings, uppercase where listed)
- Stages: `AI PROPOSES` -> `PHYSICS VERIFIES` -> `HUMAN APPROVES`.
- Triage status: queued `QUEUED`, working `AI WORKING`, auto_executed `AUTO-EXECUTED · FR-n`, awaiting_approval `NEEDS APPROVAL`, executed `EXECUTED`, safe_mode_recommended `SAFE MODE RECOMMENDED`.
- Matrix cell: `PASS` / `FAILS` + `min_soc_p5` as %, implausible hypotheses `RULED OUT` (hatched, not counted).
- Refusal: `REFUSED · physics veto` / `Fails under: <labels[H]>` / `No override exists.`
- Infeasible: `INFEASIBLE · no plan survives every plausible cause` / `Recommend SAFE_MODE`.
- Connection lost: badge `RECONNECTING · showing last state` (T35).

## Figma
- File: _filled in below after the capture runs._
