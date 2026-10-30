#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import runtime  # noqa: E402
import state as state_module  # noqa: E402
import plan  # noqa: E402
import dispatch as dispatch_module  # noqa: E402
import render  # noqa: E402


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _dispatch(role: str, root: str, payload):
    """Dispatch one member; return (result, error_text, error_class).

    A platform-created child with no id is the dangerous case: the member Task may
    exist, but nothing downstream can reference the native scheduling card, so the
    turn must fail closed with its own reason instead of reporting success.
    """
    try:
        return dispatch_module.dispatch(role, root, payload), "", ""
    except ValueError as error:
        return None, str(error)[:300], "configuration"
    except RuntimeError as error:
        text = str(error)[:300]
        error_class = "dispatch_identity_missing" if "no child issue id" in text else "member_dispatch_failed"
        return None, text, error_class


def _inline_wait_enabled() -> bool:
    return os.environ.get("TEAM_INLINE_WAIT", "0").strip().lower() in {"1", "true", "yes", "on"}


def _poll_slice(contexts, state, request):
    """Wait one bounded slice for member progress; never block past the deadline."""
    deadline = time.monotonic() + max(_env_float("TEAM_SLICE_SECONDS", 25.0), 0.01)
    poll = max(_env_float("TEAM_POLL_SECONDS", 2.0), 0.01)
    while time.monotonic() < deadline:
        time.sleep(poll)
        refreshed = state_module.rebuild(contexts)
        refreshed["dispatched_roles"] = set(refreshed.get("dispatched_roles") or set()) | set(
            state.get("dispatched_roles") or set()
        )
        action = plan.next_action(refreshed, request)
        if action.get("action") != "wait":
            return refreshed, action
        state = refreshed
    return state, plan.next_action(state, request)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--continue", dest="continue_loop", action="store_true")
    parser.add_argument("--event", default="")
    parser.add_argument("--output", default="/tmp/turn.json")
    args = parser.parse_args()

    started = time.monotonic()
    root = ""
    contexts = runtime.load_contexts(args.context or ["context/task.json"])
    state = state_module.rebuild(contexts)
    root = str(state.get("root_issue_id") or "")
    request = runtime.current_request(contexts) or str(state.get("request") or "")
    action = plan.next_action(state, request)
    activity = "action"
    warnings = []

    if _inline_wait_enabled():
        for _ in range(max(1, int(_env_float("TEAM_MAX_STEPS", 10)))):
            if action.get("action") != "dispatch":
                break
            role = str(action.get("role") or "")
            _dispatched, dispatch_error, dispatch_error_class = _dispatch(role, root, action.get("input") or {})
            if not dispatch_error and not str(_dispatched.get("id") or "").strip():
                dispatch_error = "dispatch returned no member issue id for role %s" % role
                dispatch_error_class = "dispatch_identity_missing"
            if dispatch_error:
                action = {
                    "action": "error",
                    "error_class": dispatch_error_class or "configuration",
                    "text": "成员任务已提交但未返回任务编号，无法确认调度卡状态，请稍后重试。"
                    if dispatch_error_class == "dispatch_identity_missing"
                    else "暂时无法派发成员任务，请检查成员 Agent 绑定。",
                    "summary": dispatch_error,
                }
                break
            state.setdefault("dispatched_roles", set()).add(role)
            if os.environ.get("DRY_RUN") == "1":
                action = {"action": "wait", "text": "已派发，正在等待%s返回结果。" % role}
                break
            state, action = _poll_slice(contexts, state, request)
            if action.get("action") == "wait":
                break

    if action.get("action") == "dispatch":
        role = str(action.get("role") or "")
        dispatched, dispatch_error, dispatch_error_class = _dispatch(role, root, action.get("input") or {})
        child_id = str((dispatched or {}).get("id") or "").strip()
        if not dispatch_error and not child_id:
            dispatch_error = "dispatch returned no member issue id for role %s" % role
            dispatch_error_class = "dispatch_identity_missing"
        if dispatch_error:
            activity = "failed"
            payload = runtime.turn_payload(
                render.build(
                    "error",
                    root,
                    "成员任务已提交但未返回任务编号，无法确认调度卡状态，请稍后重试。"
                    if dispatch_error_class == "dispatch_identity_missing"
                    else "暂时无法派发成员任务，请检查成员 Agent 绑定。",
                ),
                started,
                ok=False,
                error={"class": dispatch_error_class or "configuration", "summary": dispatch_error},
                observable={"root_issue_id": root, "dispatch_role": role},
            )
        else:
            text = "已受理，正在交给%s处理，完成后我会汇总结果。" % role
            if dispatched.get("dry_run"):
                text = "已受理，准备交给%s处理。" % role
            payload = runtime.turn_payload(
                render.build("progress", root, text),
                started,
                observable={"root_issue_id": root, "dispatch_issue_id": child_id, "dispatch_role": role},
            )
    elif action.get("action") == "needs_input":
        activity = "no_action"
        payload = runtime.turn_payload(render.build("needs_input", root, str(action.get("text") or "")), started)
    elif action.get("action") == "final":
        payload = runtime.turn_payload(
            render.build("final", root, str(action.get("text") or "已完成。"), action.get("facts")),
            started,
        )
        try:
            runtime.set_issue_status(root, "in_review")
        except Exception as error:  # keep the answer; surface the failed platform write
            warnings.append("issue status: %s" % str(error)[:300])
    elif action.get("action") == "member_stalled":
        activity = "no_action"
        payload = runtime.turn_payload(
            render.build("error", root, str(action.get("text") or "成员任务长时间没有进展。")),
            started,
            ok=False,
            error={
                "class": "member_runtime_stalled",
                "summary": "role=%s idle=%ss" % (action.get("role"), action.get("idle_seconds")),
            },
        )
    elif action.get("action") == "wait" and _inline_wait_enabled():
        activity = "no_action"
        payload = runtime.turn_payload(None, started, continue_loop=True)
    elif action.get("action") == "wait":
        activity = "no_action"
        payload = runtime.turn_payload(
            render.build("progress", root, str(action.get("text") or "正在处理。")),
            started,
            observable={
                "root_issue_id": root,
                "dispatch_issue_id": str(action.get("dispatch_issue_id") or ""),
            },
        )
    else:
        activity = "failed"
        payload = runtime.turn_payload(
            render.build("error", root, str(action.get("text") or "处理失败，请稍后重试。")),
            started,
            ok=False,
            error={
                "class": str(action.get("error_class") or "terminal"),
                "summary": str(action.get("summary") or action.get("text") or "")[:500],
            },
        )

    if root and os.environ.get("DRY_RUN") != "1":
        try:
            runtime.record_squad_activity(root, activity, "turn.py: %s" % action.get("action"))
        except Exception as error:
            warnings.append("squad activity: %s" % str(error)[:300])
    if warnings:
        payload["warnings"] = warnings

    runtime.write_json(args.output, payload)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        started = time.monotonic()
        payload = runtime.turn_payload(
            runtime.reply_intent("escalate", "", "处理失败，请稍后重试。"),
            started,
            ok=False,
            error={"class": "terminal", "summary": str(error)[:500]},
        )
        output = "/tmp/turn.json"
        if "--output" in sys.argv:
            try:
                output = sys.argv[sys.argv.index("--output") + 1]
            except IndexError:
                pass
        runtime.write_json(output, payload)
        print(str(error)[:500], file=sys.stderr)
        sys.exit(1)
