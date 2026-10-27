#!/usr/bin/env python3
"""Remote verification probe for DeepWorks experts and expert teams.

Sends ONE message to an agent chat session (new session, or an existing one via
--session for selection/continuation rounds), polls the resulting task until a
terminal status, and prints a structured JSON result:

- segmented timings: claim (created->claimed), start (claimed->started),
  execution (started->terminal), total (created->terminal), with the timing
  source (server timestamps when available, poll-observation upper bound
  otherwise);
- terminal status, failure_code, last observed status;
- session id / task id;
- hidden continuation markers (root issue id non-empty, state marker presence);
- the final assistant message;
- a ready-to-paste `ledger_row` for docs/sdd/ledger-latency-running.md.

Side-effect boundary: the only platform write is the chat message itself (plus
session creation for new rounds). The probe never deletes sessions, creates
issues, dispatches agents, or calls Foil/MES APIs.

The IAM token is bridged from the running DeepWorks desktop app through the
dp-ask-teams helper script. Its path is parameterized (--ask-teams-script or
DP_ASK_TEAMS_SCRIPT); no other machine-specific assumption is baked in, and a
missing helper fails fast with an actionable error. Standard library only.

Usage:
  python3 scripts/remote_probe.py --agent <agent-name> --message "测试消息" \
      [--round smoke] [--timeout 300] [--session <id>] [--env test] \
      [--workspace <workspace-slug>] [--poll-interval 5] \
      [--debug-port 9228] [--ask-teams-script /path/to/ask_teams_agent.py] \
      [--print-row]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA_NAME = "industrial-agent-remote-probe/1"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ASK_TEAMS_SCRIPT_ENV = "DP_ASK_TEAMS_SCRIPT"
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 300.0
MAX_CONSECUTIVE_POLL_ERRORS = 5

ROOT_MARKER = re.compile(
    r"<!--\s*(?:squad_root_issue_id|team_root_issue_id|expert_root_issue_id):([^>]*?)\s*-->"
)
STATE_MARKER = re.compile(
    r"<!--\s*(?:team_state|expert_state|chain_state):[^>]*?-->" ,
    re.IGNORECASE,
)

EXIT_COMPLETED = 0
EXIT_BUSINESS_FAILURE = 1
EXIT_CONFIG_ERROR = 2
EXIT_TIMEOUT = 3
EXIT_POLL_ERROR = 4


class ProbeConfigError(RuntimeError):
    """Operator-fixable configuration problem (missing helper, no token, ...)."""


# ---------------------------------------------------------------------------
# dp-ask-teams helper loading and token bridging
# ---------------------------------------------------------------------------

def load_ask_teams_module(script_path: str) -> Any:
    path = Path(script_path).expanduser()
    if not path.is_file():
        raise ProbeConfigError(
            f"dp-ask-teams helper script not found at {path}. "
            f"Pass --ask-teams-script <path> or set {ASK_TEAMS_SCRIPT_ENV}=<path> "
            "(expected file: ask_teams_agent.py from the dp-ask-teams skill)."
        )
    spec = importlib.util.spec_from_file_location("remote_probe_ask_teams_agent", path)
    if spec is None or spec.loader is None:
        raise ProbeConfigError(f"cannot load dp-ask-teams helper as a module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for attribute in ("read_iam_cookie", "find_desktop_debug_port", "TeamsClient"):
        if not hasattr(module, attribute):
            raise ProbeConfigError(
                f"dp-ask-teams helper at {path} lacks {attribute}(); is it the right file?"
            )
    return module


def resolve_token(module: Any, env_name: str, debug_port: int | None) -> str:
    if debug_port:
        return str(module.read_iam_cookie(f"http://127.0.0.1:{debug_port}"))
    port = module.find_desktop_debug_port(env_name)
    if port:
        return str(module.read_iam_cookie(f"http://127.0.0.1:{port}"))
    raise ProbeConfigError(
        f"no DeepWorks desktop debug port found for env '{env_name}'. Open and log in "
        "to the matching desktop app, or pass --debug-port, or provide the token "
        "directly via --token / AGENT_TEAMS_TOKEN."
    )


def build_client(module: Any, token: str, env_name: str) -> Any:
    env = dict(os.environ)
    env["AGENT_TEAMS_TOKEN_PROD" if env_name == "prod" else "AGENT_TEAMS_TOKEN"] = token
    return module.TeamsClient(env, env_name)


# ---------------------------------------------------------------------------
# Payload helpers (local, so run_probe has no dependency on the helper module)
# ---------------------------------------------------------------------------

def _items(payload: object) -> list:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "agents", "sessions", "messages", "tasks"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def parse_iso_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def extract_markers(message_text: str) -> dict:
    text_value = message_text or ""
    match = ROOT_MARKER.search(text_value)
    root_value = match.group(1).strip() if match else ""
    return {
        "root_issue_id": root_value,
        "root_nonempty": bool(root_value),
        "state_marker_present": bool(STATE_MARKER.search(text_value)),
    }


def compute_timings(task_payload: dict, observed: dict, t_send: float) -> tuple[dict, str]:
    """Prefer server timestamps; fall back to poll-observation upper bounds."""
    created = parse_iso_timestamp(task_payload.get("created_at"))
    claimed = parse_iso_timestamp(task_payload.get("claimed_at"))
    started = parse_iso_timestamp(task_payload.get("started_at"))
    ended = parse_iso_timestamp(task_payload.get("completed_at"))

    server_used = 0
    observation_used = 0

    def pick(server_start, server_end, observation_start, observation_end):
        nonlocal server_used, observation_used
        if server_start is not None and server_end is not None:
            server_used += 1
            return round((server_end - server_start).total_seconds(), 1)
        if observation_start is not None and observation_end is not None:
            observation_used += 1
            return round(observation_end - observation_start, 1)
        return None

    timings = {
        "claim_seconds": pick(created, claimed, t_send, observed.get("claim")),
        "start_after_claim_seconds": pick(
            claimed, started, observed.get("claim"), observed.get("start")
        ),
        "exec_seconds": pick(started, ended, observed.get("start"), observed.get("terminal")),
        "total_seconds": pick(created, ended, t_send, observed.get("terminal")),
    }
    if server_used and not observation_used:
        source = "server"
    elif observation_used and not server_used:
        source = "observation"
    elif server_used and observation_used:
        source = "mixed"
    else:
        source = "none"
    return timings, source


def _fmt_cell(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        if abs(value - round(value)) < 0.05:
            return str(int(round(value)))
        return f"{value:.1f}"
    return str(value)


def render_ledger_row(result: dict) -> str:
    """One row per round, matching the docs/sdd/ledger-latency-running.md table."""
    timings = result.get("timings") or {}
    date = str(result.get("sent_at") or "")[:10] or "-"
    if result.get("final_assistant_message"):
        marker_cell = "yes" if (result.get("markers") or {}).get("root_nonempty") else "no"
    else:
        marker_cell = "-"
    cells = [
        date,
        result.get("env") or "-",
        result.get("agent") or "-",
        result.get("round_type") or "-",
        (result.get("session_id") or "")[:8] or "-",
        (result.get("task_id") or "")[:8] or "-",
        _fmt_cell(timings.get("claim_seconds")),
        _fmt_cell(timings.get("start_after_claim_seconds")),
        _fmt_cell(timings.get("exec_seconds")),
        _fmt_cell(timings.get("total_seconds")),
        marker_cell,
        result.get("terminal_status") or "-",
        _fmt_cell(result.get("failure_code")),
        result.get("source") or f"remote_probe {date}",
    ]
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def exit_code_for(result: dict) -> int:
    status = result.get("terminal_status")
    if status == "completed":
        return EXIT_COMPLETED
    if status in ("failed", "cancelled"):
        return EXIT_BUSINESS_FAILURE
    if status == "timeout":
        return EXIT_TIMEOUT
    if status == "poll-error":
        return EXIT_POLL_ERROR
    return EXIT_CONFIG_ERROR


# ---------------------------------------------------------------------------
# Probe flow (client is any object with .request(path, method=, workspace_slug=, body=))
# ---------------------------------------------------------------------------

def resolve_agent(client: Any, workspace_slug: str, agent_name: str) -> dict:
    agents = [entry for entry in _items(client.request("/api/agents", workspace_slug=workspace_slug))
              if isinstance(entry, dict)]
    matches = [entry for entry in agents if str(entry.get("name") or "").strip() == agent_name]
    if len(matches) == 1:
        return matches[0]
    available = ", ".join(str(entry.get("name")) for entry in agents)
    if not matches:
        raise ProbeConfigError(f"agent not found by name '{agent_name}'. Available: {available}")
    raise ProbeConfigError(f"agent name is ambiguous: '{agent_name}'")


def run_probe(
    client: Any,
    *,
    agent_name: str,
    message: str,
    workspace_slug: str,
    env_name: str = "test",
    round_type: str = "other",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    session_id: str | None = None,
    session_title_prefix: str = "remote-probe",
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    sent_at = datetime.now().astimezone().isoformat(timespec="seconds")
    t_send = clock()
    agent_id = ""

    if session_id is None:
        agent = resolve_agent(client, workspace_slug, agent_name)
        agent_id = str(agent.get("id") or "")
        created_session = client.request(
            "/api/chat/sessions",
            method="POST",
            workspace_slug=workspace_slug,
            body={
                "project_id": agent.get("project_id"),
                "target_type": "agent",
                "agent_id": agent_id,
                "title": f"{session_title_prefix} {datetime.now().strftime('%Y%m%d-%H%M%S')}",
            },
        )
        session_id = str(created_session.get("id") or "") if isinstance(created_session, dict) else ""
        if not session_id:
            raise ProbeConfigError(f"create chat session did not return an id: {created_session}")

    sent = client.request(
        f"/api/chat/sessions/{urllib.parse.quote(session_id)}/messages",
        method="POST",
        workspace_slug=workspace_slug,
        body={"content": message, "attachment_ids": [], "force_fresh_session": False},
    )
    task_id = str((sent.get("taskId") or sent.get("task_id") or "")) if isinstance(sent, dict) else ""
    if not task_id:
        raise ProbeConfigError(f"send message did not return a task id: {sent}")

    deadline = t_send + timeout_seconds
    terminal_status = ""
    failure_code = None
    last_status = ""
    last_poll_error = ""
    poll_count = 0
    poll_errors = 0
    consecutive_errors = 0
    observed = {"claim": None, "start": None, "terminal": None}
    task_payload: dict = {}

    while True:
        now = clock()
        if now >= deadline:
            terminal_status = "timeout"
            observed["terminal"] = now
            break
        poll_count += 1
        try:
            task_payload = client.request(
                f"/api/tasks/{urllib.parse.quote(task_id)}", workspace_slug=workspace_slug
            ) or {}
        except Exception as exc:  # noqa: BLE001 - transient poll failures are recorded, not fatal
            poll_errors += 1
            consecutive_errors += 1
            last_poll_error = str(exc)[:200]
            if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                terminal_status = "poll-error"
                observed["terminal"] = now
                break
        else:
            consecutive_errors = 0
            status = str(task_payload.get("status") or "").lower()
            last_status = status
            if task_payload.get("claimed_at") or status in ("claimed", "running"):
                if observed["claim"] is None:
                    observed["claim"] = now
            if task_payload.get("started_at") or status == "running":
                if observed["start"] is None:
                    observed["start"] = now
            if status in TERMINAL_STATUSES:
                terminal_status = status
                failure_code = task_payload.get("failure_code")
                observed["terminal"] = now
                break
        sleep(poll_interval_seconds)

    final_message = ""
    try:
        history = _items(client.request(
            f"/api/chat/sessions/{urllib.parse.quote(session_id)}/messages",
            workspace_slug=workspace_slug,
        ))
        for entry in reversed(history):
            if not isinstance(entry, dict):
                continue
            content = str(entry.get("content") or "").strip()
            if str(entry.get("role") or "") == "assistant" and content:
                final_message = content
                break
    except Exception as exc:  # noqa: BLE001 - best effort; timings are already secured
        last_poll_error = last_poll_error or str(exc)[:200]

    timings, timing_source = compute_timings(task_payload, observed, t_send)
    result = {
        "schema": SCHEMA_NAME,
        "round_type": round_type,
        "env": env_name,
        "workspace": workspace_slug,
        "agent": agent_name or "",
        "session_id": session_id,
        "task_id": task_id,
        "sent_at": sent_at,
        "terminal_status": terminal_status,
        "failure_code": failure_code,
        "last_status": last_status,
        "timing_source": timing_source,
        "timings": timings,
        "markers": extract_markers(final_message),
        "final_assistant_message": final_message,
        "poll_count": poll_count,
        "poll_errors": poll_errors,
    }
    if last_poll_error:
        result["last_poll_error"] = last_poll_error
    result["ledger_row"] = render_ledger_row(result)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send one round to a remote Agent Teams agent and measure it (LAB-021)."
    )
    parser.add_argument("--agent", default="", help="agent name (required unless --session)")
    parser.add_argument("--message", required=True, help="message text sent as-is (one round)")
    parser.add_argument("--round", default="other",
                        help="round type label for the latency ledger (query/selection/continuation/...)")
    parser.add_argument("--session", default="",
                        help="existing chat session id (selection/continuation rounds); skips agent resolution")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS,
                        help="seconds to wait for a terminal status (default 300)")
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS,
                        help="seconds between task polls (default 5)")
    parser.add_argument("--env", default="test", choices=("test", "prod"),
                        help="Agent Teams environment (default test)")
    parser.add_argument("--workspace", default="",
                        help="workspace slug (required unless $AGENT_TEAMS_WORKSPACE_SLUG is set)")
    parser.add_argument("--debug-port", type=int, default=None,
                        help="DeepWorks desktop DevTools debug port (skip auto discovery)")
    parser.add_argument("--ask-teams-script", default="",
                        help=f"path to ask_teams_agent.py (or ${ASK_TEAMS_SCRIPT_ENV})")
    parser.add_argument("--token", default="",
                        help="bearer token (skip desktop bridging); default $AGENT_TEAMS_TOKEN")
    parser.add_argument("--title-prefix", default="remote-probe", help="chat session title prefix")
    parser.add_argument("--print-row", action="store_true",
                        help="print only the latency ledger row instead of the full JSON")
    args = parser.parse_args(argv)
    if not args.session and not args.agent:
        parser.error("--agent is required unless --session is given")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workspace_slug = (
        args.workspace
        or os.environ.get("AGENT_TEAMS_WORKSPACE_SLUG", "").strip()
    )
    script_path = (
        args.ask_teams_script
        or os.environ.get(ASK_TEAMS_SCRIPT_ENV, "").strip()
    )
    try:
        if not workspace_slug:
            raise ProbeConfigError("--workspace or AGENT_TEAMS_WORKSPACE_SLUG is required")
        if not script_path:
            raise ProbeConfigError(
                f"--ask-teams-script or {ASK_TEAMS_SCRIPT_ENV} is required "
                "(path to dp-ask-teams ask_teams_agent.py)"
            )
        module = load_ask_teams_module(script_path)
        token = args.token or os.environ.get("AGENT_TEAMS_TOKEN", "").strip()
        if not token:
            token = resolve_token(module, args.env, args.debug_port)
        client = build_client(module, token, args.env)
        result = run_probe(
            client,
            agent_name=args.agent.strip(),
            message=args.message,
            workspace_slug=workspace_slug,
            env_name=args.env,
            round_type=args.round.strip() or "other",
            timeout_seconds=args.timeout,
            poll_interval_seconds=args.poll_interval,
            session_id=args.session.strip() or None,
            session_title_prefix=args.title_prefix,
        )
    except ProbeConfigError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if args.print_row:
        print(result["ledger_row"])
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code_for(result)


if __name__ == "__main__":
    raise SystemExit(main())
