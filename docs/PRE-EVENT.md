# What existed before the hacking period, and what was added after

UB Hacking 2026 hacking started Sat 2026-11-07 13:00 US Eastern. This file lists everything team Penumbra had before
13:00 and what was added after it, up to the setup commit. All times are US Eastern unless marked UTC.

**All product code is written during the hacking period.**

At the setup commit, nothing exists in `backend/`, `frontend/`, `bench/`, `tests/`, `contracts/` or `deploy/`. No app
scaffolding was generated, and there are no starter modules.

## 1. Before 13:00: local commit `f460a34` (11:56)
A local commit only: no remote, never pushed. It holds planning, tooling, data and config. It holds no product code.
- **Planning docs** (prose and tables, no code; file times 10:42–11:35): `CLAUDE.md` and `AGENTS.md` (identical rules
  for the AI coding sessions), `CONTRACTS.md` (draft interface contracts: names, fields, units), `ISSUES.md` (the backlog).
- **Workflow tooling** (team process, not product features): `scripts/board.sh` (reads the AGENT BOARD issue),
  `scripts/path_guard.py` (CI check that a branch only touches its owner's paths), `scripts/seed_issues.py` (creates
  GitHub labels and issues from `ISSUES.md`), `.github/workflows/ci.yml`, `.github/CODEOWNERS`, `.gitignore`.
- **Data** from a first setup pass that morning: `data/omm/fleet.json` (CelesTrak, fetched 16:49 UTC),
  `data/ephem/de421.bsp` (JPL), `data/stations.json`, `data/README.md`.
- **Config and records**: `.claude/settings.json` (Claude Code permissions), `docs/PRE-EVENT.md`, `docs/pinned-versions.txt`.
- **Outside the repo**: a Python 3.11 venv at `~/.venvs/penumbra` and a warmed npm cache.

## 2. After 13:00, before the setup session: planning-doc edits
Prose only, still no code. They came from the team's planning kit (`penumbra-kit.zip`, kept out of git).
- `CLAUDE.md` / `AGENTS.md` (13:25): one added rule on data transparency (a "Real / Simulated" legend in the UI).
- `ISSUES.md` (13:25): added the H0-DATA human task (NASA PCoE battery data), the mandatory Devpost sections, the
  Real/Simulated legend for T09, and the PCoE-based acceptance criteria for T07 and T30.
- `docs/RUNBOOK.md` (13:58, new): the humans' step-by-step runbook.

## 3. Added by the setup session (2026-11-07, from 14:23)
The working tree's data files and docs from `f460a34` were deleted before this session, and the session re-created them.
- **Data** (details and checksums in `data/README.md`):
  - `data/omm/fleet.json`: downloaded again, once, at 19:26:23 UTC (HTTP 200, 131 objects).
  - `data/ephem/de421.bsp`: downloaded again with Skyfield's loader at 19:26:29 UTC.
  - `data/stations.json`: written again by hand.

  All three files are **byte-identical** to the copies in `f460a34`, because CelesTrak had not published newer
  elements for this group in between. Git therefore records them as unchanged since the pre-event commit, and in
  practice this data existed before 13:00. Only `data/README.md` changed: it now gives the new fetch times.
- **Records**: `docs/pinned-versions.txt` (exact installed Python and npm versions) and this file.
- **Agent config**: `.claude/settings.json` was rewritten to use no absolute home paths, so both laptops can share it.
  Configuration only.
- **Environment, outside the repo**:
  - The venv `~/.venvs/penumbra` was recreated from scratch with uv (Python 3.11.16, matching CI) and smoke-tested with
    one `python -c` import line.
  - The npm cache was warmed with `npm cache add` for cesium, vite, vite-plugin-cesium, react, react-dom,
    @vitejs/plugin-react and gsap, plus their dependencies (90 packages). No npm project was created.
  - The user-level Claude Code skill ui-ux-pro-max (with its companion skills) was installed into `~/.claude/skills/`.
    The Impeccable skill was already installed there before the event.
  - Codex CLI settings (`~/.codex/config.toml`, `~/.codex/rules/default.rules`) were written up for each human to
    apply on their own machine. They are not in the repo.
- **Secrets**: API keys live only in `~/.config/penumbra/.env`, outside the repo. They are never committed or printed.

## Not in the repo
- `penumbra-kit.zip`, the archive the planning files came from (excluded locally via `.git/info/exclude`).
- No remote has been added and nothing has been pushed at the setup commit.
