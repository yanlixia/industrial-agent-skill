#!/usr/bin/env python3
"""Submit a general performance finding to industrial-agent-skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from storylib.model import redact_deep


DEFAULT_REPO = "yanlixia/industrial-agent-skill"
SCHEMA_VERSION = "session-storyboard-feedback/1"
ALLOWED_AREAS = {"scaffold", "contract", "reference", "test", "tooling"}


class FeedbackError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeedbackError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FeedbackError(f"top-level JSON must be an object: {path}")
    return value


def _text(candidate: dict[str, Any], key: str, *, minimum: int = 8) -> str:
    value = str(candidate.get(key) or "").strip()
    if len(value) < minimum:
        raise FeedbackError(f"{key} must contain at least {minimum} characters")
    if len(value) > 2000:
        raise FeedbackError(f"{key} must be at most 2000 characters")
    return value


def validate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    if candidate.get("schema_version") != SCHEMA_VERSION:
        raise FeedbackError(f"schema_version must be {SCHEMA_VERSION}")
    if candidate.get("scope") != "general":
        raise FeedbackError("scope must be general; session-specific findings are not feedback candidates")

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "scope": "general",
        "title": _text(candidate, "title", minimum=8)[:120],
        "observation": _text(candidate, "observation"),
        "mechanism": _text(candidate, "mechanism"),
        "recommendation": _text(candidate, "recommendation"),
        "validation": _text(candidate, "validation"),
    }

    areas = candidate.get("affected_areas")
    if not isinstance(areas, list) or not areas:
        raise FeedbackError("affected_areas must be a non-empty array")
    normalized["affected_areas"] = []
    for area in areas:
        value = str(area or "").strip()
        if value not in ALLOWED_AREAS:
            raise FeedbackError("affected_areas entries must be one of: " + ", ".join(sorted(ALLOWED_AREAS)))
        if value not in normalized["affected_areas"]:
            normalized["affected_areas"].append(value)

    evidence = candidate.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise FeedbackError("evidence must be a non-empty array")
    normalized_evidence = []
    for item in evidence:
        if not isinstance(item, dict):
            raise FeedbackError("each evidence item must be an object")
        reference = str(item.get("ref") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if not reference or len(summary) < 8:
            raise FeedbackError("each evidence item needs ref and a summary of at least 8 characters")
        normalized_evidence.append({"ref": reference[:300], "summary": summary[:1000]})
    normalized["evidence"] = normalized_evidence
    return redact_deep(normalized)


def feedback_fingerprint(candidate: dict[str, Any]) -> str:
    normalized = validate_candidate(candidate)
    identity = {
        key: normalized[key]
        for key in ("title", "observation", "mechanism", "recommendation", "affected_areas", "validation")
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_issue_body(candidate: dict[str, Any], trace: dict[str, Any]) -> str:
    normalized = validate_candidate(candidate)
    fingerprint = feedback_fingerprint(normalized)
    target = trace.get("target") or {}
    metrics = trace.get("metrics") or {}
    selected_metrics = {
        key: metrics.get(key)
        for key in (
            "wall_seconds",
            "round_sum_seconds",
            "max_round_seconds",
            "member_task_count",
            "exploration_calls",
            "artifact_count",
            "duplicate_effects",
        )
        if key in metrics
    }
    evidence_lines = "\n".join(
        f"- `{item['ref']}`: {item['summary']}" for item in normalized["evidence"]
    )
    areas = ", ".join(normalized["affected_areas"])
    return f"""## Observation

{normalized['observation']}

## Evidence

{evidence_lines}

## Mechanism

{normalized['mechanism']}

## Proposed industrial-agent-builder change

{normalized['recommendation']}

Affected areas: {areas}

## Validation

{normalized['validation']}

## Source

- Trace schema: `{trace.get('schema_version', 'unknown')}`
- Session: `{target.get('id', 'unknown')}`
- Target: `{target.get('kind', 'unknown')}` / `{target.get('title', 'unknown')}`
- Metrics: `{json.dumps(selected_metrics, ensure_ascii=False, sort_keys=True)}`

<!-- session-storyboard-feedback-id:{fingerprint} -->
"""


def _run(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
    except OSError as exc:
        raise FeedbackError(f"cannot run {command[0]}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise FeedbackError(f"{command[0]} failed ({completed.returncode}): {detail}")
    return completed.stdout.strip()


def find_existing(gh: str, repo: str, fingerprint: str) -> str:
    output = _run([
        gh, "issue", "list", "--repo", repo, "--state", "all",
        "--search", fingerprint, "--json", "number,url,body", "--limit", "20",
    ])
    try:
        issues = json.loads(output)
    except json.JSONDecodeError as exc:
        raise FeedbackError(f"gh returned invalid JSON: {output[:500]}") from exc
    marker = f"session-storyboard-feedback-id:{fingerprint}"
    for issue in issues if isinstance(issues, list) else []:
        if marker in str(issue.get("body") or ""):
            return str(issue.get("url") or "")
    return ""


def create_issue(gh: str, repo: str, title: str, body: str) -> str:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(body)
        body_path = handle.name
    try:
        return _run([
            gh, "issue", "create", "--repo", repo,
            "--title", title, "--body-file", body_path,
        ])
    finally:
        Path(body_path).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an idempotent industrial-agent-skill feedback issue.")
    parser.add_argument("--candidate", required=True, help="candidate JSON path")
    parser.add_argument("--trace", required=True, help="session-trace.json path")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--gh", default="gh")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--body-out", default="")
    args = parser.parse_args(argv)

    try:
        candidate = validate_candidate(_read_json(Path(args.candidate).expanduser()))
        trace = _read_json(Path(args.trace).expanduser())
        body = render_issue_body(candidate, trace)
        if args.body_out:
            output = Path(args.body_out).expanduser()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(body, encoding="utf-8")
        if args.dry_run:
            print(body)
            return 0

        fingerprint = feedback_fingerprint(candidate)
        existing = find_existing(args.gh, args.repo, fingerprint)
        if existing:
            print(f"existing: {existing}")
            return 0
        url = create_issue(args.gh, args.repo, f"[storyboard] {candidate['title']}", body)
        print(f"created: {url}")
        return 0
    except FeedbackError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
