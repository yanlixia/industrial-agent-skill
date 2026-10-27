import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD = ROOT / "tools" / "scaffold.py"

FAKE_CLI = r'''#!/usr/bin/env python3
import json
import os
import sys
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
    done({"issues": [child for child in db["children"] if child["parent_issue_id"] == parent]})

if args[:2] == ["issue", "create"]:
    child = {
        "id": "child-%d" % (len(db["children"]) + 1),
        "parent_issue_id": value("--parent"),
        "title": value("--title"),
        "description": value("--description"),
        "status": "todo",
        "assignee_id": value("--assignee-id"),
    }
    db["children"].append(child)
    save()
    done(child)

if args[:3] == ["issue", "comment", "list"]:
    done({"comments": []})

if args[:2] == ["artifact", "list"]:
    issue_id = args[2]
    child = next((item for item in db["children"] if item["id"] == issue_id), {})
    items = [{"id": "artifact-" + issue_id, "name": "member-result.json", "created_at": "2026-10-27T00:00:00Z"}] if child.get("artifact") else []
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
    db["activity"].append(args[3])
    save()
    done({"ok": True})

raise SystemExit("unsupported fake deepexi command: %s" % " ".join(args))
'''


class GeneratedTeamFlowTests(unittest.TestCase):
    def test_serial_team_flow_through_generated_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "team"
            subprocess.run(
                [
                    sys.executable, str(SCAFFOLD), "--mode", "team",
                    "--domain", "energy", "--title", "能源专家团",
                    "--roles", "diagnosis,maintenance,monitor",
                    "--leader-agent-id", "leader-id",
                    "--agent-ids", "diagnosis=diag-id,maintenance=fix-id,monitor=watch-id",
                    "--squad-id", "squad-id", "--target", str(target),
                ],
                text=True, capture_output=True, check=True,
            )
            fake_cli = Path(tmp) / "deepexi"
            fake_cli.write_text(FAKE_CLI, encoding="utf-8")
            fake_cli.chmod(0o755)
            db_path = Path(tmp) / "db.json"
            db_path.write_text(json.dumps({"children": [], "activity": []}), encoding="utf-8")

            root_id = "11111111-1111-4111-8111-111111111111"
            task = {
                "id": "task-1",
                "source_type": "issue",
                "source_ref": root_id,
                "input": "ISS-1: 处理设备故障\n\nDeepWorks chat squad run.\n\nUser request:\n处理设备故障",
            }
            task_path = Path(tmp) / "task.json"
            task_path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
            env = dict(os.environ)
            env.pop("DRY_RUN", None)
            env.pop("INDUSTRIAL_AGENT_OFFLINE", None)
            env["DEEPEXI_BIN"] = str(fake_cli)
            env["FAKE_DEEPEI_DB"] = str(db_path)
            entry = target / "skills" / "energy-orchestrate" / "scripts" / "turn.py"

            def run_turn(name):
                output = Path(tmp) / ("turn-%s.json" % name)
                completed = subprocess.run(
                    [sys.executable, str(entry), "--context", str(task_path), "--output", str(output)],
                    cwd=target,
                    text=True,
                    capture_output=True,
                    env=env,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
                return json.loads(output.read_text(encoding="utf-8"))

            first = run_turn("dispatch-diagnosis")
            self.assertEqual(first["reply_intent"]["kind"], "progress")
            state = json.loads(db_path.read_text(encoding="utf-8"))
            self.assertEqual([child["assignee_id"] for child in state["children"]], ["diag-id"])

            waiting = run_turn("wait-diagnosis")
            self.assertEqual(waiting["reply_intent"]["kind"], "progress")
            self.assertEqual(len(json.loads(db_path.read_text(encoding="utf-8"))["children"]), 1)

            for role, current_agent_id, next_agent_id in [
                ("diagnosis", "diag-id", "fix-id"),
                ("maintenance", "fix-id", "watch-id"),
                ("monitor", "watch-id", None),
            ]:
                state = json.loads(db_path.read_text(encoding="utf-8"))
                child = state["children"][-1]
                self.assertEqual(child["assignee_id"], current_agent_id)
                child["status"] = "done"
                child["artifact"] = json.dumps(
                    {
                        "ok": True,
                        "role": role,
                        "result": {"summary": role + " 已完成", "facts": {}},
                        "member_result_key": "key-" + role,
                    },
                    ensure_ascii=False,
                )
                db_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                payload = run_turn("after-" + role)
                current = json.loads(db_path.read_text(encoding="utf-8"))
                if next_agent_id is None:
                    self.assertEqual(payload["reply_intent"]["kind"], "final")
                    self.assertEqual(current["root_status"], "in_review")
                else:
                    self.assertEqual(payload["reply_intent"]["kind"], "progress")
                    self.assertEqual(len(current["children"]), len(state["children"]) + 1)
                    self.assertEqual(current["children"][-1]["assignee_id"], next_agent_id)


if __name__ == "__main__":
    unittest.main()
