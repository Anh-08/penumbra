#!/usr/bin/env python3
"""Seed GitHub labels, the pinned AGENT BOARD issue, and backlog issues from ISSUES.md.

A human runs this once, from the repo root, with gh logged in:
  python3 scripts/seed_issues.py --dry-run   # print what would happen
  python3 scripts/seed_issues.py             # create everything as `proposed`
  python3 scripts/seed_issues.py --approve   # same, but issues not marked "proposed" get `approved`
"""
import re
import subprocess
import sys

DRY = "--dry-run" in sys.argv
APPROVE = "--approve" in sys.argv

LABELS = {
    "proposed": "cccccc", "approved": "0e8a16", "blocked": "b60205",
    "needs-decision": "d93f0b", "hold": "000000", "in-progress": "1d76db",
    "contract-change": "5319e7", "fix": "fbca04", "stretch": "c5def5",
    "owner:A1": "f9d0c4", "owner:A2": "f9d0c4", "owner:B1": "bfdadc", "owner:B2": "bfdadc",
}

BOARD_BODY = (
    "Pinned board for agent handoffs. Message format and rules: CLAUDE.md / AGENTS.md section 5.\n"
    "Humans: pause or resume any session with a message whose TO line is <CODE> or ALL."
)


def gh(*args: str) -> str:
    if DRY:
        print("gh", *[repr(a) if (" " in a or "\n" in a) else a for a in args][:6], "...")
        return "https://github.com/DRY/RUN/issues/0"
    out = subprocess.run(["gh", *args], check=True, capture_output=True, text=True)
    return out.stdout.strip()


def issue_number(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def main() -> None:
    for name, color in LABELS.items():
        gh("label", "create", name, "--color", color, "--force")

    board_no = issue_number(gh("issue", "create", "--title", "AGENT BOARD", "--body", BOARD_BODY))
    gh("issue", "pin", board_no)

    text = open("ISSUES.md", encoding="utf-8").read()
    numbers = {}
    for block in re.split(r"(?m)^### ", text)[1:]:
        head, _, body = block.partition("\n")
        m = re.match(r"(T\d{2})\s+\[(A1|A2|B1|B2)\]\s+(.+)", head.strip())
        if not m:
            continue
        tid, owner, title = m.groups()
        low = title.lower()
        stretch = "(stretch" in low
        proposed_only = "proposed)" in low

        lines = []
        for line in body.strip().splitlines():
            if line.startswith("- Blocked by:"):
                line = re.sub(r"T\d{2}", lambda mm: f"#{numbers[mm.group(0)]}" if mm.group(0) in numbers else mm.group(0), line)
            lines.append(line)

        labels = [f"owner:{owner}"]
        if stretch:
            labels.append("stretch")
        labels.append("approved" if APPROVE and not proposed_only else "proposed")

        url = gh("issue", "create", "--title", f"[{owner}] {tid} {title}",
                 "--body", "\n".join(lines), "--label", ",".join(labels))
        numbers[tid] = tid[1:] if DRY else issue_number(url)

    print(f"\nexport BOARD_ISSUE={board_no}")
    for t, n in numbers.items():
        print(f"{t} -> #{n}")


if __name__ == "__main__":
    main()
