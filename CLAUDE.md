# Penumbra: rules for every agent session
<!-- CLAUDE.md and AGENTS.md must stay identical. Only B1 edits them, after A and B agree. -->

Penumbra is verified AI mission ops for a 24-satellite LEO fleet. **AI proposes, physics verifies, humans approve.**
Event: UB Hacking 2026, hacking starts Sat 13:00. All times are US Eastern. Feature freeze **Sun 06:00**. Submit **Sun 12:00** (hard deadline 12:30).
Scope is fixed by ISSUES.md. Do not build anything that is not in an approved issue.

## 1. Sessions and ownership
The human tells you your code when the session starts ("You are A1"). If you were not told, ask before doing anything.

| Code | Human | Tool | Role | Paths you may edit |
|---|---|---|---|---|
| B1 | B | Claude Code | Integrator + physics core | `.claude/`, `CONTRACTS.md`, `contracts/`, `backend/eps/`, `backend/verify/`, `backend/fleet/`, `backend/__init__.py`, `backend/conftest.py`, `backend/requirements.txt`, `pyproject.toml`, `.github/`, `scripts/`, `docs/`, `runs/`, `CLAUDE.md`, `AGENTS.md`, `ISSUES.md`, `README.md`, `.gitignore` |
| B2 | B | Codex | Orbit, recorder, scheduler, benchmarks, e2e | `backend/orbit/`, `backend/recorder/`, `backend/sched/`, `bench/`, `data/`, `tests/` |
| A1 | A | Claude Code | Agent + safety layer | `backend/agent/` |
| A2 | A | Codex | API + frontend + deploy | `backend/api/`, `frontend/`, `deploy/` |

Humans: A = @Nguyen-Le-Tuan, B = @Anh-08 (equal rights). Every session may also edit its own `progress/<CODE>.md`. Nothing else.
If you need a change in someone else's path, including a new dependency in `backend/requirements.txt`, post a REQUEST to the owner on the board. Never edit it yourself. CI `path-guard` rejects any PR that touches paths outside the branch owner's list.
Unit tests live next to the code you own (e.g. `backend/eps/tests/`). `tests/` is for e2e only (B2).

## 2. Hard rules
- Work only on issues labeled `approved` whose title starts with your code, e.g. `[A1] T10 ...`. Never work on `hold`, `proposed` or `needs-decision` issues.
- An issue is unblocked only when every issue in its "Blocked by" line is closed.
- One issue = one branch = one PR. Branch: `<CODE>/T<nn>-<slug>` (e.g. `B2/T03-penumbra`). PR title `[<CODE>] T<nn> <title>`; PR body contains `Closes #<issue>`.
- Never push to `main`. Only B1 merges.
- Commit small, push often. Commit message: `T<nn>: <what changed>`.
- Contracts (`CONTRACTS.md`, `contracts/`) are frozen. They change only through a B1 PR labeled `contract-change` that touches contract files only, and a human other than the PR author must approve it (CODEOWNERS; in practice A, since B1's PRs are opened from B's account).
- Secrets come only from env (`GEMINI_API_KEY`, `GEMINI_MODEL`, `ELEVENLABS_API_KEY`). Never print, commit or post them.
- No network in tests. Never call CelesTrak from code; read `data/omm/*.json`, which a human fetched once. Tests must pass with `PENUMBRA_OFFLINE=1`.
- Time is UTC and timezone-aware; arrays use POSIX seconds. SI units unless the field name says otherwise (`_deg`, `_c`, `_wh`, `_ms`).
- Satellites are always `SAT-01`..`SAT-24`. Never show real operator or satellite names.
- After the feature freeze (Sun 06:00), only PRs labeled `fix`.

