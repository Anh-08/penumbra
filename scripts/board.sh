#!/usr/bin/env bash
# AGENT BOARD reader. Uses no model quota: plain GitHub API polling.
#   scripts/board.sh <CODE> --once            # agents: run at every task boundary
#   scripts/board.sh <CODE> [--interval 90]   # humans: leave running in a terminal for notifications
# Needs: gh (logged in as this machine's owner) and jq. Run from inside the repo.
# Env: BOARD_ISSUE=<number of the pinned AGENT BOARD issue>
set -euo pipefail

CODE="${1:?usage: board.sh A1|A2|B1|B2 [--once] [--interval SEC]}"; shift || true
ONCE=0; INTERVAL=90
while [ $# -gt 0 ]; do
  case "$1" in
    --once) ONCE=1 ;;
    --interval) INTERVAL="${2:?--interval needs seconds}"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done
: "${BOARD_ISSUE:?set BOARD_ISSUE to the AGENT BOARD issue number}"
REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"

# Separate cursors for the agent and the human watcher, so the watcher never "consumes" the agent's unread messages.
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/penumbra-board"
mkdir -p "$STATE_DIR"
if [ "$ONCE" = 1 ]; then STATE="$STATE_DIR/$CODE.agent.since"; else STATE="$STATE_DIR/$CODE.human.since"; fi

check() {
  local since now
  since="$(cat "$STATE" 2>/dev/null || echo 1970-01-01T00:00:00Z)"
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  gh api --paginate "repos/$REPO/issues/$BOARD_ISSUE/comments?since=$since&per_page=100" \
    | jq -r --arg code "$CODE" '
        .[]
        | . as $c
        | ([($c.body | split("\n"))[] | select(startswith("TO:"))][0] // "") as $to
        | select($to | test("\\b(" + $code + "|ALL)\\b"))
        | "---- \($c.created_at) @\($c.user.login) \($c.html_url)\n\($c.body)"'
  echo "$now" > "$STATE"
}

if [ "$ONCE" = 1 ]; then
  out="$(check)"
  if [ -n "$out" ]; then printf '%s\n' "$out"; else echo "board: nothing new for $CODE"; fi
  exit 0
fi

echo "watching AGENT BOARD #$BOARD_ISSUE on $REPO for $CODE every ${INTERVAL}s (Ctrl-C to stop)"
while true; do
  out="$(check || true)"
  if [ -n "$out" ]; then
    printf '\a%s\n' "$out"
    if command -v notify-send >/dev/null 2>&1; then notify-send "AGENT BOARD -> $CODE" "new message"; fi
    if command -v osascript >/dev/null 2>&1; then osascript -e "display notification \"new message\" with title \"AGENT BOARD -> $CODE\""; fi
  fi
  sleep "$INTERVAL"
done
