"""Narration rule (CONTRACTS.md): digits in agent free text are allowed only inside
placeholders the backend renders, or inside known ids. Everything else is rejected."""
from __future__ import annotations

import re

# {<sim_id | plan_id | approval_id | alert_id>.<field>}; the field may be a dotted path.
PLACEHOLDER_RE = re.compile(r"\{(?:S-\d+|P-\d+|AP-\d+|AL-\d+)\.[A-Za-z_][A-Za-z0-9_.]*\}")
ALLOWED_ID_RE = re.compile(r"\b(?:SAT-\d{2}|[PS]-\d+|AP-\d+|AL-\d+|FR-\d+|H\d|T\d{2})\b")
DIGIT_RE = re.compile(r"\d+")
MAX_NARRATION_RETRIES = 2


def bare_digits(text: str) -> list[str]:
    """Digit runs that are outside any placeholder or allowed id. Empty list = text is OK."""
    stripped = PLACEHOLDER_RE.sub(" ", text)
    stripped = ALLOWED_ID_RE.sub(" ", stripped)
    return DIGIT_RE.findall(stripped)


def narration_ok(text: str) -> bool:
    return not bare_digits(text)


def placeholders(text: str) -> list[str]:
    """Placeholders in text, without braces, e.g. ["S-3.worst_margin_soc"]."""
    return [m.group(0)[1:-1] for m in PLACEHOLDER_RE.finditer(text)]
