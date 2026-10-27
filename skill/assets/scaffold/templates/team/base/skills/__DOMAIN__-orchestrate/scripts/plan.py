from __future__ import annotations

from typing import Any, Dict

ROLES = __ROLES_JSON__


def _role_label(role: str) -> str:
    return role.replace("-", " ").replace("_", " ").title()


def _result_text(role: str, item: Dict[str, Any]) -> str:
    result = item.get("result") if isinstance(item.get("result"), dict) else item
    return str(result.get("summary") or result.get("text") or "已返回结果").strip()


def _synth(state: Dict[str, Any]) -> Dict[str, Any]:
    lines: list[str] = []
    facts: Dict[str, Any] = {}
    for role in ROLES:
        item = state.get("completed", {}).get(role)
        if not item:
            continue
        text = _result_text(role, item)
        if text:
            lines.append("%s：%s" % (_role_label(role), text))
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if isinstance(result.get("facts"), dict):
            facts.update(result["facts"])
    return {"text": "\n".join(lines) or "团队成员已完成处理。", "facts": facts}


def next_action(state: Dict[str, Any], request: str) -> Dict[str, Any]:
    if state.get("recovery_error"):
        return {
            "action": "error",
            "error_class": "terminal",
            "text": "暂时无法读取团队任务状态，请稍后重试。",
        }
    if state.get("failed"):
        role, item = next(iter(state["failed"].items()))
        summary = _result_text(role, item)
        return {
            "action": "error",
            "error_class": "terminal",
            "text": "%s未完成：%s" % (_role_label(role), summary),
        }
    if not request and not state.get("children"):
        return {"action": "needs_input", "text": "请告诉我需要团队处理的问题。"}
    completed = state.get("completed") or {}
    if ROLES and all(role in completed for role in ROLES):
        return {"action": "final", **_synth(state)}
    if state.get("active"):
        return {"action": "wait", "text": "任务已派发，正在等待成员返回结果。"}
    ready = next((role for role in ROLES if role not in state.get("dispatched_roles", set())), "")
    if not ready:
        return {"action": "wait", "text": "任务已派发，正在等待成员返回结果。"}
    return {
        "action": "dispatch",
        "role": ready,
        "input": {"request": request, "completed_roles": list(completed)},
    }
