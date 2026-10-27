#!/usr/bin/env python3
"""Validate a drafted user-visible reply against reply_intent."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

MAX_REPLY_CHARS = 2000

FORBIDDEN_PATTERNS = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "uuid"),
    (re.compile(r"(^|\n)```"), "code-fence"),
    (re.compile(r"(^|\n)\s*(\$|python3? |\.?/bin/)"), "command-line"),
    (re.compile(r"(/tmp/|/workspace/|/var/|/root/|artifacts/)"), "file-path"),
]

FACT_TOKEN_PATTERN = re.compile(
    r"\b(?:[A-Z]{2,10}\d{4,}|[A-Z]{2,10}\d+|\d{1,4}\.\d{1,3}|\d{10,})\b"
)

# Keep this as a literal tuple: check_marker_contract.py reads it with AST.
# A marker listed here is a machine channel, not a user-visible fact. It is
# stripped before the forbidden-content scan, so UUID bans and verbatim marker
# copying cannot contradict each other.
CONTINUATION_MARKER_NAMESPACES = (
    "squad_root_issue_id",
    "squad_leader_continuation",
    "team_root_issue_id",
    "team_role",
    "role_dispatch_key",
    "team_state",
    "expert_root_issue_id",
    "expert_state",
    "chain_state",
)

CONTINUATION_MARKER_PATTERN = re.compile(
    r"<!--\s*(?:%s):[^>]*?-->" % "|".join(CONTINUATION_MARKER_NAMESPACES),
    re.IGNORECASE,
)


def _fact_tokens(value: Any) -> List[str]:
    tokens: List[str] = []
    if isinstance(value, dict):
        for item in value.values():
            tokens.extend(_fact_tokens(item))
    elif isinstance(value, list):
        for item in value:
            tokens.extend(_fact_tokens(item))
    elif isinstance(value, (int, float, str)):
        tokens.extend(match.group(0) for match in FACT_TOKEN_PATTERN.finditer(str(value)))
    return tokens


def validate(intent: Dict[str, Any], draft: str) -> Dict[str, Any]:
    if isinstance(intent.get("reply_intent"), dict):
        intent = intent["reply_intent"]
    violations: List[str] = []

    frozen = intent.get("frozen") or {}
    marker = frozen.get("marker")
    if marker and marker not in draft:
        violations.append("frozen marker missing: %s" % marker)
    for phrase in frozen.get("phrases") or []:
        if phrase not in draft:
            violations.append("frozen phrase missing: %s" % phrase)

    for option in frozen.get("options") or []:
        label = str(option.get("label", ""))
        number = str(option.get("id", ""))
        if label and label not in draft:
            violations.append("frozen option label missing: %s" % label)
        if number and label and "%s. %s" % (number, label) not in draft and "%s.%s" % (number, label) not in draft:
            violations.append("frozen option numbering mismatch: %s. %s" % (number, label))

    for token in _fact_tokens(intent.get("facts")):
        if token not in draft:
            violations.append("fact token missing: %s" % token)

    scan_text = draft
    if isinstance(marker, str) and marker:
        scan_text = scan_text.replace(marker, "")
    scan_text = CONTINUATION_MARKER_PATTERN.sub("", scan_text)

    for pattern, name in FORBIDDEN_PATTERNS:
        if pattern.search(scan_text):
            violations.append("forbidden %s pattern present" % name)

    if len(draft) > MAX_REPLY_CHARS:
        violations.append("length exceeds budget: %s > %s" % (len(draft), MAX_REPLY_CHARS))

    return {"ok": not violations, "violations": violations}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a drafted reply against reply_intent")
    parser.add_argument("--intent", required=True, help="path to reply_intent JSON")
    parser.add_argument("--rendered", required=True, help="path to drafted reply markdown")
    args = parser.parse_args(argv)
    try:
        intent = json.loads(Path(args.intent).read_text(encoding="utf-8"))
        draft = Path(args.rendered).read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "violations": ["input error: %s" % error]}, ensure_ascii=False))
        return 1
    result = validate(intent, draft)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
