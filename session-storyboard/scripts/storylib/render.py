"""Render a normalized trace into a self-contained HTML storyboard."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .model import parse_ts, redact

TEMPLATE = Path(__file__).resolve().parents[2] / "assets" / "story-template.html"
KIND_CLASS = {
    "user": "platform",
    "assistant": "model",
    "platform": "platform",
    "model": "model",
    "tool": "tool",
    "sandbox": "tool",
    "api": "external",
    "artifact": "artifact",
    "child": "member",
    "member": "member",
    "error": "error",
}


def _relative(value: Any, origin: Any) -> float:
    start, base = parse_ts(value), parse_ts(origin)
    if start is None or base is None:
        return 0.0
    return max(0.0, (start - base).total_seconds())


def _duration_label(seconds: float | None) -> str:
    return "—" if seconds is None else f"{seconds:.1f}s"


def _human(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    if seconds >= 60:
        return f"{seconds / 60:.1f}m"
    return f"{seconds:.0f}s"


def _short(text: Any, limit: int = 18) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"


def _span(
    cls: str,
    start: float,
    end: float,
    label: str,
    detail: str,
    confidence: str = "exact",
    *,
    task_id: str = "",
    task_title: str = "",
    seq: Any = None,
) -> dict[str, Any]:
    return {
        "cls": cls,
        "start": round(start, 3),
        "end": round(max(start + 0.35, end), 3),
        "label": _short(label, 18),
        "detail": str(detail or ""),
        "confidence": confidence,
        "task_id": task_id,
        "task_title": task_title,
        "seq": seq,
    }


def _build_lanes(trace: dict[str, Any], origin: Any, total: float) -> list[dict[str, Any]]:
    tasks = trace.get("tasks") or []
    events = trace.get("events") or []
    topology_titles = {
        str(item.get("id") or ""): str(item.get("title") or "")
        for item in (trace.get("topology") or [])
        if item.get("id")
    }
    round_labels = {
        str(item.get("id") or ""): str(item.get("label") or item.get("id") or "")
        for item in (trace.get("rounds") or [])
    }
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("task_id"):
            by_task[str(event["task_id"])].append(event)

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for task in sorted(tasks, key=lambda item: item.get("created_at") or ""):
        task_id = str(task.get("id") or "")
        is_leader = task.get("kind") == "leader"
        agent = str(task.get("agent_name") or task.get("kind") or "Task")
        group_key = ("Coordinator", "leader") if is_leader else (agent, "member")
        group = groups.setdefault(group_key, {
            "id": f"{group_key[1]}:{agent}",
            "name": "Coordinator" if is_leader else agent,
            "role": group_key[1],
            "agents": set(),
            "tasks": [],
            "spans": [],
        })
        group["agents"].add(agent)
        group["tasks"].append(task)
        task_events = sorted(by_task.get(task_id, []), key=lambda item: item.get("ts") or "")
        spans: list[dict[str, Any]] = []
        created = _relative(task.get("created_at"), origin) if task.get("created_at") else None
        claimed = _relative(task.get("claimed_at"), origin) if task.get("claimed_at") else None
        started = _relative(task.get("started_at"), origin) if task.get("started_at") else None
        completed = _relative(task.get("completed_at"), origin) if task.get("completed_at") else None
        round_id = str(task.get("round_id") or "")
        stage = topology_titles.get(task_id) or str(task.get("title") or task.get("issue_id") or task_id)
        stage_short = _short(stage.split()[0] if stage.split() else stage, 12)
        if is_leader:
            round_label = round_labels.get(round_id, "run")
            run_label = round_label if round_label.startswith(round_id) else f"{round_id} · {round_label}".strip(" ·")
        else:
            run_label = stage_short or f"task {task_id[:8]}"
        if created is not None and claimed is not None and claimed >= created:
            spans.append(_span("platform", created, claimed, "claim", f"{run_label} · Task 创建 → runtime 认领", "derived", task_id=task_id, task_title=stage))
        if claimed is not None and started is not None and started >= claimed:
            spans.append(_span("platform", claimed, started, "start", f"{run_label} · Runtime 认领 → 沙箱/模型启动", "derived", task_id=task_id, task_title=stage))
        if started is not None and completed is not None and completed >= started:
            spans.append(_span(
                "member" if task.get("kind") in {"member", "child"} else "model",
                started,
                completed,
                run_label,
                f"{task.get('agent_name') or 'agent'} · {task.get('status') or 'unknown'} · task {task_id[:8]}",
                "derived",
                task_id=task_id,
                task_title=stage,
            ))
        lifecycle_titles = {"Task created", "Runtime claimed", "Execution started", "Task completed"}
        for event in task_events:
            title = str(event.get("title") or "")
            kind = str(event.get("kind") or "platform")
            if kind not in {"platform", "model", "tool", "sandbox", "api", "artifact", "child", "member", "error"}:
                continue
            if title in lifecycle_titles:
                continue
            start = _relative(event.get("ts"), origin)
            end = _relative(event.get("end_ts"), origin) if event.get("end_ts") else start + 0.8
            if start > total + 1:
                continue
            label = title
            if kind in {"tool", "sandbox"}:
                sandbox = event.get("sandbox") or {}
                label = sandbox.get("cmd_excerpt") or sandbox.get("cmd") or title
            spans.append(_span(
                KIND_CLASS.get(kind, "platform"),
                start,
                min(end, total + 1),
                label,
                f"{run_label} · {event.get('detail') or title}",
                event.get("confidence") or "exact",
                task_id=task_id,
                task_title=stage,
                seq=(event.get("sandbox") or {}).get("seq"),
            ))
        spans.sort(key=lambda item: (item["start"], item["end"]))
        group["spans"].extend(spans)

    lanes: list[dict[str, Any]] = []
    for group in groups.values():
        group_tasks = group["tasks"]
        durations = [
            float(item.get("duration_ms") or 0) / 1000.0
            for item in group_tasks
            if item.get("duration_ms") is not None
        ]
        if group["role"] == "leader":
            kind = f"leader · {len(group_tasks)} run{'s' if len(group_tasks) != 1 else ''}"
            if durations:
                kind += f" · Σ {sum(durations):.0f}s"
            agents = sorted(group["agents"])
            if len(agents) > 1:
                kind += f" · {len(agents)} agent names"
        else:
            kind = f"member · {len(group_tasks)} task{'s' if len(group_tasks) != 1 else ''}"
            if durations:
                kind += f" · avg {sum(durations) / len(durations):.0f}s"
        lanes.append({
            "id": group["id"],
            "name": group["name"],
            "role": group["role"],
            "kind": kind,
            "task_ids": [str(item.get("id") or "") for item in group_tasks],
            "spans": sorted(group["spans"], key=lambda item: (item["start"], item["end"])),
        })
    lanes.sort(key=lambda item: (0 if item["role"] == "leader" else 1, min((span["start"] for span in item["spans"]), default=0)))

    if not lanes:
        by_actor: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            if event.get("kind") in {"user", "assistant"}:
                continue
            by_actor[str(event.get("actor") or "runtime")].append(event)
        for actor, actor_events in by_actor.items():
            spans = []
            for event in actor_events:
                start = _relative(event.get("ts"), origin)
                end = _relative(event.get("end_ts"), origin) if event.get("end_ts") else start + 0.8
                spans.append(_span(KIND_CLASS.get(str(event.get("kind")), "platform"), start, min(end, total + 1), event.get("title") or "", event.get("detail") or "", event.get("confidence") or "exact"))
            if spans:
                lanes.append({"id": actor, "name": actor, "kind": "inferred lane", "task_ids": [], "spans": sorted(spans, key=lambda item: item["start"])})
    return lanes


def _build_events(trace: dict[str, Any], origin: Any, total: float) -> list[dict[str, Any]]:
    task_titles = {
        str(task.get("id") or ""): str(task.get("title") or task.get("agent_name") or "task")
        for task in (trace.get("tasks") or [])
    }
    round_labels = {
        str(item.get("id") or ""): str(item.get("label") or item.get("id") or "")
        for item in (trace.get("rounds") or [])
    }
    result = []
    for event in trace.get("events") or []:
        start = _relative(event.get("ts"), origin)
        if start > total + 1:
            continue
        end = _relative(event.get("end_ts"), origin) if event.get("end_ts") else None
        kind = str(event.get("kind") or "platform")
        sandbox = event.get("sandbox") or {}
        title = str(event.get("title") or kind)
        key = kind in {"tool", "sandbox", "artifact", "child", "api", "error", "user", "assistant"}
        if kind == "artifact":
            artifact_name = title.removeprefix("Artifact · ")
            if artifact_name in {"final-message.txt", "Artifact created"} or artifact_name.startswith("turn-trace-"):
                key = False
        if kind == "platform" and title in {"Run completed", "run:completed"}:
            key = True
        result.append({
            "id": event.get("id") or "",
            "t": round(start, 3),
            "end_t": round(end, 3) if end is not None else None,
            "type": kind.upper(),
            "cls": KIND_CLASS.get(kind, "platform"),
            "title": title,
            "actor": event.get("actor") or "runtime",
            "detail": event.get("detail") or "",
            "dur": _duration_label(end - start if end is not None else None),
            "dur_s": round(end - start, 3) if end is not None else None,
            "sandbox": sandbox or None,
            "confidence": event.get("confidence") or "exact",
            "source": event.get("source") or "unknown",
            "key": key,
            "round_id": event.get("round_id") or "",
            "round_label": round_labels.get(str(event.get("round_id") or ""), ""),
            "task_id": event.get("task_id") or "",
            "task_title": task_titles.get(str(event.get("task_id") or ""), ""),
            "status": event.get("status") or "",
            "error": event.get("error") or None,
            "evidence_refs": event.get("evidence_refs") or [],
        })
    return sorted(result, key=lambda item: item["t"])


def _build_sources(trace: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(str(event.get("source") or "unknown") for event in events)
    known = [
        ("platform-api", "tasks · task-runs · issues · events"),
        ("session-messages", "user / assistant messages"),
        ("runtime-events", "sandbox stages · tools · artifacts"),
        ("acceptance-artifacts", "captured acceptance JSON"),
        ("local-db", "opencode.db"),
    ]
    peak = max(counts.values(), default=0) or 1
    result = []
    for name, detail in known:
        count = counts.get(name, 0)
        result.append({"name": name, "detail": f"{count} events" if count else f"not available · {detail}", "pct": round(count / peak * 100) if count else 0})
    for name, count in counts.items():
        if name not in {item[0] for item in known}:
            result.append({"name": name, "detail": f"{count} events", "pct": round(count / peak * 100)})
    return result


def _build_zoom_ranges(
    rounds: list[dict[str, Any]],
    total: float,
    slowest_id: str,
    round_tails: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    ranges = [{
        "id": "session",
        "label": "Session",
        "start": 0.0,
        "end": round(total, 3),
        "duration": round(total, 3),
        "note": "完整会话 · 包含轮间空档",
        "default": not rounds,
    }]
    for item in rounds:
        round_id = str(item.get("id") or "R")
        duration = max(0.0, float(item.get("end") or 0) - float(item.get("start") or 0))
        end = max(float(item.get("end") or 0), float((round_tails or {}).get(round_id) or 0))
        if end > float(item.get("end") or 0):
            duration = end - float(item.get("start") or 0)
        ranges.append({
            "id": round_id,
            "label": str(item.get("label") or item.get("id") or "Round"),
            "start": round(float(item.get("start") or 0), 3),
            "end": round(end, 3),
            "duration": round(duration, 3),
            "note": " · ".join(
                part for part in (
                    str(item.get("note") or ""),
                    "含轮后证据" if end > float(item.get("end") or 0) else "",
                ) if part
            ),
            "default": round_id == slowest_id,
        })
    return ranges


def _build_errors(trace: dict[str, Any], origin: Any) -> list[dict[str, Any]]:
    errors = []
    for event in trace.get("errors") or []:
        info = event.get("error") or {}
        errors.append({
            "id": event.get("id") or "",
            "t": round(_relative(event.get("ts"), origin), 3),
            "ts": event.get("ts") or "",
            "task_id": event.get("task_id") or "",
            "round_id": event.get("round_id") or "",
            "actor": event.get("actor") or "runtime",
            "category": info.get("category") or "unknown",
            "severity": info.get("severity") or event.get("status") or "error",
            "label": info.get("label") or event.get("title") or "错误",
            "line": info.get("line") or event.get("detail") or "",
            "summary": info.get("summary") or "",
            "evidence_ref": info.get("evidence_ref") or "",
            "inferred_ts": bool(info.get("inferred_ts")),
        })
    return sorted(errors, key=lambda item: (item["t"], item["category"]))


def _build_tasks_detail(
    trace: dict[str, Any],
    view_rounds: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw = trace.get("raw_evidence") or {}
    task_details = raw.get("task_details") or {}
    task_messages = raw.get("task_messages") or {}
    round_meta = {str(item.get("id") or ""): item for item in view_rounds}
    events_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in trace.get("events") or []:
        if event.get("task_id"):
            events_by_task[str(event["task_id"])].append(event)
    errors_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for error in errors:
        if error.get("task_id"):
            errors_by_task[str(error["task_id"])].append(error)
    artifacts_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for artifact in trace.get("artifacts") or []:
        if artifact.get("task_id"):
            artifacts_by_task[str(artifact["task_id"])].append(artifact)

    result = []
    for task in trace.get("tasks") or []:
        task_id = str(task.get("id") or "")
        detail = task_details.get(task_id) or {}
        if not isinstance(detail, dict):
            detail = {}
        round_id = str(task.get("round_id") or "")
        round_item = round_meta.get(round_id) or {}
        lifecycle = []
        for field, label in (
            ("created_at", "created"),
            ("claimed_at", "claimed"),
            ("started_at", "started"),
            ("completed_at", "completed"),
        ):
            value = task.get(field) or detail.get(field)
            if value:
                lifecycle.append({"label": label, "ts": value})
        for item in detail.get("timeline") or []:
            if not isinstance(item, dict):
                continue
            entry = {"label": str(item.get("title") or item.get("kind") or "timeline"), "ts": item.get("timestamp") or "", "detail": str(item.get("detail") or "")}
            if entry["ts"] and not any(existing["label"] == entry["label"] and existing["ts"] == entry["ts"] for existing in lifecycle):
                lifecycle.append(entry)
        lifecycle.sort(key=lambda item: item.get("ts") or "")

        outputs: list[dict[str, Any]] = []
        for row in task_messages.get(task_id) or []:
            kind = str(row.get("event_type") or row.get("type") or "")
            if kind not in {"text", "text_final"}:
                continue
            text = row.get("content") or row.get("output") or ""
            if text:
                outputs.append({
                    "seq": row.get("seq"),
                    "ts": row.get("created_at") or "",
                    "kind": kind,
                    "content": redact(str(text)),
                })
        output = detail.get("output") or {}
        final_reply = output.get("final_reply") if isinstance(output, dict) else ""
        if final_reply and not any(item["content"] == final_reply for item in outputs):
            outputs.append({
                "seq": None,
                "ts": task.get("completed_at") or "",
                "kind": "final_reply",
                "content": redact(str(final_reply)),
            })

        tool_events = [
            event for event in events_by_task.get(task_id, [])
            if event.get("kind") in {"tool", "sandbox"}
        ]
        tool_events.sort(key=lambda event: (event.get("ts") or "", event.get("id") or ""))
        result.append({
            "id": task_id,
            "short_id": task_id[:8],
            "kind": task.get("kind") or "member",
            "title": task.get("title") or task.get("agent_name") or task_id,
            "agent_id": task.get("agent_id") or detail.get("agent_id") or "",
            "agent_name": task.get("agent_name") or detail.get("agent_name") or "agent",
            "agent_version": str(detail.get("agent_version") or ""),
            "status": task.get("status") or detail.get("status") or "unknown",
            "issue_id": task.get("issue_id") or detail.get("source_ref") or "",
            "identifier": str((((raw.get("issues") or {}).get(str(task.get("issue_id") or "")) or {}).get("identifier") or "")),
            "parent_task_id": task.get("parent_task_id") or detail.get("parent_task_id") or "",
            "runtime_name": task.get("runtime_name") or detail.get("runtime_name") or "",
            "round_id": round_id,
            "round_label": str(round_item.get("label") or round_id),
            "round_start": round_item.get("start"),
            "round_end": round_item.get("end"),
            "created_at": task.get("created_at") or detail.get("created_at") or "",
            "claimed_at": task.get("claimed_at") or detail.get("claimed_at") or "",
            "started_at": task.get("started_at") or detail.get("started_at") or "",
            "completed_at": task.get("completed_at") or detail.get("completed_at") or "",
            "duration_ms": task.get("duration_ms") if task.get("duration_ms") is not None else detail.get("duration_ms"),
            "failure_reason": redact(str(detail.get("failure_reason") or "")),
            "prompt": redact(str(detail.get("prompt") or "")),
            "lifecycle": lifecycle,
            "outputs": outputs,
            "tool_event_ids": [str(event.get("id") or "") for event in tool_events],
            "error_event_ids": [str(item.get("id") or "") for item in errors_by_task.get(task_id, [])],
            "artifact_ids": [str(item.get("id") or "") for item in artifacts_by_task.get(task_id, [])],
            "usage": detail.get("usage") or {},
            "attempts": detail.get("attempts") or [],
            "raw_message_count": len(task_messages.get(task_id) or []),
        })
    result.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or ""))
    return result


def build_view(trace: dict[str, Any]) -> dict[str, Any]:
    rounds = trace.get("rounds") or []
    tasks = trace.get("tasks") or []
    events = trace.get("events") or []
    start = trace.get("window", {}).get("start") or (rounds[0].get("start") if rounds else (events[0].get("ts") if events else ""))
    if not start:
        start = "1970-01-01T00:00:00+00:00"
    wall_total = round(float(trace.get("window", {}).get("duration_ms") or 0) / 1000.0, 3)
    if wall_total <= 0:
        wall_total = max([_relative(item.get("ts"), start) for item in events] + [0.0]) + 1.0
    round_tails: dict[str, float] = {}
    latest_round_end = max(
        (_relative(item.get("end"), start) for item in rounds if item.get("end")),
        default=0.0,
    )
    tail_grace = 300.0
    for event in events:
        observed = max(
            _relative(event.get("ts"), start),
            _relative(event.get("end_ts"), start) if event.get("end_ts") else 0.0,
        )
        round_id = str(event.get("round_id") or "")
        if round_id and latest_round_end <= observed <= latest_round_end + tail_grace:
            round_tails[round_id] = max(round_tails.get(round_id, 0.0), observed)
    # Visual horizon: keep post-round evidence (finalizing / artifact) visible
    # without changing the Wall time metric, which remains the round window.
    # Only the chronologically last round may claim trailing evidence; stale
    # round ids from older runs must not stretch that round's zoom range.
    total = round(max([wall_total, latest_round_end, *round_tails.values()]), 3)
    metrics = trace.get("metrics") or {}
    target = trace.get("target") or {}
    view_rounds = []
    label_counts = Counter(str(item.get("label") or item.get("id") or "round") for item in rounds)
    label_seen: Counter[str] = Counter()
    for item in rounds:
        begin = _relative(item.get("start"), start)
        end = _relative(item.get("end"), start)
        if end <= begin:
            end = begin + float(item.get("duration_ms") or 0) / 1000.0
        label = str(item.get("label") or item.get("id") or "round")
        label_seen[label] += 1
        if label_counts[label] > 1:
            label = f"{label} · {label_seen[label]}"
        view_rounds.append({
            "id": item.get("id") or "R",
            "label": label,
            "start": round(begin, 3),
            "end": round(end, 3),
            "note": f"{item.get('status') or 'completed'} · {item.get('timing_source') or 'unknown'}",
        })
    slowest_round = max(view_rounds, key=lambda item: item["end"] - item["start"], default=None)
    default_view = str(slowest_round.get("id")) if slowest_round else "session"
    zoom_ranges = _build_zoom_ranges(view_rounds, total, default_view, round_tails)
    errors = _build_errors(trace, start)
    tasks_detail = _build_tasks_detail(trace, view_rounds, errors)
    max_round = metrics.get("max_round_seconds")
    round_sum = float(metrics.get("round_sum_seconds") or sum(float(item.get("duration_ms") or 0) / 1000.0 for item in rounds))
    idle = float(metrics.get("between_round_gap_seconds") or max(0.0, wall_total - round_sum))
    findings = []
    for item in trace.get("findings") or []:
        severity = str(item.get("severity") or "info")
        findings.append({
            "kind": "good" if severity == "good" else "warn" if severity in {"warn", "error"} else "info",
            "title": item.get("title") or "finding",
            "desc": item.get("detail") or "",
        })
    topology = []
    for index, item in enumerate(trace.get("topology") or []):
        kind = str(item.get("kind") or "member")
        title = str(item.get("title") or item.get("id") or "node")
        if title.startswith(str(target.get("root_issue_id") or "___")) and target.get("title"):
            title = str(target["title"])
        topology.append({
            "kind": "leader" if kind == "leader" else "member",
            "icon": "L" if kind == "leader" else "M",
            "title": title,
            "meta": item.get("meta") or "",
        })
    if not topology:
        topology = [{"kind": "leader", "icon": "L", "title": target.get("title") or "session", "meta": "root"}]
    tool_calls = int(metrics.get("tool_calls") or 0)
    exploration = int(metrics.get("exploration_calls") or 0)
    duplicate = metrics.get("duplicate_effects")
    artifact_count = metrics.get("artifact_count")
    view_events = _build_events(trace, start, total)
    key_events = [event for event in view_events if event.get("key")]
    initial_event = next(
        (event for event in view_events if event.get("key") and event.get("type") not in {"USER", "ASSISTANT"}),
        next((event for event in view_events if event.get("type") not in {"USER"}), view_events[0] if view_events else {"t": 0}),
    )
    initial_t = round(max(0.0, float(initial_event.get("t") or 0)), 3)
    if slowest_round is not None:
        initial_t = round(float(slowest_round.get("start") or 0), 3)
    error_categories = Counter(str(item.get("category") or "unknown") for item in errors)
    kpis = [
        {"label": "Wall time", "value": _human(wall_total), "unit": "", "note": f"{len(view_rounds)} rounds · Σ {round_sum:.0f}s · gap {idle:.0f}s", "cls": "cyan"},
        {"label": "Max round", "value": "—" if max_round is None else f"{float(max_round):.0f}", "unit": "s", "note": f"budget {float(metrics.get('budget_seconds') or 120):.0f}s", "cls": "warn" if max_round is not None and float(max_round) > float(metrics.get('budget_seconds') or 120) else "good"},
        {"label": "Tasks", "value": str(len(tasks)), "unit": "", "note": f"{metrics.get('leader_run_count', 0)} leader runs · {metrics.get('member_task_count', 0)} member tasks", "cls": "violet"},
        {"label": "Tool calls", "value": str(tool_calls), "unit": "", "note": f"observed · {exploration} exploration heuristics", "cls": "warn" if exploration else "good"},
        {"label": "Artifacts", "value": "—" if artifact_count is None else str(artifact_count), "unit": "", "note": "unavailable ≠ zero" if artifact_count is None else "created in events", "cls": "cyan"},
        {"label": "API events", "value": str(metrics.get("external_calls") or 0), "unit": "", "note": "observed only · not side-effect count", "cls": "cyan"},
        {"label": "Duplicate effects", "value": "—" if duplicate is None else str(duplicate), "unit": "", "note": "— = no idempotency ledger" if duplicate is None else "idempotency ledger", "cls": "good" if duplicate == 0 else "warn" if duplicate else "cyan"},
        {"label": "Errors", "value": str(len(errors)), "unit": "", "note": " · ".join(f"{name} {count}" for name, count in error_categories.most_common(3)) or "no structured errors", "cls": "warn" if errors else "good"},
    ]
    counts = metrics.get("source_event_counts") or {}
    view = {
        "schema_version": trace.get("schema_version") or "session-story/v1",
        "total": round(max(total, 1.0), 3),
        "wall_total": round(max(wall_total, 1.0), 3),
        "initial_t": initial_t,
        "topology_hint": "single agent · root only" if target.get("kind") == "agent_session" or len(topology) <= 1 else "root issue → children",
        "topology_badge": "agent" if target.get("kind") == "agent_session" or len(topology) <= 1 else "native squad",
        "hero": {
            "eyebrow": f"{str(target.get('kind') or 'agent_session').replace('_', ' ').upper()} / SESSION REPLAY",
            "title": str(target.get("title") or "一条会话"),
            "title_tail": "拆成可回放的时间线",
            "sub": "从用户发出第一条消息开始，展示每个 Task 的启动、模型首动作、工具调用、沙箱命令、子任务交接、等待、artifact 和最终回复。缺失数据会显式标注来源。",
            "status": str(target.get("status") or "unknown").upper(),
            "environment": str(target.get("environment") or "unknown").upper(),
            "session_line": f"session {target.get('id') or '—'} · env {target.get('environment') or '—'}",
            "workspace_line": f"workspace {target.get('workspace') or '—'} · squad {str(target.get('squad_id') or '—')[:8]}",
            "timeline_sub": f"Wall time {_human(wall_total)} · Σ rounds {_human(round_sum)} · {len(tasks)} tasks · {len(events)} events",
            "rounds_sub": "max " + ("—" if max_round is None else f"{float(max_round):.0f}s"),
            "findings_sub": f"{len(findings)} items",
            "default_view": default_view,
            "errors_sub": f"{len(errors)} errors",
        },
        "kpis": kpis,
        "rounds": view_rounds,
        "default_view": default_view,
        "zoom_ranges": zoom_ranges,
        "lanes": _build_lanes(trace, start, total),
        "events": view_events,
        "event_stats": {"key": len(key_events), "all": len(view_events), "errors": len(errors)},
        "errors": errors,
        "tasks_detail": tasks_detail,
        "task_stats": {
            "total": len(tasks),
            "leader": sum(1 for task in tasks if task.get("kind") == "leader"),
            "member": sum(1 for task in tasks if task.get("kind") in {"member", "child"}),
            "with_detail": sum(1 for item in tasks_detail if item.get("prompt") or item.get("tool_event_ids")),
        },
        "topology": topology,
        "findings": findings,
        "sources": _build_sources(trace, events),
        "gaps": list(trace.get("gaps") or []),
        "sources_hint": "每个 span 都带 source 与 confidence；只有顺序或轮询观测时明确标注，不把推断写成精确时间。",
        "metrics": metrics,
        "artifacts": list(trace.get("artifacts") or []),
        "raw_evidence": trace.get("raw_evidence") or {},
        "evidence_counts": ((trace.get("raw_evidence") or {}).get("collection") or {}).get("counts") or {},
    }
    return view


def render_html(trace: dict[str, Any], output: str, template: str = "") -> str:
    template_path = Path(template).expanduser() if template else TEMPLATE
    text = template_path.read_text(encoding="utf-8")
    view = build_view(trace)
    payload = json.dumps(view, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    if "__SESSION_STORY_JSON__" not in text:
        raise RuntimeError(f"模板缺少 __SESSION_STORY_JSON__ 占位符: {template_path}")
    text = text.replace("__SESSION_STORY_JSON__", payload)
    out_path = Path(output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return str(out_path)
