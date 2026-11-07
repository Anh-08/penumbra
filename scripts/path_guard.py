#!/usr/bin/env python3
"""CI hard gate for directory ownership (CLAUDE.md section 1).

Usage: path_guard.py <head_branch> <base_ref>
The branch must start with A1/, A2/, B1/ or B2/ (agent sessions), or human/ (humans; always allowed).
Keep OWN in sync with the table in CLAUDE.md.
"""
import subprocess
import sys

OWN = {
    "B1": [".claude/", "CONTRACTS.md", "contracts/", "backend/eps/", "backend/verify/", "backend/fleet/",
           "backend/__init__.py", "backend/conftest.py", "backend/requirements.txt", "pyproject.toml",
           ".github/", "scripts/", "docs/", "runs/", "CLAUDE.md", "AGENTS.md", "ISSUES.md",
           "README.md", ".gitignore"],
    "B2": ["backend/orbit/", "backend/recorder/", "backend/sched/", "bench/", "data/", "tests/"],
    "A1": ["backend/agent/"],
    "A2": ["backend/api/", "frontend/", "deploy/"],
}


def allowed(path: str, prefixes: list) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in prefixes)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    branch, base = sys.argv[1], sys.argv[2]
    prefix = branch.split("/", 1)[0]
    if prefix == "human":
        print(f"path-guard: human branch '{branch}', skipped")
        return 0
    code = prefix.upper()
    if code not in OWN:
        print(f"path-guard: branch '{branch}' must start with A1/, A2/, B1/, B2/ or human/")
        return 1
    diff = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"],
                          check=True, capture_output=True, text=True).stdout.split()
    prefixes = OWN[code] + [f"progress/{code}.md"]
    bad = [p for p in diff if not allowed(p, prefixes)]
    if bad:
        print(f"path-guard: {code} may not touch:")
        for p in bad:
            print(f"  - {p}")
        print("Post a REQUEST to the owner on the AGENT BOARD instead (CLAUDE.md section 1).")
        return 1
    print(f"path-guard OK: {len(diff)} file(s) within {code} paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
