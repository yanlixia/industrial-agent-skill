#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import runtime  # noqa: E402
import state as state_module  # noqa: E402
import render  # noqa: E402
import domain  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--continue", dest="continue_loop", action="store_true")
    parser.add_argument("--event", default="")
    parser.add_argument("--output", default="/tmp/turn.json")
    args = parser.parse_args()

    started = time.monotonic()
    contexts = runtime.load_contexts(args.context or ["context/task.json"])
    state = state_module.rebuild(contexts)
    root = str(state.get("root_issue_id") or runtime.root_issue_id(contexts))
    request = runtime.current_request(contexts)

    if not request:
        payload = runtime.turn_payload(
            render.build("needs_input", root, "请补充需要处理的问题。"),
            started,
        )
    else:
        result = domain.respond(request, state)
        payload = runtime.turn_payload(
            render.build(result.get("kind", "result"), root, result.get("text", ""), result.get("facts")),
            started,
        )
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
