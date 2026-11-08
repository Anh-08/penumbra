"""Narration filter and placeholder renderer (CONTRACTS.md "Narration rule").

The model never writes numbers. It writes placeholders like {S-3.worst_margin_soc}, and the backend fills them
in from its own results. On top of the contract's digit rule, the filter also rejects ids that do not exist
(so "P-80 percent" cannot smuggle a number), placeholders that do not resolve to a single value, and any other
{...} text.

`normalize` first unwraps braces around a bare token ({P-2} -> P-2, {H2} -> H2, {SVALBARD} -> SVALBARD), a habit
of Gemini's; the unwrapped text still goes through every check. Besides the stored fields, a few derived fields
are available (DERIVED_FIELDS), e.g. {P-2.n_commands} and {AL-1.time_to_critical_min}.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel

from contracts import ALLOWED_ID_RE, HYPOTHESES, PLACEHOLDER_RE, SAT_IDS, bare_digits

_BRACES_RE = re.compile(r"\{[^{}]*\}|[{}]")
_BARE_TOKEN_RE = re.compile(r"\{\s*([A-Za-z][A-Za-z0-9_-]*)\s*\}")  # {P-2}, {H2}, {SVALBARD}: no field
_TIME_FIELDS = {"t", "ts", "aos", "los", "t_exec", "start_t", "end_t", "onset_t", "next_contact_aos", "deadline_t"}
_FRACTION_FIELDS = {"pass_rate", "payload_uptime", "payload_uptime_worst", "duty", "posterior"}


class Registry(Protocol):
    def lookup(self, obj_id: str) -> BaseModel | None:
        """The plan (P-), SimResult (S-), approval (AP-) or alert (AL-) with this id, or None."""
        ...


class RenderError(ValueError):
    pass


# Derived fields per id prefix: (field name -> function of the stored object).
DERIVED_FIELDS: dict[str, dict[str, Any]] = {
    "P-": {"n_commands": lambda p: len(p.commands), "station_id": lambda p: p.uplink.station_id},
    "AL-": {"time_to_critical_min": lambda a: (None if a.time_to_critical_s is None
                                                else int(round(a.time_to_critical_s / 60.0)))},
}

# What the prompt and the re-ask message list as usable placeholders.
USABLE_FIELDS = {
    "S-n": ["worst_margin_soc", "payload_uptime_worst", "n_commands", "passes_all"],
    "P-n": ["n_commands", "station_id", "sat_id", "kind"],
    "AP-n": ["tier", "plan_id", "sat_id"],
    "AL-n": ["sat_id", "severity", "reason_key", "time_to_critical_min"],
}


def normalize(text: str) -> str:
    """Unwrap braces around a bare token: {P-2} -> P-2. Real placeholders ({P-2.n_commands}) are untouched."""
    return _BARE_TOKEN_RE.sub(lambda m: m.group(1), text)


def narration_problems(text: str, registry: Registry) -> list[str]:
    """Everything wrong with `text` under the narration rule. Empty list = OK to show (after render)."""
    problems = []
    digits = bare_digits(text)
    if digits:
        problems.append(f"bare digits: {digits}")
    without = PLACEHOLDER_RE.sub(" ", text)
    for m in _BRACES_RE.finditer(without):
        problems.append(f"unsupported placeholder {m.group(0)}")
    for token in dict.fromkeys(m.group(0) for m in ALLOWED_ID_RE.finditer(without)):
        if not _id_exists(token, registry):
            problems.append(f"unknown id: {token}")
    for m in PLACEHOLDER_RE.finditer(text):
        try:
            _resolve(m.group(0)[1:-1], registry)
        except RenderError as exc:
            problems.append(f"placeholder {m.group(0)}: {exc}")
    return problems


def render(text: str, registry: Registry) -> str:
    """Fill every placeholder with the backend value. Raises RenderError if one does not resolve."""
    return PLACEHOLDER_RE.sub(lambda m: format_value(*_resolve(m.group(0)[1:-1], registry)), text)


def safe_text(text: str | None, registry: Registry) -> str | None:
    """Rendered text if it passes the narration rule, else None (the UI then shows tool results only)."""
    if not text or narration_problems(text, registry):
        return None
    return render(text, registry)


def _id_exists(token: str, registry: Registry) -> bool:
    if token.startswith("SAT-"):
        return token in SAT_IDS
    if token.startswith(("FR-", "T")):
        return True  # flight rules and task ids are not tracked by the agent
    if re.fullmatch(r"H\d", token):
        return token in HYPOTHESES
    return registry.lookup(token) is not None


def _resolve(ref: str, registry: Registry) -> tuple[str, Any]:
    obj_id, _, path = ref.partition(".")
    obj = registry.lookup(obj_id)
    if obj is None:
        raise RenderError(f"unknown object {obj_id}")
    derived = next((d for prefix, d in DERIVED_FIELDS.items() if obj_id.startswith(prefix)), {})
    if path in derived:
        return path, derived[path](obj)
    value: Any = obj.model_dump(mode="python")
    field = obj_id
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise RenderError(f"no field {path!r}")
        if not part.isdigit():
            field = part
    if isinstance(value, dict):
        raise RenderError(f"{path!r} is not a single value")
    return field, value


def format_value(field: str, value: Any) -> str:
    """Human formatting chosen by field name (SI units unless the name says otherwise)."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "n/a"
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ", ".join(format_value(field, v) for v in value)
    if not isinstance(value, (int, float)):
        raise RenderError(f"cannot format {field}")
    if field in _TIME_FIELDS:
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%H:%M UTC")
    if "margin" in field:
        return f"{value * 100:+.1f} % SOC"
    if field.endswith("_ms"):
        return f"{value:.0f} ms"
    if field.endswith("_s"):
        return _duration(value)
    if field.endswith("_c"):
        return f"{value:.1f} °C"
    if field.endswith("_deg"):
        return f"{value:.0f}°"
    if "v_bus" in field:
        return f"{value:.2f} V"
    if "soc" in field or field in _FRACTION_FIELDS:
        return f"{value * 100:.1f} %"
    if field.endswith("_min"):
        return f"{value:.0f} min"
    if isinstance(value, int):
        return str(value)
    return f"{value:.3g}"


def _duration(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    h, m = divmod(s // 60, 60)
    return f"{h} h {m} min" if h else f"{m} min"
