"""Shared trace model helpers.

The trace is intentionally plain JSON so any collector can produce it and any
renderer can consume it. Timestamps are stored as ISO-8601 strings; duration
values are milliseconds unless the field name says otherwise.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

SCHEMA_VERSION = "session-story/v1"
LOCAL_TZ = _dt.datetime.now().astimezone().tzinfo


def parse_ts(value: Any) -> _dt.datetime | None:
    if not value:
        return None
    if isinstance(value, _dt.datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = _dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        # Agent Teams emits naive local timestamps (Asia/Shanghai on this host).
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(_dt.timezone.utc)


def iso_ts(value: Any) -> str:
    parsed = parse_ts(value)
    if parsed is None:
        return ""
    return parsed.astimezone(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ms_between(start: Any, end: Any) -> int | None:
    a, b = parse_ts(start), parse_ts(end)
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() * 1000)


def seconds_between(start: Any, end: Any) -> float | None:
    ms = ms_between(start, end)
    return None if ms is None else round(ms / 1000.0, 3)


def excerpt(value: Any, limit: int = 280) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+"),
    re.compile(r"(?i)((?:token|cookie|password|secret|api[_-]?key)\s*[:=]\s*)[^\s,;\"']+"),
]


def redact(value: Any) -> str:
    text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + "[REDACTED]", text)
    return text


def new_trace(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso_ts(_dt.datetime.now(_dt.timezone.utc)),
        "target": target,
        "window": {"start": "", "end": "", "duration_ms": None},
        "rounds": [],
        "tasks": [],
        "events": [],
        "topology": [],
        "artifacts": [],
        "side_effects": [],
        "metrics": {},
        "findings": [],
        "provenance": [],
        "gaps": [],
        "raw_evidence": {},
    }


def add_provenance(trace: dict[str, Any], source: str, detail: str, count: int = 1) -> None:
    trace.setdefault("provenance", []).append({
        "source": source,
        "detail": detail,
        "count": count,
    })


def add_event(
    trace: dict[str, Any],
    *,
    ts: Any,
    end_ts: Any = None,
    task_id: str = "",
    round_id: str = "",
    actor: str = "",
    kind: str = "platform",
    title: str = "",
    detail: str = "",
    status: str = "",
    source: str = "unknown",
    confidence: str = "exact",
    evidence_refs: list[str] | None = None,
    sandbox: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "id": f"evt-{len(trace.get('events', [])) + 1:04d}",
        "ts": iso_ts(ts),
        "end_ts": iso_ts(end_ts) if end_ts else "",
        "duration_ms": ms_between(ts, end_ts) if end_ts else None,
        "task_id": task_id or "",
        "round_id": round_id or "",
        "actor": actor or "runtime",
        "kind": kind,
        "title": title or kind,
        "detail": redact(detail),
        "status": status or "",
        "source": source,
        "confidence": confidence,
        "evidence_refs": evidence_refs or [],
    }
    if sandbox:
        event["sandbox"] = sandbox
    trace.setdefault("events", []).append(event)
    return event


def redact_deep(value: Any) -> Any:
    """Recursively redact strings while preserving the evidence structure.

    Raw evidence is embedded in the HTML and exported as JSON, so every string
    leaf must go through the same redaction rules as normalized event details.
    Keys are preserved because collectors use stable field names; values are the
    only place credentials can appear.
    """

    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_deep(item) for item in value]
    if isinstance(value, tuple):
        return [redact_deep(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_deep(item) for key, item in value.items()}
    return value