## 3. Task loop
1. Run `scripts/board.sh <CODE> --once` and act on new messages addressed to you.
2. Pick the lowest-numbered approved, unblocked issue for your code. Comment `CLAIM <CODE>` on it and add the label `in-progress`.
3. Read only: the issue, `CONTRACTS.md`, your own paths, `progress/<CODE>.md` and recent `git log`. Do not read the whole repo.
4. Turn the acceptance criteria into tests first. Implement. Run the tests locally.
5. Open the PR. Post DONE (or HANDOFF) on the board to the sessions whose issues are blocked by yours.
6. Update `progress/<CODE>.md` with done / doing / next / blockers. Go back to step 1.

To resume after an interruption: read the issue, `progress/<CODE>.md` and `git log`, then continue.

## 4. Stop-and-wait conditions
In these cases, post NEEDS-DECISION on the board (TO your human), add the label `needs-decision`, then move to another approved issue:
- the change would go outside the issue's acceptance criteria or your paths;
- a contract or schema change seems necessary;
- CI is still red after 2 fix attempts, or one step failed twice;
- information is missing and you cannot infer it.

If you hit your usage limit: post BLOCKED with `REASON: quota` and stop (see §6).

## 5. Board protocol
The board is the pinned issue **AGENT BOARD**. Post one message per comment, in exactly this format:
```
FROM: <CODE>
TO: <CODE>[, <CODE>] | ALL   @<github-user-of-the-recipient's-human>
TYPE: HANDOFF | QUESTION | REQUEST | BLOCKED | DONE | NEEDS-DECISION | PAUSE | RESUME | DIGEST
TASK: #<issue>
PR/BRANCH: #<pr> / <branch>
SUMMARY: 1-3 lines
NEED: what the recipient must do next, or the question
```
- Act only on messages whose TO line contains your code or ALL.
- On a PAUSE addressed to you: finish the current commit, push, and stop until you get a RESUME.
- Read only from the latest DIGEST onward.
- Never post secrets.

## 6. Quota rules
- Keep context small (§3 step 3). Never poll in a loop inside the model; check the board only at task boundaries, with `--once`.
- Use one focused issue per context. If the context has grown large, start a fresh session after the issue is merged.
- Backup owners, used when the owner hit its usage limit or its machine has been off for more than 45 min, and a human has posted a HANDOFF to confirm: A1 → B1 · A2 ↔ B2 · B1 integration → humans merge by hand.
- A backup works on a branch with the owner's prefix (e.g. `A1/T17-...`) and writes `Takeover-by: <CODE>` in the PR body.

## 7. Integrator (B1 only)
- Squash-merge a PR when: CI is green (path-guard, backend, frontend), the PR closes an approved issue, and the change matches the acceptance criteria. Review the diff, not the repo.
- PRs that touch contracts wait for a CODEOWNERS approval from the other human.
- After each merge, post DONE to the sessions blocked by it.
- Post a DIGEST about every 3 h, and a `MORNING DIGEST` at 06:00. Include: merged PRs, open PRs, pending decisions, red tests, quota status per session.
- While human B is asleep: merge only green PRs that touch no contract files and nothing in `.github/`.

## 8. Required physics and safety tests (owners keep them green)
- Eclipse (B2): circular 500 km orbit at beta 0 → geometric shadow 35.8 ± 1 min per orbit; illumination is in [0,1]; penumbra intervals have nonzero duration.
- EPS (B1):
  - SOC stays in [0,1].
  - Energy balance over one orbit at constant load closes within 1 %.
  - The same seed gives identical telemetry.
  - Essential loads are never off.
- Verifier (B1):
  - A plan that drains the battery under any plausible hypothesis is never ranked feasible.
  - The plan hash changes when any command changes.
  - If no plan is feasible, the result is `infeasible` with a SAFE_MODE recommendation.
- Agent (A1): each of these is rejected:
  - a command that is not on the allowlist;
  - LOAD_OFF on an essential load;
  - an uplink outside a contact window;
  - an approval with a stale hash;
  - narration with a bare digit outside an allowed token.
