# Prepared before the event

UB Hacking 2026 starts Sat 2026-11-07 13:00 US Eastern. This is the complete list of what team Penumbra prepared
before 13:00. Everything here is planning, workflow tooling, data or environment setup.

**All product code is written during the event.**

Nothing exists yet in `backend/`, `frontend/`, `bench/`, `tests/`, `contracts/` or `deploy/`. No app scaffolding was
generated, and there are no starter modules.

## 1. Planning docs (in the repo)
Written by the team on 2026-11-07 before 13:00 (file times 10:42–11:35 ET). They contain prose and tables, no code.
- `CLAUDE.md` and `AGENTS.md`: identical rules for the AI coding sessions (ownership, branches, board protocol).
- `CONTRACTS.md`: draft interface contracts between modules (names, fields, units). A and B approve it at 13:00.
- `ISSUES.md`: the backlog. It is seeded into GitHub issues during the event.
- `docs/PRE-EVENT.md` (this file) and `docs/pinned-versions.txt`.

## 2. Workflow tooling (in the repo)
Team process tools. They are not part of the product and do not implement any product feature.
- `scripts/board.sh`: reads the AGENT BOARD issue through the GitHub API.
- `scripts/path_guard.py`: CI check that each branch only touches its owner's paths.
- `scripts/seed_issues.py`: creates GitHub labels and issues from `ISSUES.md`.
- `.github/workflows/ci.yml` (path guard, backend tests, frontend build; it skips each job until that code exists) and
  `.github/CODEOWNERS`.
- `.gitignore`.

## 3. Data files (in the repo)
Downloaded or written by hand. Details and checksums are in `data/README.md`.
- `data/omm/fleet.json`: CelesTrak GP data for one satellite group. Downloaded once, 2026-11-07 16:49 UTC.
- `data/ephem/de421.bsp`: JPL DE421 ephemeris, downloaded with Skyfield's loader.
- `data/stations.json`: 3 ground stations with approximate coordinates.

## 4. Environment (outside the repo)
- Tool check: python3, node, npm, git, gh (2.46, supports `gh issue pin`), jq, notify-send and claude are
  installed. `gh` is logged in. The `codex` CLI was not installed at commit time.
- Python 3.11 venv at `~/.venvs/penumbra` (3.11 matches CI) with the libraries listed in `docs/pinned-versions.txt`.
  It was smoke-tested with one `python -c` import line, so no files were created.
- npm cache warmed with `npm cache add` for cesium, vite, vite-plugin-cesium, react, react-dom and
  @vitejs/plugin-react, plus their runtime dependencies (86 packages). No npm project was created.
- API keys (Gemini, ElevenLabs) are kept in `~/.config/penumbra/.env`, outside the repo, and are never committed.
  The file had not been created yet when this commit was made.

## 5. Agent configuration
- `.claude/settings.json`: Claude Code permission rules (which commands may run without a prompt, and which are
  denied). Configuration only.
- Codex CLI settings for each laptop's `~/.codex/config.toml` and `~/.codex/rules/default.rules` were written up.
  They are not in the repo, and each human applies them on their own machine.

## Not in this commit
- `penumbra-kit.zip`, the archive the planning files came from. It is excluded locally through `.git/info/exclude`.
- No remote has been added and nothing has been pushed.
