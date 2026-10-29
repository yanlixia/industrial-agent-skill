"""Deterministic metrics and findings for a session trace."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any

from .model import add_event, excerpt, iso_ts, parse_ts, redact, seconds_between

EXPLORATION_MARKERS = (
    "ls ",
    " ls",
    "--help",
    " grep ",
    " rg ",
    "cat ",
    "sed -n",
    "head ",
    "tail ",
    "find ",
    "pwd",
    "task.json",
)


def _event_seconds(event: dict[str, Any]) -> tuple[float | None, float | None]:
    start, end = parse_ts(event.get("ts")), parse_ts(event.get("end_ts"))
    if start is None:
        return None, None
    if end is None:
        return 0.0, None
    return 0.0, (end - start).total_seconds()


def _round_duration(round_item: dict[str, Any]) -> float:
    if round_item.get("duration_ms") is not None:
        return round(float(round_item["duration_ms"]) / 1000.0, 3)
    value = seconds_between(round_item.get("start"), round_item.get("end"))
    return float(value or 0.0)


def _command_of(event: dict[str, Any]) -> str:
    sandbox = event.get("sandbox") or {}
    return str(sandbox.get("cmd") or sandbox.get("command") or event.get("detail") or "")


def _active_seconds(tasks: list[dict[str, Any]], events: list[dict[str, Any]]) -> float:
    """Union of task/model/tool spans, used as a lower-bound active time."""
    intervals: list[tuple[float, float]] = []
    for task in tasks:
        start, end = parse_ts(task.get("started_at")), parse_ts(task.get("completed_at"))
        if start is not None and end is not None and end >= start:
            intervals.append((start.timestamp(), end.timestamp()))
    for event in events:
        start, end = parse_ts(event.get("ts")), parse_ts(event.get("end_ts"))
        if start is not None and end is not None and end >= start:
            intervals.append((start.timestamp(), end.timestamp()))
    if not intervals:
        return 0.0
    intervals.sort()
    merged: list[list[float]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return round(sum(end - start for start, end in merged), 3)


def _finding(severity: str, title: str, detail: str, evidence: str) -> dict[str, str]:
    return {
        "severity": severity,
        "title": title,
        "detail": detail,
        "evidence": evidence,
    }


ERROR_RULES: tuple[tuple[str, str, str, re.Pattern[str]], ...] = (
    (
        "cli_invalid_choice",
        "warning",
        "CLI 参数错误",
        re.compile(r"invalid choice:\s*'?([^'\n]+)'?", re.I),
    ),
    (
        "foil_auth",
        "error",
        "Foil 授权失败",
        re.compile(r"Foil\s*授权失败|Workbench product-user authentication is incomplete", re.I),
    ),
    (
        "traceback",
        "error",
        "Python Traceback",
        re.compile(r"Traceback \(most recent call last\)"),
    ),
    (
        "module_not_found",
        "error",
        "缺少 Python 模块",
        re.compile(r"ModuleNotFoundError|ImportError: No module named", re.I),
    ),
    (
        "http_error",
        "error",
        "HTTP 4xx/5xx",
        re.compile(
            r"\bHTTP(?:/[\d.]+)?\s*(?:4\d\d|5\d\d)\b"
            r"|\b(?:4\d\d|5\d\d)\s+(?:Bad Request|Unauthorized|Forbidden|Not Found|Internal Server Error|Bad Gateway|Service Unavailable)\b"
            r"|\bstatus(?:_code| code)?\s*[:=]\s*(?:4\d\d|5\d\d)\b",
            re.I,
        ),
    ),
    (
        "generic_error",
        "warning",
        "执行错误",
        re.compile(
            r"(?im)^\s*(?:error|failed|failure|fatal)\s*:\s*(.+)$"
            r"|\b(?:command|request|tool|run|operation)\s+failed\b"
            r"|(?:调用|执行|请求|命令|运行)?失败\s*[:：]",
        ),
    ),
)


def _candidate_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = row.get(key)
        if value:
            if isinstance(value, dict):
                value = value.get("output") or value.get("content") or ""
            if value:
                return redact(str(value))
    return ""


def _error_candidates(trace: dict[str, Any]) -> list[dict[str, Any]]:
    raw = trace.get("raw_evidence") or {}
    tasks = {str(item.get("id") or ""): item for item in (trace.get("tasks") or [])}
    rounds = trace.get("rounds") or []
    round_by_task: dict[str, str] = {
        task_id: str(task.get("round_id") or "")
        for task_id, task in tasks.items()
    }
    round_by_issue: dict[str, str] = {}
    for task in tasks.values():
        issue_id = str(task.get("issue_id") or "")
        if issue_id and task.get("round_id"):
            round_by_issue.setdefault(issue_id, str(task.get("round_id")))
    for item in rounds:
        for key in ("task_id", "final_task_id"):
            task_id = str(item.get(key) or "")
            if task_id:
                round_by_task.setdefault(task_id, str(item.get("id") or ""))

    result: list[dict[str, Any]] = []

    def add(
        text: str,
        *,
        ts: Any = "",
        task_id: str = "",
        issue_id: str = "",
        actor: str = "runtime",
        source: str = "",
        ref: str = "",
        exact: bool = False,
        command: str = "",
    ) -> None:
        text = redact(str(text or ""))
        if len(text.strip()) < 5:
            return
        task = tasks.get(task_id) or {}
        result.append({
            "text": text,
            "ts": ts,
            "task_id": task_id,
            "round_id": str(task.get("round_id") or round_by_task.get(task_id) or round_by_issue.get(issue_id) or ""),
            "actor": actor or str(task.get("agent_name") or "runtime"),
            "source": source,
            "ref": ref,
            "exact": bool(exact and ts),
            "command": redact(command),
        })

    for run_id, events in (raw.get("run_events") or {}).items():
        for event in events or []:
            kind = str(event.get("type") or "")
            if kind in {"member:tool_running", "member:thinking", "member:execution_heartbeat", "member:progress"}:
                continue
            text = _candidate_text(event, ("output", "content", "summary"))
            if not text:
                text = _candidate_text(event.get("payload") or {}, ("output", "content"))
            if not text:
                continue
            command = ""
            payload = event.get("input") or (event.get("message") or {}).get("input") or {}
            if isinstance(payload, dict):
                command = str(payload.get("command") or payload.get("cmd") or "")
            add(
                text,
                ts=event.get("created_at"),
                task_id=str(event.get("task_id") or ""),
                issue_id=str(event.get("issue_id") or ""),
                actor=_agent_or_runtime(event),
                source="runtime-events",
                ref=f"run:{run_id}:seq:{event.get('sequence') or event.get('id') or '?'}",
                exact=True,
                command=command,
            )

    for task_id, rows in (raw.get("task_messages") or {}).items():
        for row in rows or []:
            kind = str(row.get("event_type") or row.get("type") or "")
            if kind not in {"tool_result", "text"}:
                continue
            text = _candidate_text(row, ("output", "content"))
            if not text:
                continue
            payload = row.get("input") or {}
            command = str(payload.get("command") or payload.get("cmd") or "") if isinstance(payload, dict) else ""
            add(
                text,
                task_id=str(task_id),
                source="task-messages",
                ref=f"task:{task_id}:seq:{row.get('seq') or '?'}",
                command=command,
            )

    for task_id, detail in (raw.get("task_details") or {}).items():
        if not isinstance(detail, dict):
            continue
        output = detail.get("output") or {}
        final_reply = output.get("final_reply") if isinstance(output, dict) else ""
        add(
            str(final_reply or ""),
            ts=detail.get("completed_at") or detail.get("started_at"),
            task_id=str(task_id),
            actor=str(detail.get("agent_name") or "runtime"),
            source="task-details",
            ref=f"task:{task_id}:output.final_reply",
        )
        add(
            str(detail.get("failure_reason") or ""),
            ts=detail.get("completed_at") or detail.get("started_at"),
            task_id=str(task_id),
            actor=str(detail.get("agent_name") or "runtime"),
            source="task-details",
            ref=f"task:{task_id}:failure_reason",
        )

    for issue_id, comments in (raw.get("issue_comments") or {}).items():
        for index, comment in enumerate(comments or []):
            add(
                str(comment.get("content") or ""),
                ts=comment.get("created_at"),
                issue_id=str(issue_id),
                actor=str(comment.get("author_id") or "issue"),
                source="issue-comments",
                ref=f"issue:{issue_id}:comment:{comment.get('id') or index}",
                exact=True,
            )

    for message in raw.get("session_messages") or []:
        if str(message.get("role") or "") != "assistant":
            continue
        add(
            str(message.get("content") or ""),
            ts=message.get("created_at"),
            task_id=str(message.get("task_id") or ""),
            actor="assistant",
            source="session-messages",
            ref=f"session-message:{message.get('id') or '?'}",
            exact=True,
        )
    return result


def _agent_or_runtime(event: dict[str, Any]) -> str:
    agent = event.get("agent") or (event.get("payload") or {}).get("agent") or {}
    if isinstance(agent, dict):
        return str(agent.get("name") or agent.get("agent_name") or "runtime")
    return str(agent or "runtime")


def _matching_line(text: str, pattern: re.Pattern[str]) -> str:
    for line in text.splitlines():
        if pattern.search(line):
            return line.strip()
    match = pattern.search(text)
    return match.group(0).strip() if match else ""


def _is_inspection_command(command: str) -> bool:
    """Source-reading commands often print strings that look like errors.

    A grep/sed/cat result containing `raise ...Error(...)` is evidence from the
    code, not evidence that the command failed. Skip those outputs so the error
    panel only reflects runtime failures. Commands that both inspect and execute
    (for example `mkdir && python3 ...`) are not treated as inspection.
    """

    text = str(command or "").strip().lower()
    if not text:
        return False
    if re.search(r"\b(?:python3?|node|bash)\b", text):
        return False
    return bool(re.search(
        r"(?:^|[\s\"'`;|&])(?:sed\s+-n|grep\s|rg\s|cat\s|head\s|tail\s|find\s|ls\s|wc\s|stat\s|file\s)",
        text,
    ))


def _detect_errors(trace: dict[str, Any]) -> list[dict[str, Any]]:
    trace["events"] = [
        event for event in (trace.get("events") or [])
        if not (event.get("kind") == "error" and (event.get("error") or {}).get("detected_by") == "storylib")
    ]
    tasks = {str(item.get("id") or ""): item for item in (trace.get("tasks") or [])}
    chosen: dict[str, dict[str, Any]] = {}
    for candidate in _error_candidates(trace):
        if _is_inspection_command(candidate.get("command") or ""):
            continue
        text = candidate["text"]
        matches = [rule for rule in ERROR_RULES if rule[3].search(text)]
        if not matches:
            continue
        category, severity, label, pattern = matches[0]
        line = _matching_line(text, pattern) or excerpt(text, 400)
        signature_text = " ".join(text.split())[:400]
        signature = hashlib.sha1(
            f"{candidate['task_id']}|{category}|{signature_text}".encode("utf-8")
        ).hexdigest()
        item = {
            **candidate,
            "category": category,
            "severity": severity,
            "label": label,
            "line": redact(line)[:1200],
            "matches": [rule[0] for rule in matches],
            "signature": signature,
        }
        previous = chosen.get(signature)
        if previous is None:
            chosen[signature] = item
            continue
        previous["matches"] = list(dict.fromkeys(previous["matches"] + item["matches"]))
        if item["exact"] and not previous["exact"]:
            item["evidence_refs"] = list(dict.fromkeys(previous.get("evidence_refs", []) + [item["ref"]]))
            chosen[signature] = item
        else:
            previous.setdefault("evidence_refs", []).append(item["ref"])

    errors: list[dict[str, Any]] = []
    window_start = (trace.get("window") or {}).get("start") or ""
    for item in sorted(chosen.values(), key=lambda value: (value.get("ts") or window_start, value["category"])):
        task = tasks.get(item["task_id"]) or {}
        ts = item.get("ts") or task.get("started_at") or task.get("completed_at") or window_start
        confidence = "exact" if item.get("exact") else "derived" if task else "inferred"
        event = add_event(
            trace,
            ts=ts,
            task_id=item["task_id"],
            round_id=item["round_id"],
            actor=item["actor"],
            kind="error",
            title=f"错误 · {item['label']}",
            detail=f"[{item['severity']}] {item['line']}",
            status=item["severity"],
            source=item["source"],
            confidence=confidence,
            evidence_refs=list(dict.fromkeys([item["ref"], *item.get("evidence_refs", [])])),
        )
        event["error"] = {
            "detected_by": "storylib",
            "category": item["category"],
            "severity": item["severity"],
            "label": item["label"],
            "matches": item["matches"],
            "line": item["line"],
            "summary": excerpt(item["text"], 1200),
            "evidence_ref": item["ref"],
            "inferred_ts": not item.get("exact"),
        }
        if item.get("command"):
            event["sandbox"] = {
                "cmd": item["command"],
                "cmd_excerpt": excerpt(item["command"], 240),
                "cwd": "",
                "io": excerpt(item["text"], 400),
                "result_output": item["text"],
                "files": [],
                "network": [],
                "status": "error",
                "exit": "not captured",
                "result_observed": True,
            }
        errors.append(event)
    trace["events"].sort(key=lambda event: (event.get("ts") or "", event.get("id") or ""))
    trace["errors"] = errors
    return errors


def enrich_trace(trace: dict[str, Any], *, budget_seconds: float = 120.0) -> dict[str, Any]:
    rounds = trace.get("rounds") or []
    tasks = trace.get("tasks") or []
    events = trace.get("events") or []
    for item in rounds:
        if item.get("start"):
            item["start"] = iso_ts(item["start"])
        if item.get("end"):
            item["end"] = iso_ts(item["end"])
    for task in tasks:
        for key in ("created_at", "claimed_at", "started_at", "completed_at"):
            if task.get(key):
                task[key] = iso_ts(task[key])
    for item in rounds:
        item["duration_ms"] = round(_round_duration(item) * 1000)

    starts = [parse_ts(item.get("start")) for item in rounds if parse_ts(item.get("start"))]
    ends = [parse_ts(item.get("end")) for item in rounds if parse_ts(item.get("end"))]
    if str((trace.get("target") or {}).get("status") or "") == "in_progress":
        observed = list(ends)
        for task in tasks:
            for key in ("created_at", "claimed_at", "started_at", "completed_at"):
                parsed = parse_ts(task.get(key))
                if parsed is not None:
                    observed.append(parsed)
        for event in events:
            for key in ("ts", "end_ts"):
                parsed = parse_ts(event.get(key))
                if parsed is not None:
                    observed.append(parsed)
        if observed:
            ends = [max(observed)]
    window_start = min(starts) if starts else None
    window_end = max(ends) if ends else None
    if window_start and window_end:
        trace["window"] = {
            "start": window_start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "end": window_end.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "duration_ms": round((window_end - window_start).total_seconds() * 1000),
        }
    else:
        trace["window"] = {"start": "", "end": "", "duration_ms": None}

    _detect_errors(trace)
    events = trace.get("events") or []

    round_durations = [_round_duration(item) for item in rounds]
    max_round = max(round_durations, default=None)
    tool_events = [event for event in events if event.get("kind") in {"tool", "sandbox"}]
    external_events = [event for event in events if event.get("kind") == "api"]
    exploration = [
        event for event in tool_events
        if any(marker in _command_of(event).lower() for marker in EXPLORATION_MARKERS)
    ]
    side_effects = trace.get("side_effects") or []
    effect_keys = [str(item.get("idempotency_key") or "") for item in side_effects if item.get("idempotency_key")]
    duplicate_effects = sum(count - 1 for count in Counter(effect_keys).values() if count > 1)
    wall_seconds = round(float(trace["window"]["duration_ms"] or 0) / 1000.0, 3)
    round_sum = round(sum(round_durations), 3)
    active_seconds = _active_seconds(tasks, events)
    leader_runs = sum(1 for task in tasks if task.get("kind") == "leader")
    member_runs = sum(1 for task in tasks if task.get("kind") in {"member", "child"})
    unique_agents = len({
        str(task.get("agent_name") or "")
        for task in tasks
        if task.get("agent_name")
    })
    member_issue_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        issue_id = str(task.get("issue_id") or "")
        if issue_id and task.get("kind") in {"member", "child"}:
            member_issue_runs[issue_id].append(task)
    repeated_issue_runs = {
        issue_id: len(items)
        for issue_id, items in member_issue_runs.items()
        if len(items) > 1
    }
    auxiliary_leader_runs = sum(
        1 for task in tasks
        if task.get("kind") == "leader" and not task.get("round_id")
    )
    between_round_gap = round(max(0.0, wall_seconds - round_sum), 3)
    tools_with_end = sum(1 for event in tool_events if event.get("end_ts"))
    tools_with_cwd = sum(1 for event in tool_events if (event.get("sandbox") or {}).get("cwd"))
    tools_with_exit = sum(
        1 for event in tool_events
        if str((event.get("sandbox") or {}).get("exit") or "").lower() not in {"", "unknown", "not captured"}
    )
    tools_with_output = sum(1 for event in tool_events if (event.get("sandbox") or {}).get("result_output"))
    tools_with_result = sum(1 for event in tool_events if (event.get("sandbox") or {}).get("result_observed"))
    error_events = trace.get("errors") or []

    trace["metrics"] = {
        "wall_seconds": wall_seconds,
        "round_sum_seconds": round_sum,
        "active_seconds": active_seconds,
        "idle_seconds": round(max(0.0, wall_seconds - active_seconds), 3),
        "between_round_gap_seconds": between_round_gap,
        "round_count": len(rounds),
        "max_round_seconds": max_round,
        "round_seconds": round_durations,
        "task_count": len(tasks),
        "leader_run_count": leader_runs,
        "member_task_count": member_runs,
        "unique_agent_count": unique_agents,
        "tool_calls": len(tool_events),
        "exploration_calls": len(exploration),
        "tools_with_end": tools_with_end,
        "tools_with_cwd": tools_with_cwd,
        "tools_with_exit": tools_with_exit,
        "tools_with_output": tools_with_output,
        "tools_with_result": tools_with_result,
        "error_events": len(error_events),
        "error_categories": dict(Counter(str((event.get("error") or {}).get("category") or "unknown") for event in error_events)),
        "external_calls": len(external_events),
        "artifact_count": len(trace.get("artifacts") or []) if trace.get("artifacts") else None,
        "duplicate_effects": duplicate_effects if effect_keys else None,
        "repeated_issue_run_count": len(repeated_issue_runs),
        "max_repeated_issue_runs": max(repeated_issue_runs.values(), default=0),
        "auxiliary_leader_runs": auxiliary_leader_runs,
        "budget_seconds": budget_seconds,
        "over_budget_rounds": sum(1 for value in round_durations if value > budget_seconds),
    }

    findings: list[dict[str, Any]] = []
    if max_round is not None and max_round > budget_seconds:
        findings.append(_finding(
            "warn",
            "单轮超过预算",
            f"最慢一轮 {max_round:.1f}s，超过 {budget_seconds:.0f}s 预算。",
            "rounds",
        ))
    if max_round is not None and len(round_durations) > 1:
        findings.append(_finding(
            "info",
            "Wall time 与轮次累加是两个口径",
            f"会话首尾跨度 {wall_seconds:.1f}s；各轮耗时累加 {round_sum:.1f}s，"
            f"轮间空闲约 {between_round_gap:.1f}s。"
            f"比较 {budget_seconds:.0f}s 预算时应看单轮最大值 {max_round:.1f}s。",
            "rounds",
        ))
    if exploration:
        findings.append(_finding(
            "warn",
            "出现探索型工具调用",
            f"检测到 {len(exploration)} 次疑似 ls/help/grep/task.json/源码读取调用；"
            "该判断是文本启发式，需点击事件核对。",
            "events",
        ))
    if any(not event.get("end_ts") for event in tool_events):
        findings.append(_finding(
            "info",
            "部分工具调用只有顺序没有精确结束时间",
            "平台消息表记录了调用顺序，但未记录每一次工具调用的独立起止时间；"
            "这些 span 标为推断。",
            "events",
        ))
    if not side_effects:
        findings.append(_finding(
            "info",
            "副作用不可判定",
            "本会话没有独立副作用账本；0 个 API 事件只代表“未观测到”，"
            "不能据此断言没有调用外部 API 或写工单。",
            "side_effects",
        ))
    if not tasks:
        findings.append(_finding(
            "warn",
            "没有 Task 级数据",
            "只能还原会话消息，无法拆分平台启动、成员 hop 和沙箱阶段。",
            "tasks",
        ))
    if repeated_issue_runs:
        examples = sorted(repeated_issue_runs.items(), key=lambda item: item[1], reverse=True)
        sample = "、".join(f"{issue[:8]}={count} runs" for issue, count in examples[:3])
        findings.append(_finding(
            "warn",
            "同一 issue 出现多个 Task run",
            f"{len(repeated_issue_runs)} 个 issue 被多个 Task run 处理（{sample}）。"
            "这通常意味着 continuation；兼容性处理必须按 run_id 比较，避免把旧 continuation 计成关键路径。",
            "tasks",
        ))
    elif auxiliary_leader_runs:
        findings.append(_finding(
            "info",
            "Leader 出现额外 claim",
            f"{leader_runs} 个 leader Task run 中，有 {auxiliary_leader_runs} 个没有映射到用户轮次。"
            "这些通常是 continuation/续跑；必须比较 run_id 和幂等键，确认它们没有重复触发副作用。",
            "tasks",
        ))
    if member_runs:
        member_seconds = [
            float(task.get("duration_ms") or 0) / 1000.0
            for task in tasks
            if task.get("kind") in {"member", "child"} and task.get("duration_ms") is not None
        ]
        if member_seconds:
            avg_member = sum(member_seconds) / len(member_seconds)
            findings.append(_finding(
                "info",
                "成员 Task 地板成本",
                f"{member_runs} 个成员 Task，平均 {avg_member:.1f}s。"
                "先用 Task 级耗时拆分 claim/start/执行，再判断脚本本身是否慢；"
                "不要按整轮耗时优化确定性命令。",
                "tasks",
            ))
    if rounds and max_round is not None:
        slow_round = max(rounds, key=lambda item: _round_duration(item))
        slow_start = parse_ts(slow_round.get("start"))
        slow_end = parse_ts(slow_round.get("end"))
        completed_in_round = [
            parse_ts(task.get("completed_at"))
            for task in tasks
            if (
                parse_ts(task.get("completed_at")) is not None
                and slow_start is not None
                and slow_start <= parse_ts(task.get("completed_at"))
                and slow_end is not None
                and parse_ts(task.get("completed_at")) <= slow_end
                and task.get("kind") in {"member", "child"}
            )
        ]
        if slow_end is not None and completed_in_round:
            tail = (slow_end - max(completed_in_round)).total_seconds()
            if tail >= 5:
                findings.append(_finding(
                    "info",
                    "最慢轮次的执行尾部",
                    f"{slow_round.get('id')} 在最后一个 Task 完成后仍有约 {tail:.1f}s，"
                    "用于 leader 汇总、校验和最终回复。优化时把这段与成员执行分开计量。",
                    "rounds/tasks",
                ))
    if error_events:
        categories = Counter(str((event.get("error") or {}).get("category") or "unknown") for event in error_events)
        sample = "、".join(f"{name}={count}" for name, count in categories.most_common(4))
        findings.append(_finding(
            "warn",
            "出现结构化错误事件",
            f"从工具输出、Task 消息、issue 评论和最终回复中识别到 {len(error_events)} 条错误（{sample}）。"
            "优先查看“关键事件”里的红色错误，再判断哪些是已恢复的探索错误、哪些进入关键路径。",
            "events/errors",
        ))
    if trace["metrics"]["artifact_count"] is None:
        findings.append(_finding(
            "info",
            "Artifact 覆盖不可用",
            "本数据源没有 artifact 清单；页面显示“不可用”，而不是 0。"
            "需要补采事件流里的 artifact:created 或 acceptance 目录中的产物索引。",
            "artifacts",
        ))
    commands_without_cwd = [
        event for event in tool_events
        if (event.get("sandbox") or {}).get("cmd") and not (event.get("sandbox") or {}).get("cwd")
    ]
    if commands_without_cwd:
        findings.append(_finding(
            "info",
            "沙箱上下文只采到命令",
            f"{len(commands_without_cwd)}/{len(tool_events)} 个工具事件有命令但没有 cwd；"
            f"结束时间 {tools_with_end}/{len(tool_events)}、退出码 {tools_with_exit}/{len(tool_events)}。"
            "缺失字段显示 not captured，不用 0 或 completed 冒充。",
            "events",
        ))
    elif tool_events and tools_with_end < len(tool_events):
        findings.append(_finding(
            "info",
            "工具结束时间覆盖不完整",
            f"{tools_with_end}/{len(tool_events)} 个工具事件有结束时间；"
            "没有结果的调用保持 duration 为空，不推算。",
            "events",
        ))
    trace["findings"] = findings

    sources = Counter(event.get("source") or "unknown" for event in events)
    trace["metrics"]["source_event_counts"] = dict(sources)
    if not trace.get("gaps"):
        trace["gaps"] = []
    if any(event.get("confidence") == "inferred" for event in events):
        trace["gaps"].append("部分时间点由事件顺序推断，已在 confidence 中标注 inferred。")
    if trace["metrics"]["artifact_count"] is None:
        trace["gaps"].append("本数据源没有 artifact 清单；不能据此断言没有 artifact。")
    if not side_effects:
        trace["gaps"].append("本数据源没有副作用账本；external_calls 只能表示已观测到的 API 事件。")
    if not (trace.get("raw_evidence") or {}):
        trace["gaps"].append("本数据源没有原始证据层；HTML 只能展示规范化事件，无法回查完整输出。")
    if str((trace.get("target") or {}).get("status") or "") == "in_progress":
        trace["gaps"].append("会话仍在进行；当前结束时间是最后一次可观测事件，不是最终回复。")
    trace["gaps"] = list(dict.fromkeys(trace["gaps"]))
    return trace
