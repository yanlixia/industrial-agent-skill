#!/usr/bin/env python3
"""Session Storyboard CLI.

Usage:
  python session_story.py run --session <id> [--env test] [--workspace <slug>]
  python session_story.py collect --source artifacts --artifacts-dir <dir>
  python session_story.py render <trace.json> --out <story.html>
  python session_story.py validate <trace.json>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from storylib.collectors import collect_artifacts, collect_cloud, collect_local
from storylib.metrics import enrich_trace
from storylib.model import excerpt
from storylib.render import render_html


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: str) -> dict:
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))


def _default_out(trace: dict) -> Path:
    target = trace.get("target") or {}
    ident = str(target.get("id") or "session").replace("/", "-")
    return Path.cwd() / "tmp" / "session-story" / ident


def _report(trace: dict) -> str:
    metrics = trace.get("metrics") or {}
    lines = [
        f"schema: {trace.get('schema_version')}",
        f"target: {trace.get('target', {}).get('kind')} {trace.get('target', {}).get('id')}",
        f"window: {trace.get('window', {}).get('start')} -> {trace.get('window', {}).get('end')}",
        f"rounds: {metrics.get('round_count', 0)}  max_round: {metrics.get('max_round_seconds')}s",
        f"tasks: {metrics.get('task_count', 0)}  tool_calls: {metrics.get('tool_calls', 0)}  exploration: {metrics.get('exploration_calls', 0)}",
        f"tool coverage: end {metrics.get('tools_with_end', 0)}/{metrics.get('tool_calls', 0)}  result {metrics.get('tools_with_result', 0)}/{metrics.get('tool_calls', 0)}  non-empty output {metrics.get('tools_with_output', 0)}/{metrics.get('tool_calls', 0)}  cwd {metrics.get('tools_with_cwd', 0)}/{metrics.get('tool_calls', 0)}  exit {metrics.get('tools_with_exit', 0)}/{metrics.get('tool_calls', 0)}",
        f"errors: {metrics.get('error_events', 0)}  categories: {metrics.get('error_categories') or {}}",
        f"events: {len(trace.get('events') or [])}  artifacts: {metrics.get('artifact_count', 0)}",
        f"raw evidence: {(trace.get('raw_evidence') or {}).get('collection', {}).get('counts', {})}",
        "sources:",
    ]
    for source, count in sorted((metrics.get("source_event_counts") or {}).items()):
        lines.append(f"  - {source}: {count}")
    if trace.get("gaps"):
        lines.append("gaps:")
        lines.extend(f"  - {item}" for item in trace["gaps"])
    return "\n".join(lines) + "\n"


def _validate(trace: dict) -> list[str]:
    errors: list[str] = []
    if trace.get("schema_version") != "session-story/v1":
        errors.append("schema_version 必须是 session-story/v1")
    if not trace.get("target"):
        errors.append("缺少 target")
    if not trace.get("rounds"):
        errors.append("缺少 rounds")
    previous = ""
    for event in trace.get("events") or []:
        ts = str(event.get("ts") or "")
        if not ts:
            errors.append(f"event {event.get('id')} 缺少 ts")
        if previous and ts < previous:
            errors.append("events 未按时间排序")
            break
        previous = ts
    return errors


def _collect(args: argparse.Namespace) -> dict:
    if args.source == "artifacts":
        if not args.artifacts_dir:
            raise SystemExit("--source artifacts 需要 --artifacts-dir")
        trace = collect_artifacts(args.artifacts_dir)
    elif args.source == "local":
        if not args.session:
            raise SystemExit("--source local 需要 --session")
        trace = collect_local(args.session, env_name=args.env, db_path=args.db)
    elif args.source == "cloud":
        if not args.session:
            raise SystemExit("--source cloud 需要 --session")
        trace = collect_cloud(
            args.session,
            env_name=args.env,
            workspace=args.workspace,
            token=args.token,
            debug_port=args.debug_port,
            teams_script=args.teams_script,
            no_sync=args.no_sync_token,
        )
    else:
        raise SystemExit(f"未知 source: {args.source}")
    return enrich_trace(trace, budget_seconds=args.budget)


def _write_outputs(trace: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = out_dir / "session-trace.json"
    html_path = out_dir / "session-story.html"
    _write_json(trace_path, trace)
    (out_dir / "collection-report.txt").write_text(_report(trace), encoding="utf-8")
    render_html(trace, str(html_path))
    return trace_path, html_path


def _open_path(path: Path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        pass


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", default="", help="chat session id (cloud) or ses_* id (local)")
    parser.add_argument("--env", default="test", choices=("test", "prod", "dev"))
    parser.add_argument("--workspace", default="", help="workspace slug; default deepsense-workspace")
    parser.add_argument("--token", default="", help="Agent Teams IAM token; default bridge from desktop")
    parser.add_argument("--debug-port", type=int, default=None)
    parser.add_argument("--teams-script", default="", help="path to dp-ask-teams ask_teams_agent.py")
    parser.add_argument("--no-sync-token", action="store_true")
    parser.add_argument("--artifacts-dir", default="", help="saved acceptance artifact directory")
    parser.add_argument("--db", default="", help="explicit opencode.db path for --source local")
    parser.add_argument("--budget", type=float, default=120.0, help="per-round budget seconds")
    parser.add_argument("--out", default="", help="output directory; default tmp/session-story/<id>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a self-contained HTML replay of an agent session.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("collect", "run"):
        p = sub.add_parser(name, help="collect a trace" + (" and render HTML" if name == "run" else ""))
        p.add_argument("--source", default="cloud", choices=("cloud", "artifacts", "local"))
        _add_common(p)
        p.add_argument("--open", action="store_true", help="open HTML after generation")

    p_render = sub.add_parser("render", help="render an existing trace")
    p_render.add_argument("trace")
    p_render.add_argument("--out", required=True)
    p_render.add_argument("--template", default="")

    p_validate = sub.add_parser("validate", help="validate an existing trace")
    p_validate.add_argument("trace")

    args = parser.parse_args(argv)
    if args.cmd == "render":
        trace = _read_json(args.trace)
        print(render_html(trace, args.out, args.template))
        return 0
    if args.cmd == "validate":
        trace = _read_json(args.trace)
        errors = _validate(trace)
        if errors:
            print("\n".join(f"ERROR: {item}" for item in errors), file=sys.stderr)
            return 1
        print("OK: trace is valid")
        return 0

    try:
        trace = _collect(args)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    errors = _validate(trace)
    if errors:
        print("\n".join(f"ERROR: {item}" for item in errors), file=sys.stderr)
        return 3
    out_dir = Path(args.out).expanduser() if args.out else _default_out(trace)
    trace_path, html_path = _write_outputs(trace, out_dir)
    if args.cmd == "run":
        print(f"trace: {trace_path}")
        print(f"html:  {html_path}")
    else:
        print(f"trace: {trace_path}")
    if getattr(args, "open", False):
        _open_path(html_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
