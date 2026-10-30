"""End-to-end dispatch flow against a fake `deepexi` CLI.

This test needs no network and no platform account: it proves that the generated
团长 can dispatch every member in order, consume structured member results and
return one final answer, and that a member with no progress surfaces
`member_runtime_stalled` instead of looping forever.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "deepworks.manifest.json").read_text(encoding="utf-8"))
ENTRY = ROOT / MANIFEST["entry"]
ROLES = [str(member.get("role")) for member in MANIFEST.get("members") or []]
ROOT_ISSUE = "11111111-1111-4111-8111-111111111111"

FAKE_CLI = r'''#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

db_path = Path(os.environ["FAKE_DEEPEI_DB"])
db = json.loads(db_path.read_text(encoding="utf-8"))
args = sys.argv[1:]


def value(flag, default=""):
    return args[args.index(flag) + 1] if flag in args else default


def save():
    db_path.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def done(payload):
    print(json.dumps(payload, ensure_ascii=False))
    raise SystemExit(0)


if args[:2] == ["issue", "list"]:
    parent = value("--parent")
    if os.environ.get("FAKE_AUTO_COMPLETE") == "1":
        for child in db["children"]:
            if child.get("parent_issue_id") != parent or child.get("status") != "todo":
                continue
            child["status"] = "done"
            child["artifact"] = json.dumps(
                {
                    "ok": True,
                    "role": child.get("role", ""),
                    "result": {"summary": child.get("role", "") + " 已完成", "facts": {}},
                    "member_result_key": "key-" + child.get("role", ""),
                },
                ensure_ascii=False,
            )
        save()
    done({"issues": [child for child in db["children"] if child["parent_issue_id"] == parent]})

if args[:2] == ["issue", "create"]:
    if os.environ.get("FAKE_CREATE_WITHOUT_ID") == "1":
        done({"ok": True})
    child = {
        "id": "child-%d" % (len(db["children"]) + 1),
        "parent_issue_id": value("--parent"),
        "title": value("--title"),
        "description": value("--description"),
        "status": "todo",
        "assignee_id": value("--assignee-id"),
        "role": value("--title").split("]")[0].lstrip("["),
        "created_at": os.environ.get("FAKE_CHILD_CREATED_AT") or datetime.now(timezone.utc).isoformat(),
    }
    db["children"].append(child)
    save()
    done(child)

if args[:3] == ["issue", "comment", "list"]:
    done({"comments": []})

if args[:2] == ["artifact", "list"]:
    issue_id = args[2]
    child = next((item for item in db["children"] if item["id"] == issue_id), {})
    items = [{"id": "artifact-" + issue_id, "name": "member-result.json"}] if child.get("artifact") else []
    done({"items": items})

if args[:2] == ["artifact", "download"]:
    artifact_id = args[2]
    issue_id = artifact_id[len("artifact-"):] if artifact_id.startswith("artifact-") else artifact_id
    child = next((item for item in db["children"] if item["id"] == issue_id), {})
    Path(value("--output-file")).write_text(child.get("artifact") or "", encoding="utf-8")
    done({"artifact_id": artifact_id})

if args[:2] == ["issue", "status"]:
    db["root_status"] = args[3]
    save()
    done({"ok": True})

if args[:2] == ["squad", "activity"]:
    db.setdefault("activity", []).append(args[3])
    save()
    done({"ok": True})

raise SystemExit("unsupported fake deepexi command: %s" % " ".join(args))
'''


class DispatchFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.cli = self.base / "deepexi"
        self.cli.write_text(FAKE_CLI, encoding="utf-8")
        self.cli.chmod(0o755)
        self.db_path = self.base / "db.json"
        self.db_path.write_text(json.dumps({"children": [], "activity": []}), encoding="utf-8")
        self.task_path = self.base / "task.json"
        self.task_path.write_text(
            json.dumps(
                {
                    "id": "task-1",
                    "source_type": "issue",
                    "source_ref": ROOT_ISSUE,
                    "input": "处理设备故障",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_turn(self, extra_env=None):
        output = self.base / "turn.json"
        env = dict(os.environ)
        env.pop("DRY_RUN", None)
        env.pop("INDUSTRIAL_AGENT_OFFLINE", None)
        env["DEEPEXI_BIN"] = str(self.cli)
        env["FAKE_DEEPEI_DB"] = str(self.db_path)
        for role in ROLES:
            env["DEEPWORKS_AGENT_ID_" + role.upper().replace("-", "_")] = role + "-id"
        env.update(extra_env or {})
        completed = subprocess.run(
            [sys.executable, str(ENTRY), "--context", str(self.task_path), "--output", str(output)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        return json.loads(output.read_text(encoding="utf-8"))

    def test_serial_chain_reaches_final_in_one_turn(self):
        if not ROLES:
            self.skipTest("team mode only")
        payload = self.run_turn(
            {
                "FAKE_AUTO_COMPLETE": "1",
                "TEAM_INLINE_WAIT": "1",
                "TEAM_SLICE_SECONDS": "3",
                "TEAM_POLL_SECONDS": "0.05",
            }
        )
        self.assertEqual(payload["reply_intent"]["kind"], "final", payload)
        draft = payload["reply_intent"]["tone_context"]["draft"]
        self.assertIn(ROLES[0] + " 已完成", draft)

        state = json.loads(self.db_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [child["assignee_id"] for child in state["children"]],
            [role + "-id" for role in ROLES],
        )
        self.assertEqual(state["root_status"], "in_review")

    def test_stalled_member_is_reported_without_redispatch(self):
        payload = self.run_turn(
            {
                "FAKE_CHILD_CREATED_AT": "2020-01-01T00:00:00Z",
                "TEAM_INLINE_WAIT": "1",
                "TEAM_MEMBER_STALL_SECONDS": "1",
                "TEAM_SLICE_SECONDS": "1",
                "TEAM_POLL_SECONDS": "0.05",
            }
        )
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["error"]["class"], "member_runtime_stalled")
        self.assertEqual(payload["reply_intent"]["kind"], "error")

        state = json.loads(self.db_path.read_text(encoding="utf-8"))
        self.assertEqual(len(state["children"]), 1)
        self.assertEqual(state["children"][0]["assignee_id"], ROLES[0] + "-id")

    def test_dispatch_reports_member_issue_id(self):
        if not ROLES:
            self.skipTest("team mode only")
        payload = self.run_turn()
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["reply_intent"]["kind"], "progress")
        state = json.loads(self.db_path.read_text(encoding="utf-8"))
        child_id = str(state["children"][0]["id"])
        self.assertEqual(payload["dispatch_issue_id"], child_id)
        self.assertEqual(payload["dispatch_role"], ROLES[0])

    def test_dispatch_without_child_id_fails_closed(self):
        if not ROLES:
            self.skipTest("team mode only")
        payload = self.run_turn({"FAKE_CREATE_WITHOUT_ID": "1"})
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["error"]["class"], "dispatch_identity_missing")
        self.assertEqual(payload["reply_intent"]["kind"], "error")
        self.assertNotIn("已受理", payload["reply_intent"]["tone_context"]["draft"])
        self.assertNotIn("dispatch_issue_id", payload)


if __name__ == "__main__":
    unittest.main()
