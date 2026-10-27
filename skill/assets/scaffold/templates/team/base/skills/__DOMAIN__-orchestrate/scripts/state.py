from __future__ import annotations

import os
from typing import Any, Dict, Iterable

import runtime


def _from_context(contexts: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    children: list[Dict[str, Any]] = []
    for context in contexts:
        explicit = context.get("team_children") or context.get("children")
        if isinstance(explicit, list):
            children.extend(item for item in explicit if isinstance(item, dict))
        member_result = context.get("member_result")
        if isinstance(member_result, dict):
            item = dict(member_result)
            item.setdefault("status", "done")
            children.append(item)
        tasks = context.get("previous_tasks")
        if isinstance(tasks, list):
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                result = task.get("result")
                role = str(task.get("role") or "")
                if isinstance(result, str):
                    result = runtime.extract_member_result(result, role)
                if isinstance(result, dict):
                    item = dict(result)
                    item.setdefault("role", role)
                    item.setdefault("status", str(task.get("status") or "done").lower())
                    item.setdefault("id", str(task.get("issue_id") or task.get("id") or ""))
                    children.append(item)
    return children


def _root(contexts: Iterable[Dict[str, Any]]) -> str:
    return runtime.root_issue_id(contexts) or runtime.source_issue_id(contexts)


def rebuild(contexts: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    contexts = list(contexts)
    root = _root(contexts)
    children = _from_context(contexts)
    recovery_error = ""
    if root and os.environ.get("DRY_RUN") != "1" and os.environ.get("INDUSTRIAL_AGENT_OFFLINE") != "1":
        try:
            recovered = runtime.collect_team_children(root)
            if recovered:
                children = recovered
        except Exception as error:  # platform truth is unavailable: never dispatch blindly
            recovery_error = str(error)[:500]
    by_role: Dict[str, list[Dict[str, Any]]] = {}
    for child in children:
        role = str(child.get("role") or "").strip()
        if role:
            by_role.setdefault(role, []).append(child)
    request = ""
    for items in by_role.values():
        for item in items:
            payload = item.get("input")
            if not isinstance(payload, dict):
                continue
            value = payload.get("request") or payload.get("user_input") or payload.get("input")
            if isinstance(value, str) and value.strip():
                request = value.strip()
                break
        if request:
            break
    completed: Dict[str, Dict[str, Any]] = {}
    active: Dict[str, Dict[str, Any]] = {}
    failed: Dict[str, Dict[str, Any]] = {}
    for role, items in by_role.items():
        successful = [
            item
            for item in items
            if str(item.get("status") or "").lower() in runtime.DONE_STATUSES
            and isinstance(item.get("result"), dict)
        ]
        if successful:
            completed[role] = successful[-1]
            continue
        broken = [
            item
            for item in items
            if str(item.get("status") or "").lower() in runtime.FAILED_STATUSES
            or (
                str(item.get("status") or "").lower() in runtime.DONE_STATUSES
                and not isinstance(item.get("result"), dict)
            )
        ]
        if broken:
            failed[role] = broken[-1]
            continue
        running = [
            item
            for item in items
            if str(item.get("status") or "").lower() in runtime.ACTIVE_STATUSES
        ]
        if running:
            active[role] = running[-1]
    return {
        "root_issue_id": root,
        "request": request,
        "children": children,
        "completed": completed,
        "active": active,
        "failed": failed,
        "dispatched_roles": set(by_role),
        "recovery_error": recovery_error,
    }
