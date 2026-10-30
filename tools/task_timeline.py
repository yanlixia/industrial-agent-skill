#!/usr/bin/env python3
"""Audit native member task timings from captured /api/issues/{id}/task-runs JSON.

Usage:
  python3 tools/task_timeline.py task-runs.json
  python3 tools/task_timeline.py session-a.json session-b.json --json --strict
  python3 tools/task_timeline.py sessions/*.json --strict \
    --total-budget 180 --min-samples 20

Each input file is treated as one end-to-end session sample. For a native squad,
combine the root and child task-runs arrays into one file per session before
auditing. The tool never calls the platform; it only turns captured responses
into evidence.

Why this exists: "member task is slow" is almost never one problem. The platform
claims queued tasks on a 2s DB scan, so a claim over 10s means the task is being
skipped (runtime binding, SandboxRun lock, squad-issue serialization) rather than
"the platform is busy". This tool turns timestamps into an explicit finding.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CLAIM_BUDGET_SECONDS = 10.0
DEFAULT_START_BUDGET_SECONDS = 60.0
DEFAULT_TOTAL_BUDGET_SECONDS = 180.0
DEFAULT_STALL_SECONDS = 180.0
NEVER_CLAIMED_SECONDS = 60.0
TERMINAL = {"completed", "failed", "cancelled"}
LOCAL_TZ = datetime.now().astimezone().tzinfo


def parse_ts(value):
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # The platform returns naive local timestamps (Asia/Shanghai), not UTC.
        # Assuming UTC made every "now - created" window 8h negative, so an
        # open task could never be flagged as never_claimed.
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed


def seconds_between(start, end):
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 3)


def first_value(payload, *keys):
    """Read the first present key; task-runs uses API-facing timestamp names."""
    for key in keys:
        if payload.get(key) is not None:
            return payload.get(key)
    return None


def percentile(values, quantile=0.95):
    """Nearest-rank percentile; never invents a value between samples."""
    ordered = sorted(float(value) for value in values if value is not None)
    if not ordered:
        return None
    rank = max(1, math.ceil(float(quantile) * len(ordered)))
    return round(ordered[min(rank - 1, len(ordered) - 1)], 3)


def items_of(payload):
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "tasks", "task_runs", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if "id" in payload:
            return [payload]
    raise ValueError("task-runs payload must be a list or contain items/tasks/data")


def analyze_task(task, now, claim_budget, start_budget, stall_seconds, *, source=""):
    created = parse_ts(task.get("created_at"))
    # /api/issues/{id}/task-runs serializes Task.claimed_at as ``dispatched_at``
    # and Task.finished_at as ``completed_at``. Keep the raw names as aliases so
    # captured DB rows and API responses both work.
    claimed = parse_ts(first_value(task, "claimed_at", "dispatched_at"))
    started = parse_ts(task.get("started_at"))
    finished = parse_ts(first_value(task, "finished_at", "completed_at"))
    status = str(task.get("status") or "")

    claim_seconds = seconds_between(created, claimed)
    start_seconds = seconds_between(claimed, started)
    run_seconds = seconds_between(started, finished)
    open_seconds = seconds_between(created, now)

    findings = []
    if claimed is None:
        waiting = open_seconds if open_seconds is not None else 0.0
        if status == "queued" and waiting >= NEVER_CLAIMED_SECONDS:
            findings.append(
                "never_claimed: queued %.0fs; check task.runtime_id, runtime status/mode, "
                "SandboxRun lock and squad-issue serialization" % waiting
            )
    elif claim_seconds is not None and claim_seconds > claim_budget:
        findings.append("claim_slow: %.1fs > %.1fs budget" % (claim_seconds, claim_budget))

    if claimed is not None and started is None and status not in TERMINAL:
        waiting = seconds_between(claimed, now)
        if waiting is not None and waiting > start_budget:
            findings.append("start_stalled: claimed %.0fs ago, never started" % waiting)
    elif start_seconds is not None and start_seconds > start_budget:
        findings.append("start_slow: %.1fs > %.1fs budget" % (start_seconds, start_budget))

    if status not in TERMINAL and open_seconds is not None and open_seconds > stall_seconds:
        findings.append("member_stalled: %.0fs since creation" % open_seconds)
    elif status not in TERMINAL and started is not None and finished is None:
        running = seconds_between(started, now)
        if running is not None:
            findings.append("still_running: %.0fs since start" % running)

    if status == "completed" and any(
        value is None for value in (created, claimed, started, finished)
    ):
        findings.append(
            "timeline_incomplete: completed task is missing created/dispatched/started/completed timestamp"
        )

    return {
        "id": str(task.get("id") or ""),
        "agent_id": str(task.get("agent_id") or ""),
        "runtime_id": str(task.get("runtime_id") or ""),
        "status": status,
        "attempt": task.get("attempt"),
        "is_leader_task": bool(task.get("is_leader_task")),
        "parent_task_id": task.get("parent_task_id"),
        "source_ref": task.get("source_ref"),
        "source": source,
        "created_at": task.get("created_at"),
        "claimed_at": first_value(task, "claimed_at", "dispatched_at"),
        "started_at": task.get("started_at"),
        "finished_at": first_value(task, "finished_at", "completed_at"),
        "claim_seconds": claim_seconds,
        "start_seconds": start_seconds,
        "run_seconds": run_seconds,
        "open_seconds": open_seconds,
        "findings": findings,
    }


def analyze_source(source, tasks, now, claim_budget, start_budget, stall_seconds):
    rows = [
        analyze_task(
            task,
            now,
            claim_budget,
            start_budget,
            stall_seconds,
            source=source,
        )
        for task in tasks
    ]
    starts = [parse_ts(row.get("created_at")) for row in rows]
    starts = [value for value in starts if value is not None]
    ended = [parse_ts(row.get("finished_at")) for row in rows]
    open_tasks = [
        row
        for row in rows
        if str(row.get("status") or "") not in TERMINAL or parse_ts(row.get("finished_at")) is None
    ]
    end = now if open_tasks else max((value for value in ended if value is not None), default=None)
    start = min(starts, default=None)
    findings = []
    if not rows:
        findings.append("no_tasks: captured response contains no task runs")
    window_seconds = seconds_between(start, end) if start is not None and end is not None else None
    if rows and window_seconds is None:
        findings.append("session_window_unavailable: cannot derive start/end timestamps")
    return {
        "source": source,
        "task_count": len(rows),
        "open_task_count": len(open_tasks),
        "window_seconds": window_seconds,
        "tasks": rows,
        "findings": findings,
    }


def summarize(sessions, *, claim_budget, start_budget, total_budget, min_samples):
    rows = [row for session in sessions for row in session["tasks"]]
    windows = [session["window_seconds"] for session in sessions if session["window_seconds"] is not None]
    claims = [row["claim_seconds"] for row in rows if row["claim_seconds"] is not None]
    starts = [row["start_seconds"] for row in rows if row["start_seconds"] is not None]
    runs = [row["run_seconds"] for row in rows if row["run_seconds"] is not None]
    summary = {
        "session_samples": len(windows),
        "task_samples": len(rows),
        "claim_samples": len(claims),
        "start_samples": len(starts),
        "run_samples": len(runs),
        "p95_claim_seconds": percentile(claims),
        "p95_start_seconds": percentile(starts),
        "p95_run_seconds": percentile(runs),
        "p95_session_seconds": percentile(windows),
        "max_session_seconds": round(max(windows), 3) if windows else None,
        "findings": [],
    }
    findings = summary["findings"]
    if not rows:
        findings.append("no_tasks: no task runs in any input")
    if len(windows) < min_samples:
        findings.append(
            "insufficient_samples: %d session samples < %d required for a P95 claim"
            % (len(windows), min_samples)
        )
    if summary["p95_claim_seconds"] is not None and summary["p95_claim_seconds"] > claim_budget:
        findings.append(
            "p95_claim_slow: %.1fs > %.1fs budget"
            % (summary["p95_claim_seconds"], claim_budget)
        )
    if summary["p95_start_seconds"] is not None and summary["p95_start_seconds"] > start_budget:
        findings.append(
            "p95_start_slow: %.1fs > %.1fs budget"
            % (summary["p95_start_seconds"], start_budget)
        )
    if summary["p95_session_seconds"] is not None and summary["p95_session_seconds"] > total_budget:
        findings.append(
            "p95_session_slow: %.1fs > %.1fs budget"
            % (summary["p95_session_seconds"], total_budget)
        )
    return summary


def format_cell(value):
    return "-" if value is None else ("%.1f" % value)


def render_table(rows):
    header = ("task", "agent", "kind", "status", "claim_s", "start_s", "run_s", "notes")
    lines = ["%-10s %-38s %-6s %-10s %8s %8s %8s  %s" % header]
    for row in rows:
        lines.append("%-10s %-38s %-6s %-10s %8s %8s %8s  %s" % (
            row["id"][:10],
            row["agent_id"],
            "leader" if row["is_leader_task"] else "member",
            row["status"] or "-",
            format_cell(row["claim_seconds"]),
            format_cell(row["start_seconds"]),
            format_cell(row["run_seconds"]),
            "; ".join(row["findings"]),
        ))
    return "\n".join(lines)


def read_sources(paths):
    sources = []
    stdin_used = False
    for path in paths:
        if path == "-":
            if stdin_used:
                raise ValueError("stdin can only be used once")
            stdin_used = True
            raw = sys.stdin.read()
            label = "<stdin>"
        else:
            file_path = Path(path)
            raw = file_path.read_text(encoding="utf-8")
            label = file_path.name
        sources.append((label, items_of(json.loads(raw))))
    return sources


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit task timings from captured /task-runs JSON")
    parser.add_argument("paths", nargs="*", help="JSON files, one per session; - for stdin")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--strict", action="store_true", help="non-zero exit when any finding exists")
    parser.add_argument("--claim-budget", type=float, default=DEFAULT_CLAIM_BUDGET_SECONDS)
    parser.add_argument("--start-budget", type=float, default=DEFAULT_START_BUDGET_SECONDS)
    parser.add_argument("--total-budget", type=float, default=DEFAULT_TOTAL_BUDGET_SECONDS)
    parser.add_argument("--stall-seconds", type=float, default=DEFAULT_STALL_SECONDS)
    parser.add_argument(
        "--min-samples",
        type=int,
        default=1,
        help="minimum session samples for a meaningful P95; release evidence should use 20",
    )
    parser.add_argument("--now", help="override 'now' (ISO8601) for reproducible audits")
    args = parser.parse_args(argv)

    try:
        sources = read_sources(args.paths or ["-"])
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print("task_timeline: %s" % error, file=sys.stderr)
        return 2

    now = parse_ts(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print("task_timeline: --now is not ISO8601", file=sys.stderr)
        return 2

    sessions = [
        analyze_source(source, tasks, now, args.claim_budget, args.start_budget, args.stall_seconds)
        for source, tasks in sources
    ]
    summary = summarize(
        sessions,
        claim_budget=args.claim_budget,
        start_budget=args.start_budget,
        total_budget=args.total_budget,
        min_samples=max(args.min_samples, 1),
    )

    if args.json:
        print(json.dumps(
            {"now": now.isoformat(), "sessions": sessions, "tasks": [row for s in sessions for row in s["tasks"]], "summary": summary},
            ensure_ascii=False,
            indent=2,
        ))
    else:
        for session in sessions:
            if len(sessions) > 1:
                print("## %s" % session["source"])
            print(render_table(session["tasks"]))
            if session["window_seconds"] is not None:
                print("session window: %.1fs" % session["window_seconds"])
            for finding in session["findings"]:
                print("session finding: %s" % finding)
        print(
            "P95: sessions=%d tasks=%d claim=%s start=%s run=%s session=%s"
            % (
                summary["session_samples"],
                summary["task_samples"],
                format_cell(summary["p95_claim_seconds"]),
                format_cell(summary["p95_start_seconds"]),
                format_cell(summary["p95_run_seconds"]),
                format_cell(summary["p95_session_seconds"]),
            )
        )
        for finding in summary["findings"]:
            print("gate finding: %s" % finding)

    has_findings = any(session["findings"] for session in sessions) or bool(summary["findings"])
    has_row_findings = any(row["findings"] for session in sessions for row in session["tasks"])
    if args.strict and (has_findings or has_row_findings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
