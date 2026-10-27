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
import domain  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--output", default="/tmp/result.json")
    args = parser.parse_args()

    started = time.monotonic()
    contexts = runtime.load_contexts(args.context or ["context/task.json"])
    input_payload = runtime.child_payload(contexts)
    request = str(
        input_payload.get("request")
        or input_payload.get("user_input")
        or input_payload.get("input")
        or runtime.current_request(contexts)
    ).strip()
    root = runtime.root_issue_id(contexts)
    child_issue_id = runtime.source_issue_id(contexts)
    result = domain.respond(request, contexts, input_payload)
    payload = {
        "ok": True,
        "role": "__ROLE__",
        "result": result,
        "reply_intent": runtime.reply_intent("result", root, result.get("text", ""), result.get("facts")),
        "timings": {"total_ms": int((time.monotonic() - started) * 1000)},
    }
    result_key = runtime.idempotency_key(
        child_issue_id,
        "__ROLE__",
        runtime.task_identity(contexts),
        request,
    )
    payload["member_result_key"] = result_key
    if child_issue_id:
        payload["child_issue_id"] = child_issue_id
    runtime.write_json(args.output, payload)
    runtime.write_json("artifacts/member-result.json", payload)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        payload = {
            "ok": False,
            "role": "__ROLE__",
            "error": {"class": "terminal", "summary": str(error)[:500]},
            "timings": {"total_ms": 0},
        }
        output = "/tmp/result.json"
        if "--output" in sys.argv:
            try:
                output = sys.argv[sys.argv.index("--output") + 1]
            except IndexError:
                pass
        runtime.write_json(output, payload)
        runtime.write_json("artifacts/member-result.json", payload)
        print(str(error)[:500], file=sys.stderr)
        sys.exit(1)
