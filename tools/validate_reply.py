#!/usr/bin/env python3
"""Deterministic acceptance gate for LLM-drafted user replies.

Validates a draft reply against the structured reply_intent emitted by the
turn state machine. Learned the hard way (device-ops DW-OBS-143, 2026-10-27):

- frozen strings (marker, option labels, gate phrases) must appear verbatim;
- fact tokens (numbers, ids) must appear verbatim;
- EVERY invisible continuation marker the orchestrator emits MUST be exempt
  from the forbidden-pattern scan. The uuid ban and the "copy markers
  verbatim" rule contradict each other unless exemptions are declared here.
  A missing exemption forces the LLM to delete the marker to pass validation,
  which silently severs the cross-turn state chain;
- emit/exempt consistency is machine-checked by
  scripts/check_marker_contract.py in CI, not by convention.

Output: single JSON on stdout; exit 0 ok, 1 violations. The caller renders
the template fallback after one failed correction attempt.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MAX_REPLY_CHARS = 2000

FORBIDDEN_PATTERNS = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "uuid"),
    (re.compile(r"(^|\n)```"), "code-fence"),
    (re.compile(r"(^|\n)\s*(\$|python3? |\.?/bin/)"), "command-line"),
    (re.compile(r"(/tmp/|/workspace/|/var/|/root/|artifacts/)"), "file-path"),
]

FACT_TOKEN_PATTERN = re.compile(
    r"\b(?:FDR|MER)\d{6,}\b|\b\d{1,4}\.\d{1,3}\b|\bEQ\d+\b|\b\d{10,}\b"
)

# Invisible cross-turn continuation markers are machine channels, not leaks:
# the renderer MUST copy them verbatim. When you add a new marker namespace
# here, scripts/check_marker_contract.py picks it up automatically — the only
# rule is: every marker your orchestrator emits must match this pattern.
CONTINUATION_MARKER_PATTERN = re.compile(
    r"<!-- (?:squad_root_issue_id|squad_leader_continuation|mes_state|chain_state):[^>]*?-->",
    re.IGNORECASE,
)


def _fact_tokens(value) -> list[str]:
    """Extract verbatim fact tokens from a facts value or mapping."""
    tokens: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            tokens.extend(_fact_tokens(item))
    elif isinstance(value, list):
        for item in value:
            tokens.extend(_fact_tokens(item))
    elif isinstance(value, (int, float, str)):
        tokens.extend(match.group(0) for match in FACT_TOKEN_PATTERN.finditer(str(value)))
    return tokens


def validate(intent: dict, draft: str) -> dict:
    violations: list[str] = []

    frozen = intent.get("frozen") or {}
    for marker in [frozen.get("marker")] if frozen.get("marker") else []:
        if marker not in draft:
            violations.append(f"frozen marker missing: {marker}")
    for phrase in frozen.get("phrases") or []:
        if phrase not in draft:
            violations.append(f"frozen phrase missing: {phrase}")

    options = frozen.get("options") or []
    for option in options:
        label = str(option.get("label", ""))
        number = str(option.get("id", ""))
        if label and label not in draft:
            violations.append(f"frozen option label missing: {label}")
        if number and label and f"{number}. {label}" not in draft and f"{number}.{label}" not in draft:
            violations.append(f"frozen option numbering mismatch: {number}. {label}")

    for token in _fact_tokens(intent.get("facts")):
        if token not in draft:
            violations.append(f"fact token missing: {token}")

    # Strip the mandated marker(s) before scanning so the verbatim-copy rule
    # and the forbidden-pattern rule cannot contradict (DW-OBS-143).
    scan_text = draft
    marker = frozen.get("marker")
    if isinstance(marker, str) and marker:
        scan_text = scan_text.replace(marker, "")
    scan_text = CONTINUATION_MARKER_PATTERN.sub("", scan_text)

    for pattern, name in FORBIDDEN_PATTERNS:
        if pattern.search(scan_text):
            violations.append(f"forbidden {name} pattern present")

    if len(draft) > MAX_REPLY_CHARS:
        violations.append(f"length exceeds budget: {len(draft)} > {MAX_REPLY_CHARS}")

    return {"ok": not violations, "violations": violations}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate drafted reply against reply_intent")
    parser.add_argument("--intent", required=True, help="path to reply_intent JSON")
    parser.add_argument("--rendered", required=True, help="path to drafted reply markdown")
    args = parser.parse_args(argv)
    try:
        intent = json.loads(Path(args.intent).read_text(encoding="utf-8"))
        draft = Path(args.rendered).read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "violations": [f"input error: {error}"]}, ensure_ascii=False))
        return 1
    result = validate(intent, draft)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
