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

    if action.get("action") == "dispatch":
        role = str(action.get("role") or "")
        dispatched = dispatch_module.dispatch(role, root, action.get("input") or {})
        text = "已受理，正在交给%s处理，完成后我会汇总结果。" % role
        if dispatched.get("dry_run"):
            text = "已受理，准备交给%s处理。" % role
        payload = runtime.turn_payload(render.build("progress", root, text), started)
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
    elif action.get("action") == "wait":
        activity = "no_action"
        payload = runtime.turn_payload(render.build("progress", root, str(action.get("text") or "正在处理。")), started)
    else:
        activity = "failed"
        payload = runtime.turn_payload(
            render.build("error", root, str(action.get("text") or "处理失败，请稍后重试。")),
            started,
            ok=False,
            error={"class": str(action.get("error_class") or "terminal"), "summary": str(action.get("text") or "")[:500]},
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
