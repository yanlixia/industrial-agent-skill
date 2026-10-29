"""Adapters that turn platform data into the normalized SessionStory trace."""

from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .model import (
    add_event,
    add_provenance,
    excerpt,
    iso_ts,
    ms_between,
    new_trace,
    parse_ts,
    redact,
    redact_deep,
    seconds_between,
)

SQUAD_ROOT_RE = re.compile(r"squad_root_issue_id:\s*([0-9a-fA-F-]{36})")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
ROUND_LABELS = {
    "query": "查询",
    "selection": "选择",
    "confirm": "确认",
    "execution": "执行",
    "execution_repeat": "重复确认",
    "other": "其他",
}


def _items(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "list", "data", "events", "messages", "tasks", "artifacts", "issues"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _agent_name(raw: object) -> str:
    if isinstance(raw, dict):
        return str(raw.get("name") or raw.get("agent_name") or raw.get("role") or "agent")
    return str(raw or "agent")


def _event_agent(event: dict[str, Any]) -> str:
    return _agent_name(event.get("agent") or (event.get("payload") or {}).get("agent"))


def _event_summary(event: dict[str, Any]) -> str:
    return str(event.get("content") or event.get("summary") or event.get("type") or "")


def _task_title(detail: dict[str, Any], fallback: str = "task") -> str:
    prompt = str(detail.get("prompt") or "")
    first_line = prompt.splitlines()[0].strip() if prompt else ""
    first_line = re.sub(r"^ISS-\d+:\s*", "", first_line)
    return str(detail.get("title") or first_line or fallback)


def _is_uuid(value: str) -> bool:
    return bool(UUID_RE.match(str(value or "")))


def _round_id(index: int) -> str:
    return f"R{index + 1}"


def _duration_ms(start: Any, end: Any) -> int | None:
    return ms_between(start, end)


def _tool_messages(task_messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair tool_use with the following tool_result by sequence order."""
    rows = sorted(task_messages, key=lambda item: int(item.get("seq") or 0))
    pairs: list[dict[str, Any]] = []
    pending: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        kind = str(row.get("event_type") or row.get("type") or "")
        tool = str(row.get("tool") or "tool")
        if kind == "tool_use":
            pending[tool].append(row)
        elif kind == "tool_result":
            queue = pending.get(tool) or []
            if queue:
                use_row = queue.pop(0)
                pairs.append({"use": use_row, "result": row, "tool": tool})
            else:
                pairs.append({"use": None, "result": row, "tool": tool})
    for queue in pending.values():
        for use_row in queue:
            pairs.append({"use": use_row, "result": None, "tool": use_row.get("tool") or "tool"})
    pairs.sort(key=lambda pair: int((pair.get("use") or pair.get("result") or {}).get("seq") or 0))
    return pairs


def _sandbox_from_tool(pair: dict[str, Any]) -> dict[str, Any]:
    use_row, result_row = pair.get("use") or {}, pair.get("result") or {}
    payload = use_row.get("input") or {}
    if not isinstance(payload, dict):
        payload = {"input": payload}
    command = payload.get("command") or payload.get("cmd") or payload.get("input") or use_row.get("content") or ""
    output = result_row.get("output") or result_row.get("content") or ""
    if isinstance(output, dict):
        output = output.get("output") or output.get("content") or json.dumps(output, ensure_ascii=False)
    output = redact(str(output or ""))
    status = str(result_row.get("status") or "")
    exit_code = _extract_exit_code(output)
    return {
        "cmd": redact(command),
        "cmd_excerpt": excerpt(redact(command), 240),
        "cwd": redact(payload.get("cwd") or payload.get("workdir") or ""),
        "io": excerpt(output, 400),
        "result_output": output,
        "files": [],
        "network": [],
        "status": status or "unknown",
        "exit": exit_code if exit_code is not None else "not captured",
        "result_observed": bool(result_row),
        "seq": use_row.get("seq"),
        "result_source": "task-messages" if result_row else "",
    }


def _runtime_command(event: dict[str, Any]) -> str:
    payload = event.get("input") or (event.get("message") or {}).get("input") or {}
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("command") or payload.get("cmd") or payload.get("input") or "")


def _runtime_output(event: dict[str, Any]) -> str:
    output = event.get("output")
    if output is None:
        output = (event.get("payload") or {}).get("output")
    if output is None:
        output = (event.get("message") or {}).get("output")
    if output is None:
        output = event.get("content")
    if isinstance(output, dict):
        output = output.get("output") or output.get("content") or json.dumps(output, ensure_ascii=False)
    return redact(str(output or ""))


def _extract_exit_code(text: str) -> str | None:
    match = re.search(r"(?i)(?:exit(?:ed)?(?:\s+with)?\s+(?:code|status)?\s*[:=]?\s*)(-?\d+)", text or "")
    return match.group(1) if match else None


def _tool_pair_for_result(
    pairs: list[dict[str, Any]],
    used: set[int],
    tool: str,
    output: str,
) -> dict[str, Any] | None:
    """Find the task-message pair that belongs to a runtime result.

    Task messages have no timestamp, and the runtime occasionally emits
    duplicate/standalone tool_result events. Matching by tool + output prefix is
    more reliable than blindly consuming pairs by index. If no output matches,
    fall back to the earliest unused pair for the same tool.
    """

    needle = " ".join(str(output or "").split())[:240]
    candidates = []
    for index, pair in enumerate(pairs):
        if index in used:
            continue
        pair_tool = str(pair.get("tool") or "tool")
        if tool and pair_tool != tool:
            continue
        result_row = pair.get("result") or {}
        text = result_row.get("output") or result_row.get("content") or ""
        if isinstance(text, dict):
            text = text.get("output") or text.get("content") or json.dumps(text, ensure_ascii=False)
        haystack = " ".join(str(text or "").split())[:240]
        if needle and haystack and haystack == needle:
            return pair
        candidates.append(pair)
    if candidates:
        return candidates[0]
    for index, pair in enumerate(pairs):
        if index not in used:
            return pair
    return None


def _attach_tool_result(
    event: dict[str, Any],
    *,
    end_ts: Any,
    output: str,
    status: str,
    pair: dict[str, Any] | None = None,
) -> None:
    sandbox = event.setdefault("sandbox", {})
    if pair:
        result_row = pair.get("result") or {}
        if str(status or "").lower() in {"", "unknown"} and result_row.get("status"):
            status = str(result_row.get("status"))
        pair_output = result_row.get("output") or result_row.get("content") or ""
        if isinstance(pair_output, dict):
            pair_output = pair_output.get("output") or pair_output.get("content") or json.dumps(pair_output, ensure_ascii=False)
        pair_output = str(pair_output or "")
        if len(pair_output) > len(str(output or "")):
            output = pair_output
    output = redact(str(output or ""))
    exit_code = _extract_exit_code(output)
    if end_ts:
        event["end_ts"] = iso_ts(end_ts)
        event["duration_ms"] = ms_between(event.get("ts"), end_ts)
    sandbox["status"] = status or "unknown"
    sandbox["exit"] = exit_code if exit_code is not None else "not captured"
    sandbox["result_output"] = output
    sandbox["io"] = excerpt(output, 400) or sandbox.get("io", "")
    sandbox["result_observed"] = True
    sandbox["result_source"] = "runtime-events"
    if pair:
        use_row = pair.get("use") or {}
        payload = use_row.get("input") or {}
        if isinstance(payload, dict):
            cwd = payload.get("cwd") or payload.get("workdir") or ""
            if cwd and not sandbox.get("cwd"):
                sandbox["cwd"] = redact(cwd)
            command = payload.get("command") or payload.get("cmd") or payload.get("input") or ""
            if command and not sandbox.get("cmd"):
                sandbox["cmd"] = redact(command)
                sandbox["cmd_excerpt"] = excerpt(redact(command), 240)
        elif use_row.get("content") and not sandbox.get("cmd"):
            sandbox["cmd"] = redact(use_row.get("content"))
            sandbox["cmd_excerpt"] = excerpt(redact(use_row.get("content")), 240)
    if output and (not event.get("detail") or event.get("detail") == event.get("title")):
        event["detail"] = excerpt(output, 260)


def _attach_message_pair(event: dict[str, Any], pair: dict[str, Any]) -> bool:
    """Backfill command/output from task messages when runtime events lack a result.

    Task messages carry no per-event timestamp, so this helper deliberately does
    not invent ``end_ts`` or a duration. It only fills fields that are present
    in the observed message pair.
    """
    use_row = pair.get("use") or {}
    result_row = pair.get("result") or {}
    if not use_row and not result_row:
        return False
    sandbox = event.setdefault("sandbox", {})
    payload = use_row.get("input") or {}
    if isinstance(payload, dict):
        cwd = payload.get("cwd") or payload.get("workdir") or ""
        if cwd and not sandbox.get("cwd"):
            sandbox["cwd"] = redact(cwd)
        command = payload.get("command") or payload.get("cmd") or payload.get("input") or ""
        if command and not sandbox.get("cmd"):
            sandbox["cmd"] = redact(command)
            sandbox["cmd_excerpt"] = excerpt(redact(command), 240)
    output = result_row.get("output") or result_row.get("content") or ""
    if isinstance(output, dict):
        output = output.get("output") or output.get("content") or json.dumps(output, ensure_ascii=False)
    output = redact(str(output or ""))
    if output and not sandbox.get("result_output"):
        sandbox["result_output"] = output
        sandbox["io"] = excerpt(output, 400)
        sandbox["result_source"] = "task-messages"
        if not event.get("detail") or event.get("detail") == event.get("title"):
            event["detail"] = excerpt(output, 260)
    if result_row:
        sandbox["result_observed"] = True
        status = str(result_row.get("status") or (result_row.get("details") or {}).get("status") or "completed")
        if str(sandbox.get("status") or "").lower() in {"", "running", "unknown"}:
            sandbox["status"] = status
        exit_code = _extract_exit_code(output)
        if exit_code is not None and sandbox.get("exit") == "not captured":
            sandbox["exit"] = exit_code
    if use_row and not sandbox.get("tool"):
        sandbox["tool"] = str(use_row.get("tool") or "tool")
    return True


def _append_task_lifecycle(trace: dict[str, Any], task: dict[str, Any], source: str) -> None:
    agent = task.get("agent_name") or "agent"
    for field, title in (
        ("created_at", "Task created"),
        ("claimed_at", "Runtime claimed"),
        ("started_at", "Execution started"),
        ("completed_at", "Task completed"),
    ):
        ts = task.get(field)
        if not ts:
            continue
        add_event(
            trace,
            ts=ts,
            task_id=task.get("id", ""),
            round_id=task.get("round_id", ""),
            actor=agent,
            kind="platform" if field != "completed_at" else "member",
            title=title,
            detail=f"status={task.get('status') or 'unknown'}",
            source=source,
            confidence="exact",
        )


def _resolve_session(sessions: list[dict[str, Any]], session_id: str) -> dict[str, Any]:
    for item in sessions:
        if str(item.get("id")) == session_id:
            return item
    return {}


# ---------------------------------------------------------------------------
# Agent Teams cloud collector
# ---------------------------------------------------------------------------

TEAMS_SCRIPT_CANDIDATES = (
    Path.home() / ".agents/skills/dp-ask-teams/scripts/ask_teams_agent.py",
    Path.home() / ".claude/skills/dp-ask-teams/scripts/ask_teams_agent.py",
)


def _load_teams_module(script_path: str = "") -> Any:
    candidates = [Path(script_path).expanduser()] if script_path else []
    candidates.extend(TEAMS_SCRIPT_CANDIDATES)
    path = next((item for item in candidates if item.is_file()), None)
    if path is None:
        raise RuntimeError(
            "找不到 dp-ask-teams/scripts/ask_teams_agent.py；用 --teams-script 指定。"
        )
    spec = importlib.util.spec_from_file_location("session_story_teams", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 Teams helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for attr in ("TeamsClient", "sync_iam_token", "find_desktop_debug_port", "read_iam_cookie"):
        if not hasattr(module, attr):
            raise RuntimeError(f"Teams helper 缺少 {attr}(): {path}")
    return module


def _cloud_client(teams: Any, env_name: str, token: str, debug_port: int | None, no_sync: bool) -> tuple[Any, dict[str, str]]:
    env = dict(os.environ)
    if token:
        key = "AGENT_TEAMS_TOKEN_PROD" if env_name == "prod" else "AGENT_TEAMS_TOKEN"
        env[key] = token
    elif not no_sync:
        teams.sync_iam_token(env, env_name)
    client = teams.TeamsClient(env, env_name=env_name)
    return client, env


def collect_cloud(
    session_id: str,
    *,
    env_name: str = "test",
    workspace: str = "",
    token: str = "",
    debug_port: int | None = None,
    teams_script: str = "",
    no_sync: bool = False,
) -> dict[str, Any]:
    teams = _load_teams_module(teams_script)
    client, env = _cloud_client(teams, env_name, token, debug_port, no_sync)
    ws = workspace or env.get("AGENT_TEAMS_WORKSPACE_SLUG", "").strip() or "deepsense-workspace"

    sessions = _items(client.request("/api/chat/sessions?status=all", workspace_slug=ws))
    session = _resolve_session(sessions, session_id)
    if not session:
        try:
            session = client.request(f"/api/chat/sessions/{session_id}", workspace_slug=ws) or {}
        except Exception:
            session = {}
    messages = _items(client.request(f"/api/chat/sessions/{session_id}/messages", workspace_slug=ws))
    if not messages:
        raise RuntimeError(f"session {session_id} 没有消息，无法生成故事板")

    raw_evidence: dict[str, Any] = {
        "session": session,
        "session_messages": messages,
        "run_events": {},
        "task_details": {},
        "task_messages": {},
        "issues": {},
        "issue_comments": {},
        "artifacts": [],
        "collection": {
            "source": "cloud",
            "environment": env_name,
            "workspace": ws,
            "session_id": session_id,
        },
    }
    issue_cache: dict[str, dict[str, Any]] = {}
    comment_cache: dict[str, list[dict[str, Any]]] = {}

    def load_issue(issue_id: str) -> dict[str, Any]:
        value = str(issue_id or "")
        if not _is_uuid(value):
            return {}
        if value in issue_cache:
            return issue_cache[value]
        issue: dict[str, Any] = {}
        try:
            payload = client.request(f"/api/issues/{value}", workspace_slug=ws)
            if isinstance(payload, dict):
                issue = payload
        except Exception:
            issue = {}
        issue_cache[value] = issue
        raw_evidence["issues"][value] = issue
        comments: list[dict[str, Any]] = []
        try:
            comments = _items(client.request(f"/api/issues/{value}/comments", workspace_slug=ws))
        except Exception:
            comments = []
        comment_cache[value] = comments
        raw_evidence["issue_comments"][value] = comments
        return issue

    title = str(session.get("title") or "Agent Teams session")
    squad_id = str(session.get("squad_id") or "")
    agent_id = str(session.get("agent_id") or "")
    target_kind = "squad_session" if squad_id else "agent_session"
    trace = new_trace({
        "kind": target_kind,
        "id": session_id,
        "title": title,
        "environment": env_name,
        "workspace": ws,
        "squad_id": squad_id,
        "agent_id": agent_id,
        "root_issue_id": "",
        "status": "unknown",
    })
    trace["raw_evidence"] = raw_evidence
    add_provenance(trace, "platform-api", "sessions/messages/tasks/task-runs/issues/events-list", 1)
    add_provenance(trace, "session-messages", "user/assistant 可见消息", len(messages))

    root_issue_id = ""
    for message in messages:
        match = SQUAD_ROOT_RE.search(str(message.get("content") or ""))
        if match:
            root_issue_id = match.group(1)
            break
    trace["target"]["root_issue_id"] = root_issue_id

    # Group visible messages into rounds.
    rounds: list[dict[str, Any]] = []
    leader_task_ids: set[str] = set()
    current: dict[str, Any] | None = None
    for message in messages:
        role = str(message.get("role") or "")
        if role == "user":
            current = {
                "id": _round_id(len(rounds)),
                "label": ROUND_LABELS.get("other", "其他"),
                "user_message": str(message.get("content") or ""),
                "assistant_message": "",
                "start": message.get("created_at"),
                "end": "",
                "duration_ms": None,
                "task_id": str(message.get("task_id") or ""),
                "final_task_id": "",
                "run_id": str(message.get("run_id") or ""),
                "final_run_id": "",
                "status": "in_progress",
                "timing_source": "platform-api",
            }
            rounds.append(current)
            if current["task_id"]:
                leader_task_ids.add(current["task_id"])
        elif role == "assistant" and current is not None:
            current["assistant_message"] = str(message.get("content") or "")
            current["end"] = message.get("created_at")
            current["final_task_id"] = str(message.get("task_id") or "")
            current["final_run_id"] = str(message.get("run_id") or "")
            if current["final_task_id"]:
                leader_task_ids.add(current["final_task_id"])
            elapsed = message.get("elapsed_ms")
            if elapsed is not None:
                # The assistant elapsed_ms covers only this final leader
                # continuation, not necessarily the whole user-visible round.
                # Keep the user message start and use wall time when available.
                current["final_leader_elapsed_ms"] = int(elapsed)
            wall_duration = _duration_ms(current.get("start"), current.get("end"))
            if wall_duration is not None:
                current["duration_ms"] = wall_duration
                current["timing_source"] = "derived"
            elif elapsed is not None:
                current["duration_ms"] = int(elapsed)
                current["timing_source"] = "platform-api"
            current["status"] = "completed"
            current = None
    for item in rounds:
        if item["duration_ms"] is None:
            item["duration_ms"] = _duration_ms(item.get("start"), item.get("end"))

    trace["rounds"] = rounds

    run_ids = sorted({
        str(value)
        for item in rounds
        for value in (item.get("run_id"), item.get("final_run_id"))
        if value
    })
    run_events: dict[str, list[dict[str, Any]]] = {}
    task_ids = {
        str(value)
        for item in rounds
        for value in (item.get("task_id"), item.get("final_task_id"))
        if value
    }
    for run_id in run_ids:
        try:
            payload = client.request(f"/api/chat/runs/{run_id}/events-list", workspace_slug=ws)
        except Exception:
            payload = {}
        events = _items(payload)
        run_events[run_id] = events
        raw_evidence["run_events"][run_id] = events
        for event in events:
            if event.get("task_id"):
                task_ids.add(str(event["task_id"]))
            task = event.get("task") or (event.get("payload") or {}).get("task") or {}
            if isinstance(task, dict) and task.get("id"):
                task_ids.add(str(task["id"]))
                if task.get("is_leader"):
                    leader_task_ids.add(str(task["id"]))

    task_details: dict[str, dict[str, Any]] = {}
    task_messages: dict[str, list[dict[str, Any]]] = {}
    for task_id in sorted(task_ids):
        detail: dict[str, Any] = {}
        try:
            detail = client.request(f"/api/tasks/{task_id}", workspace_slug=ws) or {}
        except Exception:
            detail = {}
        if not isinstance(detail, dict):
            detail = {}
        task_details[task_id] = detail
        raw_evidence["task_details"][task_id] = detail
        try:
            task_messages[task_id] = _items(
                client.request(f"/api/tasks/{task_id}/messages", workspace_slug=ws)
            )
        except Exception:
            task_messages[task_id] = []
        raw_evidence["task_messages"][task_id] = task_messages[task_id]

    round_by_task: dict[str, str] = {}
    for item in rounds:
        for value in (item.get("task_id"), item.get("final_task_id")):
            if value:
                round_by_task[str(value)] = str(item.get("id") or "")
    for run_id, events in run_events.items():
        round_item = next((item for item in rounds if item.get("run_id") == run_id), None)
        if round_item is None:
            continue
        for event in events:
            task_id = str(event.get("task_id") or "")
            if task_id:
                round_by_task.setdefault(task_id, str(round_item.get("id") or ""))

    tasks: list[dict[str, Any]] = []
    for task_id in sorted(task_ids):
        detail = task_details.get(task_id, {})
        kind = "leader" if task_id in leader_task_ids else "member"
        tasks.append({
            "id": task_id,
            "kind": kind,
            "title": _task_title(detail, str(detail.get("agent_name") or "agent")),
            "agent_id": str(detail.get("agent_id") or ""),
            "agent_name": str(detail.get("agent_name") or "agent"),
            "status": str(detail.get("status") or "unknown"),
            "issue_id": str(detail.get("source_ref") or ""),
            "parent_task_id": str(detail.get("parent_task_id") or ""),
            "runtime_name": str(detail.get("runtime_name") or ""),
            "created_at": detail.get("created_at"),
            "claimed_at": detail.get("claimed_at"),
            "started_at": detail.get("started_at"),
            "completed_at": detail.get("completed_at"),
            "duration_ms": detail.get("duration_ms"),
            "round_id": round_by_task.get(task_id, ""),
            "source": "platform-api",
        })
    trace["tasks"] = tasks

    if not root_issue_id:
        for round_item in rounds:
            candidate_id = str(round_item.get("task_id") or "")
            source_ref = str((task_details.get(candidate_id) or {}).get("source_ref") or "")
            if _is_uuid(source_ref):
                root_issue_id = source_ref
                trace["target"]["root_issue_id"] = root_issue_id
                break

    task_statuses = {str(task.get("status") or "").lower() for task in tasks}
    active_statuses = {"running", "queued", "claimed", "in_progress", "pending", "starting"}
    if any(item.get("status") == "in_progress" for item in rounds) or task_statuses & active_statuses:
        trace["target"]["status"] = "in_progress"
    elif "failed" in task_statuses:
        trace["target"]["status"] = "failed"
    elif tasks and task_statuses <= {"completed", "cancelled"}:
        trace["target"]["status"] = "completed"
    elif not tasks and rounds and all(item.get("status") == "completed" for item in rounds):
        trace["target"]["status"] = "completed"

    # User/assistant round events.
    assistant_actor = next(
        (str(task.get("agent_name")) for task in tasks if task.get("kind") == "leader" and task.get("agent_name")),
        str(session.get("title") or "agent"),
    )
    for round_item in rounds:
        round_item["label"] = _guess_round_label(round_item.get("user_message", ""), round_item["id"])
        add_event(
            trace,
            ts=round_item.get("start"),
            round_id=round_item["id"],
            actor="user",
            kind="user",
            title="用户消息",
            detail=excerpt(round_item.get("user_message", ""), 220),
            source="session-messages",
            confidence="exact",
        )
        if round_item.get("end") and round_item.get("assistant_message"):
            add_event(
                trace,
                ts=round_item.get("end"),
                task_id=str(round_item.get("final_task_id") or ""),
                round_id=round_item["id"],
                actor=assistant_actor,
                kind="assistant",
                title="最终回复",
                detail=excerpt(round_item.get("assistant_message", ""), 260),
                source="session-messages",
                confidence="exact",
            )

    for task in tasks:
        _append_task_lifecycle(trace, task, "platform-api")
        pairs = _tool_messages(task_messages.get(task["id"], []))
        used_pairs: set[int] = set()
        pair_cursor = 0
        task_run_events: list[tuple[str, dict[str, Any]]] = []
        for run_id, events in run_events.items():
            if round_item_for_run(rounds, run_id) is None:
                continue
            for event in events:
                if str(event.get("task_id") or "") != task["id"]:
                    continue
                task_run_events.append((run_id, event))
        task_run_events.sort(key=lambda item: (int(item[1].get("sequence") or 0), item[1].get("created_at") or ""))
        active_event: dict[str, Any] | None = None
        active_pair: dict[str, Any] | None = None
        orphan_results = 0
        for run_id, event in task_run_events:
            kind = str(event.get("type") or "")
            if kind in {"member:execution_heartbeat", "member:thinking", "member:text_final"}:
                continue
            ts = event.get("created_at")
            actor = _event_agent(event) or task.get("agent_name") or "agent"
            if kind == "member:tool_running":
                # The backend emits two member:tool_running events for one
                # tool call (start marker + command marker). Keep the first
                # until a tool_result closes the call.
                command = _runtime_command(event)
                if active_event is not None:
                    sandbox = active_event.get("sandbox") or {}
                    if command and not sandbox.get("cmd"):
                        sandbox["cmd"] = redact(command)
                        sandbox["cmd_excerpt"] = excerpt(redact(command), 240)
                    continue
                pair = None
                while pair_cursor < len(pairs):
                    candidate_index = pair_cursor
                    pair_cursor += 1
                    if candidate_index not in used_pairs:
                        pair = pairs[candidate_index]
                        break
                sandbox = _sandbox_from_tool(pair or {})
                if command:
                    sandbox["cmd"] = redact(command)
                    sandbox["cmd_excerpt"] = excerpt(redact(command), 240)
                sandbox["cwd"] = sandbox.get("cwd") or ""
                sandbox["status"] = str(event.get("status") or (event.get("payload") or {}).get("status") or "running")
                sandbox["exit"] = "not captured"
                sandbox["result_output"] = ""
                sandbox["result_observed"] = False
                sandbox["seq"] = event.get("sequence")
                sandbox["tool"] = str(event.get("tool") or pair.get("tool") or "tool")
                sandbox["result_source"] = "runtime-events"
                active_pair = pair
                add_event(
                    trace,
                    ts=ts,
                    task_id=task["id"],
                    round_id=task.get("round_id", ""),
                    actor=actor,
                    kind="tool",
                    title=f"工具 · {sandbox['tool']}",
                    detail=excerpt(sandbox.get("io") or _event_summary(event), 260),
                    source="runtime-events",
                    confidence="exact",
                    sandbox=sandbox,
                )
                active_event = trace["events"][-1]
            elif kind == "member:tool_result":
                output = _runtime_output(event)
                status = str(event.get("status") or (event.get("payload") or {}).get("status") or "unknown")
                if active_event is None:
                    orphan_results += 1
                    sandbox = {
                        "cmd": "",
                        "cmd_excerpt": "",
                        "cwd": "",
                        "io": excerpt(output, 400),
                        "result_output": output,
                        "files": [],
                        "network": [],
                        "status": status,
                        "exit": _extract_exit_code(output) or "not captured",
                        "result_observed": True,
                        "seq": event.get("sequence"),
                        "tool": str(event.get("tool") or "tool"),
                        "result_source": "runtime-events",
                        "orphan_result": True,
                    }
                    add_event(
                        trace,
                        ts=ts,
                        task_id=task["id"],
                        round_id=task.get("round_id", ""),
                        actor=actor,
                        kind="platform",
                        title="工具结果（未配对）",
                        detail=excerpt(output, 260),
                        source="runtime-events",
                        confidence="partial",
                        sandbox=sandbox,
                    )
                    continue
                pair = active_pair
                matched = _tool_pair_for_result(pairs, used_pairs, str((active_event.get("sandbox") or {}).get("tool") or ""), output)
                if matched is not None:
                    pair = matched
                if pair is not None:
                    for index, candidate in enumerate(pairs):
                        if candidate is pair:
                            used_pairs.add(index)
                            break
                _attach_tool_result(
                    active_event,
                    end_ts=ts,
                    output=output,
                    status=status,
                    pair=pair,
                )
                active_event = None
                active_pair = None
            elif kind == "artifact:created":
                artifact = event.get("artifact") or (event.get("payload") or {}).get("artifact") or {}
                if not isinstance(artifact, dict):
                    artifact = {}
                artifact_id = str(artifact.get("id") or event.get("id") or "")
                artifact_name = str(artifact.get("name") or event.get("content") or "artifact")
                raw_evidence["artifacts"].append({"event": event, "artifact": artifact, "task_id": task["id"]})
                if not any(item.get("id") == artifact_id for item in trace["artifacts"]):
                    trace["artifacts"].append({
                        "id": artifact_id,
                        "name": artifact_name,
                        "task_id": task["id"],
                        "created_at": ts,
                        "size": artifact.get("size"),
                        "source": "runtime-events",
                    })
                add_event(
                    trace,
                    ts=ts,
                    task_id=task["id"],
                    round_id=task.get("round_id", ""),
                    actor=actor,
                    kind="artifact",
                    title=f"Artifact · {artifact_name}",
                    detail=f"task {task['id'][:8]} · artifact:created",
                    source="runtime-events",
                    confidence="exact",
                )

            elif kind == "subtask:created":
                payload = event.get("payload") or {}
                issue = event.get("issue") or payload.get("issue") or {}
                subtask = event.get("task") or payload.get("task") or {}
                subtask_agent = event.get("agent") or payload.get("agent") or {}
                is_leader_subtask = bool(subtask.get("is_leader")) or str(subtask_agent.get("role") or "") == "leader"
                add_event(
                    trace,
                    ts=ts,
                    task_id=task["id"],
                    round_id=task.get("round_id", ""),
                    actor=actor,
                    kind="platform" if is_leader_subtask else "child",
                    title="Leader task created" if is_leader_subtask else "创建子任务",
                    detail=str(issue.get("title") or event.get("content") or ""),
                    source="runtime-events",
                    confidence="exact",
                )
            elif kind.startswith("member:"):
                stage = kind.split(":", 1)[1]
                add_event(
                    trace,
                    ts=ts,
                    task_id=task["id"],
                    round_id=task.get("round_id", ""),
                    actor=actor,
                    kind="platform",
                    title=stage.replace("_", " "),
                    detail=_event_summary(event),
                    source="runtime-events",
                    confidence="exact",
                )
            elif kind in {"run:running", "run:completed", "final:message"}:
                if kind == "run:completed":
                    add_event(
                        trace,
                        ts=ts,
                        task_id=task["id"],
                        round_id=task.get("round_id", ""),
                        actor=actor,
                        kind="platform",
                        title="Run completed",
                        detail=_event_summary(event),
                        source="runtime-events",
                        confidence="exact",
                    )

        if active_event is not None:
            sandbox = active_event.setdefault("sandbox", {})
            sandbox["status"] = sandbox.get("status") or "running"
            sandbox["exit"] = "not captured"
            if active_pair is not None and not sandbox.get("result_output"):
                if _attach_message_pair(active_event, active_pair):
                    for index, candidate in enumerate(pairs):
                        if candidate is active_pair:
                            used_pairs.add(index)
                            break

        # Runtime events occasionally stop after tool start and never emit a
        # result. Backfill only from still-unused task-message pairs, in event
        # order, so the tool count and message pairing stay stable.
        task_tool_events = sorted(
            (item for item in trace["events"] if item.get("task_id") == task["id"] and item.get("kind") == "tool"),
            key=lambda item: item.get("ts") or "",
        )
        for tool_event in task_tool_events:
            sandbox = tool_event.get("sandbox") or {}
            if sandbox.get("result_output"):
                continue
            tool_name = str(sandbox.get("tool") or "tool")
            candidate_index = next(
                (
                    index for index, pair in enumerate(pairs)
                    if index not in used_pairs and str(pair.get("tool") or "tool") == tool_name
                ),
                None,
            )
            if candidate_index is None:
                candidate_index = next((index for index in range(len(pairs)) if index not in used_pairs), None)
            if candidate_index is None:
                continue
            used_pairs.add(candidate_index)
            _attach_message_pair(tool_event, pairs[candidate_index])
        if orphan_results:
            trace["gaps"].append(
                f"Task {task['id'][:8]} 有 {orphan_results} 个 runtime tool_result 没有配对的 tool_running；"
                "已保留为“工具结果（未配对）”事件，未计入工具调用数。"
            )

    # Root issue and issue-backed child topology. Leader task runs are lanes,
    # not topology nodes: one root issue may be claimed by several leader runs.
    root_title = title
    if root_issue_id and _is_uuid(root_issue_id):
        root_issue = load_issue(root_issue_id)
        if root_issue.get("title"):
            root_title = str(root_issue["title"])
    topology = [{"id": root_issue_id or "root", "title": root_title, "kind": "leader", "meta": "root issue"}]
    topo_issues: set[str] = set()
    for task in tasks:
        if task.get("kind") != "member":
            continue
        issue_id = str(task.get("issue_id") or "")
        if issue_id and issue_id in topo_issues:
            continue
        if issue_id:
            topo_issues.add(issue_id)
        issue_title = str(task.get("title") or task.get("agent_name") or task["id"])
        if issue_id and _is_uuid(issue_id):
            issue = load_issue(issue_id)
            issue_title = str(issue.get("title") or issue_title)
        topology.append({
            "id": task["id"],
            "title": issue_title,
            "kind": task["kind"],
            "meta": f"{task.get('agent_name') or 'agent'} · {task.get('status') or 'unknown'} · {round(float(task.get('duration_ms') or 0) / 1000.0, 1)}s",
            "issue_id": issue_id,
        })
    trace["topology"] = topology
    trace["events"].sort(key=lambda item: (item.get("ts") or "", item.get("id") or ""))
    trace["raw_evidence"] = redact_deep(raw_evidence)
    trace["raw_evidence"]["collection"]["counts"] = {
        "session_messages": len(messages),
        "run_events": sum(len(items) for items in run_events.values()),
        "tasks": len(task_details),
        "task_messages": sum(len(items) for items in task_messages.values()),
        "issues": len(issue_cache),
        "issue_comments": sum(len(items) for items in comment_cache.values()),
        "artifacts": len(trace.get("artifacts") or []),
    }
    add_provenance(
        trace,
        "raw-evidence",
        "session messages · run events · task details/messages · issues/comments · artifacts",
        trace["raw_evidence"]["collection"]["counts"]["run_events"],
    )
    return trace


def round_item_for_run(rounds: list[dict[str, Any]], run_id: str) -> dict[str, Any] | None:
    return next((item for item in rounds if item.get("run_id") == run_id), None)


def _guess_round_label(message: str, round_id: str) -> str:
    text = str(message or "").strip()
    if not text:
        return round_id
    if text.isdigit():
        return f"选择 {text}"
    if "确认回写" in text:
        return "确认回写"
    if text in {"确认", "确认。", "确认!"}:
        return "确认"
    return f"{round_id} · {excerpt(text, 18)}"


# ---------------------------------------------------------------------------
# Saved acceptance-artifact collector
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def collect_artifacts(directory: str) -> dict[str, Any]:
    root = Path(directory).expanduser().resolve()
    summary = _load_json(root / "summary.json")
    if not isinstance(summary, dict):
        raise RuntimeError(f"{root} 不是 acceptance artifact 目录：缺少 summary.json")
    timeline = _load_json(root / "timeline-root.json") or []
    child_topology = _load_json(root / "child-topology.json") or []
    probes: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json")):
        payload = _load_json(path)
        if isinstance(payload, dict) and payload.get("schema") == "device-ops-remote-probe/1":
            probes[str(payload.get("round_type") or "other")] = payload

    task_files: list[dict[str, Any]] = []
    for path in sorted(root.glob("task-*.json")):
        payload = _load_json(path)
        if isinstance(payload, dict) and payload.get("id"):
            task_files.append(payload)

    session_id = str(summary.get("session_id") or root.name)
    first_child_title = next(
        (str(item.get("title") or "") for item in (summary.get("children") or []) if isinstance(item, dict)),
        "",
    )
    title = str(summary.get("title") or summary.get("session_title") or first_child_title or session_id)
    trace = new_trace({
        "kind": "squad_session",
        "id": session_id,
        "title": title,
        "environment": str(summary.get("environment") or "unknown"),
        "workspace": str(summary.get("workspace") or ""),
        "squad_id": str(summary.get("squad_id") or ""),
        "root_issue_id": str(summary.get("root_issue_id") or ""),
        "status": "completed" if summary.get("max_round_seconds") else "unknown",
    })
    trace["raw_evidence"] = redact_deep({
        "source": "acceptance-artifacts",
        "directory": str(root),
        "summary": summary,
        "timeline_root": timeline,
        "child_topology": child_topology,
        "probes": probes,
        "task_files": task_files,
        "collection": {"counts": {"task_files": len(task_files), "probes": len(probes)}},
    })
    add_provenance(trace, "acceptance-artifacts", f"{root}", 1)

    rounds = []
    round_task_ids: dict[str, str] = {}
    for index, item in enumerate(summary.get("rounds") or []):
        round_type = str(item.get("round") or "other")
        probe = probes.get(round_type, {})
        start = probe.get("sent_at") or summary.get("started_at") or "1970-01-01T00:00:00+00:00"
        total = float((item.get("timings") or {}).get("total_seconds") or 0.0)
        end = iso_ts(parse_ts(start) + _dt.timedelta(seconds=total)) if parse_ts(start) else ""
        task_id = str(item.get("task_id") or "")
        rid = _round_id(index)
        rounds.append({
            "id": rid,
            "label": ROUND_LABELS.get(round_type, round_type),
            "user_message": round_type,
            "assistant_message": str(probe.get("final_assistant_message") or ""),
            "start": start,
            "end": end,
            "duration_ms": round(total * 1000),
            "task_id": task_id,
            "run_id": str(item.get("run_id") or ""),
            "status": str(item.get("terminal_status") or "completed"),
            "timing_source": "acceptance-artifact",
        })
        if task_id:
            round_task_ids[task_id] = rid
    trace["rounds"] = rounds

    tasks: list[dict[str, Any]] = []
    known = set()
    root_issue_id = str(summary.get("root_issue_id") or "")
    child_titles = {
        str(item.get("task_id") or ""): str(item.get("title") or "")
        for item in (summary.get("children") or [])
        if isinstance(item, dict) and item.get("task_id")
    }
    for task in task_files:
        task_id = str(task.get("id") or "")
        known.add(task_id)
        tasks.append({
            "id": task_id,
            "kind": "leader" if task_id in round_task_ids or str(task.get("source_ref") or "") == root_issue_id else "member",
            "title": child_titles.get(task_id) or str(task.get("title") or task.get("agent_name") or "task"),
            "agent_id": str(task.get("agent_id") or ""),
            "agent_name": str(task.get("agent_name") or "agent"),
            "status": str(task.get("status") or "unknown"),
            "issue_id": str(task.get("source_ref") or ""),
            "parent_task_id": str(task.get("parent_task_id") or ""),
            "runtime_name": str(task.get("runtime_name") or ""),
            "created_at": task.get("created_at"),
            "claimed_at": task.get("claimed_at"),
            "started_at": task.get("started_at"),
            "completed_at": task.get("completed_at"),
            "duration_ms": task.get("duration_ms"),
            "round_id": round_task_ids.get(task_id, ""),
            "source": "acceptance-artifacts",
        })
    for item in summary.get("rounds") or []:
        task_id = str(item.get("task_id") or "")
        if task_id and task_id not in known:
            timings = item.get("timings") or {}
            start = probes.get(str(item.get("round")), {}).get("sent_at") or rounds[len(tasks) % len(rounds)]["start"]
            claimed = iso_ts(parse_ts(start) + _dt.timedelta(seconds=float(timings.get("claim_seconds") or 0))) if parse_ts(start) else start
            started = iso_ts(parse_ts(claimed) + _dt.timedelta(seconds=float(timings.get("start_after_claim_seconds") or 0))) if parse_ts(claimed) else claimed
            completed = iso_ts(parse_ts(started) + _dt.timedelta(seconds=float(timings.get("exec_seconds") or 0))) if parse_ts(started) else started
            tasks.append({
                "id": task_id,
                "kind": "leader",
                "agent_name": "Coordinator",
                "status": str(item.get("terminal_status") or "completed"),
                "created_at": start,
                "claimed_at": claimed,
                "started_at": started,
                "completed_at": completed,
                "duration_ms": round(float(timings.get("total_seconds") or 0) * 1000),
                "round_id": round_task_ids.get(task_id, ""),
                "source": "acceptance-artifacts",
            })
    trace["tasks"] = tasks

    for round_item in rounds:
        add_event(trace, ts=round_item.get("start"), round_id=round_item["id"], actor="user", kind="user", title="用户消息", detail=round_item.get("user_message", ""), source="acceptance-artifacts")
        add_event(trace, ts=round_item.get("end"), round_id=round_item["id"], actor="Coordinator", kind="assistant", title="最终回复", detail=excerpt(round_item.get("assistant_message", ""), 260), source="acceptance-artifacts")

    for task in tasks:
        _append_task_lifecycle(trace, task, "acceptance-artifacts")
    for task in task_files:
        task_id = str(task.get("id") or "")
        for pair in _tool_messages(task.get("messages") or []):
            use_row = pair.get("use") or {}
            result_row = pair.get("result") or {}
            ts = use_row.get("created_at") or result_row.get("created_at") or task.get("started_at")
            add_event(
                trace,
                ts=ts,
                end_ts=result_row.get("created_at"),
                task_id=task_id,
                round_id=round_task_ids.get(task_id, ""),
                actor=str(task.get("agent_name") or "agent"),
                kind="tool",
                title=f"工具 · {pair.get('tool') or 'tool'}",
                detail=excerpt(result_row.get("content") or ""),
                source="acceptance-artifacts",
                confidence="exact",
                sandbox={"cmd": excerpt(use_row.get("content") or "", 400), "cwd": "", "io": excerpt(result_row.get("content") or "", 220), "files": [], "network": [], "exit": "unknown", "result_observed": bool(result_row)},
            )
    for event in timeline if isinstance(timeline, list) else []:
        if not isinstance(event, dict):
            continue
        details = event.get("details") or {}
        task_id = str(details.get("task_id") or "")
        if not task_id:
            continue
        action = str(event.get("action") or "")
        if action in {"task_queued", "task_completed", "status_changed"}:
            add_event(
                trace,
                ts=event.get("created_at"),
                task_id=task_id,
                actor=str(details.get("agent_id") or "system"),
                kind="platform",
                title=action.replace("_", " "),
                detail=str(details.get("reason") or details.get("to") or ""),
                source="acceptance-artifacts",
            )
    trace["topology"] = [
        {"id": "root", "title": title, "kind": "leader", "meta": "root issue"},
        *[
            {"id": str(item.get("task_id") or ""), "title": str(item.get("title") or ""), "kind": "member", "meta": f"{item.get('agent_name') or 'agent'} · {round(float(item.get('duration_ms') or 0) / 1000.0, 1)}s", "issue_id": str(item.get("issue_id") or "")}
            for item in (child_topology if isinstance(child_topology, list) else [])
        ],
    ]
    trace["events"].sort(key=lambda item: (item.get("ts") or "", item.get("id") or ""))
    return trace


# ---------------------------------------------------------------------------
# Local opencode.db collector
# ---------------------------------------------------------------------------

def local_db_candidates(env_name: str) -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
        names = {
            "prod": "com.deepexi.deepworks/deepworks-engine-data/xdg/data/opencode/opencode.db",
            "test": "com.deepexi.deepworks.test/deepworks-engine-data/xdg/data/opencode/opencode.db",
            "dev": "com.deepexi.deepworks.test.dev/deepworks-dev-data/xdg/data/opencode/opencode.db",
        }
        candidates.append(base / names.get(env_name, names["prod"]))
    elif os.name == "posix" and os.uname().sysname == "Darwin":
        base = home / "Library/Application Support"
        names = {
            "prod": "com.deepexi.deepworks/deepworks-engine-data/xdg/data/opencode/opencode.db",
            "test": "com.deepexi.deepworks.test/deepworks-engine-data/xdg/data/opencode/opencode.db",
            "dev": "com.deepexi.deepworks.test.dev/deepworks-dev-data/xdg/data/opencode/opencode.db",
        }
        candidates.append(base / names.get(env_name, names["prod"]))
    else:
        candidates.extend([
            home / ".local/share/opencode/opencode.db",
            home / ".config/opencode/opencode.db",
        ])
    return candidates


def collect_local(session_id: str, *, env_name: str = "prod", db_path: str = "") -> dict[str, Any]:
    path = Path(db_path).expanduser() if db_path else next((item for item in local_db_candidates(env_name) if item.is_file()), None)
    if path is None or not path.is_file():
        raise RuntimeError("找不到 opencode.db；用 --db 指定，或确认 DeepWorks 桌面端已产生本地会话。")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM session WHERE id=?", (session_id,)).fetchone()
    if row is None:
        con.close()
        raise RuntimeError(f"本地 opencode.db 没有 session {session_id}")
    messages = []
    for msg_row in con.execute("SELECT id,time_created,data FROM message WHERE session_id=? ORDER BY time_created", (session_id,)):
        try:
            data = json.loads(msg_row["data"])
        except Exception:
            continue
        parts = []
        for part_row in con.execute("SELECT time_created,data FROM part WHERE message_id=? ORDER BY time_created", (msg_row["id"],)):
            try:
                parts.append((part_row["time_created"], json.loads(part_row["data"])))
            except Exception:
                continue
        messages.append({"id": msg_row["id"], "created_at": iso_ts(_dt.datetime.fromtimestamp(msg_row["time_created"] / 1000, _dt.timezone.utc)), "data": data, "parts": parts})
    con.close()
    title = str(row["title"] or session_id)
    trace = new_trace({
        "kind": "agent_session",
        "id": session_id,
        "title": title,
        "environment": env_name,
        "workspace": str(row["directory"] or ""),
        "squad_id": "",
        "agent_id": "",
        "root_issue_id": "",
        "status": "completed",
    })
    trace["raw_evidence"] = redact_deep({
        "source": "local-db",
        "database": str(path),
        "session": dict(row),
        "messages": messages,
        "collection": {"counts": {"messages": len(messages), "parts": sum(len(item["parts"]) for item in messages)}},
    })
    add_provenance(trace, "local-db", str(path), 1)
    rounds = []
    task_by_round = {}
    current = None
    for index, message in enumerate(messages):
        role = str(message["data"].get("role") or "")
        text = " ".join(
            str(part.get("text") or "")
            for _, part in message["parts"]
            if part.get("type") == "text" and not part.get("synthetic")
        ).strip()
        if role == "user":
            if current is not None:
                current["status"] = "completed"
            current = {
                "id": _round_id(len(rounds)),
                "label": _guess_round_label(text, _round_id(len(rounds))),
                "user_message": text,
                "assistant_message": "",
                "start": message["created_at"],
                "end": "",
                "duration_ms": None,
                "task_id": f"local-task-{len(rounds) + 1}",
                "run_id": "",
                "status": "in_progress",
                "timing_source": "local-db",
            }
            rounds.append(current)
            task_by_round[current["id"]] = {
                "id": current["task_id"],
                "kind": "leader",
                "agent_name": str(message["data"].get("agent") or "local agent"),
                "status": "completed",
                "created_at": current["start"],
                "started_at": current["start"],
                "round_id": current["id"],
                "source": "local-db",
            }
        elif role == "assistant" and current is not None:
            if text:
                current["assistant_message"] = (
                    f"{current['assistant_message']}\n{text}".strip()
                    if current["assistant_message"] else text
                )
            part_times = [
                iso_ts(_dt.datetime.fromtimestamp(part_ts / 1000, _dt.timezone.utc))
                for part_ts, _ in message["parts"]
            ]
            current["end"] = max([message["created_at"], *part_times])
            current["status"] = "completed"
            current["duration_ms"] = _duration_ms(current["start"], current["end"])
            task = task_by_round[current["id"]]
            task["completed_at"] = current["end"]
            task["duration_ms"] = current["duration_ms"]
    if current is not None:
        current["status"] = "completed"
        if current.get("duration_ms") is None:
            current["duration_ms"] = _duration_ms(current.get("start"), current.get("end"))
    trace["rounds"] = rounds
    trace["tasks"] = list(task_by_round.values())
    for message in messages:
        role = str(message["data"].get("role") or "")
        agent = str(message["data"].get("agent") or "local agent")
        text = " ".join(str(part.get("text") or "") for _, part in message["parts"] if part.get("type") == "text" and not part.get("synthetic")).strip()
        message_ts = parse_ts(message["created_at"])
        round_item = next(
            (
                item for item in rounds
                if parse_ts(item.get("start")) <= message_ts <= parse_ts(item.get("end"))
            ),
            None,
        ) if message_ts is not None else None
        if round_item is None and rounds:
            previous = [item for item in rounds if parse_ts(item.get("start")) <= message_ts]
            round_item = previous[-1] if previous else None
        rid = round_item["id"] if round_item else ""
        if text:
            add_event(trace, ts=message["created_at"], round_id=rid, actor="user" if role == "user" else agent, kind="user" if role == "user" else "assistant", title="用户消息" if role == "user" else "模型输出", detail=excerpt(text, 260), source="local-db")
        for part_ts, part in message["parts"]:
            if part.get("type") != "tool":
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            payload = state.get("input") or {}
            if not isinstance(payload, dict):
                payload = {"input": payload}
            command = payload.get("command") or payload.get("cmd") or payload.get("input") or ""
            output = state.get("output") or ""
            ts = iso_ts(_dt.datetime.fromtimestamp(part_ts / 1000, _dt.timezone.utc))
            output_text = redact(str(output))
            exit_code = _extract_exit_code(output_text)
            add_event(
                trace,
                ts=ts,
                task_id=(round_item or {}).get("task_id", ""),
                round_id=rid,
                actor=agent,
                kind="tool",
                title=f"工具 · {part.get('tool') or 'tool'}",
                detail=excerpt(output_text, 240),
                source="local-db",
                confidence="exact",
                sandbox={
                    "cmd": redact(str(command)),
                    "cmd_excerpt": excerpt(redact(str(command)), 240),
                    "cwd": "",
                    "io": excerpt(output_text, 400),
                    "result_output": output_text,
                    "files": [],
                    "network": [],
                    "status": str(state.get("status") or "unknown"),
                    "exit": exit_code or "not captured",
                    "result_observed": True,
                },
            )
    trace["topology"] = [{"id": "local-root", "title": title, "kind": "leader", "meta": "local opencode session"}]
    trace["events"].sort(key=lambda item: (item.get("ts") or "", item.get("id") or ""))
    return trace
