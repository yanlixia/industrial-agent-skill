from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import storylib.collectors as collectors
from storylib.metrics import enrich_trace


class LocalCollectorTests(unittest.TestCase):
    def test_round_ends_after_tool_parts_not_first_assistant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "opencode.db"
            con = sqlite3.connect(db)
            con.executescript(
                """
                CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT, time_created INTEGER);
                CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT);
                CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, time_created INTEGER, data TEXT);
                """
            )
            con.execute("INSERT INTO session VALUES (?,?,?,?)", ("ses_1", "Local Demo", "/tmp/ws", 1000))
            con.execute("INSERT INTO message VALUES (?,?,?,?)", ("u1", "ses_1", 1000, json.dumps({"role": "user"})))
            con.execute("INSERT INTO part VALUES (?,?,?,?)", ("p1", "u1", 1000, json.dumps({"type": "text", "text": "hello"})))
            con.execute("INSERT INTO message VALUES (?,?,?,?)", ("a1", "ses_1", 4000, json.dumps({"role": "assistant", "agent": "demo"})))
            con.execute("INSERT INTO part VALUES (?,?,?,?)", ("p2", "a1", 3000, json.dumps({"type": "text", "text": "working"})))
            con.execute(
                "INSERT INTO part VALUES (?,?,?,?)",
                ("p3", "a1", 2000, json.dumps({"type": "tool", "tool": "bash", "state": {"input": {"command": "ls -la"}, "output": "ok", "status": "completed"}})),
            )
            con.commit()
            con.close()

            trace = collectors.collect_local("ses_1", env_name="prod", db_path=str(db))
            result = enrich_trace(trace)

        self.assertEqual(result["target"]["title"], "Local Demo")
        self.assertEqual(result["rounds"][0]["duration_ms"], 3000)
        tool = next(item for item in result["events"] if item["kind"] == "tool")
        self.assertEqual(tool["round_id"], "R1")
        self.assertEqual(tool["sandbox"]["cmd"], "ls -la")
        self.assertLessEqual(tool["ts"], result["window"]["end"])


class ArtifactsCollectorTests(unittest.TestCase):
    def test_reads_title_from_children_and_does_not_use_uuid_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = {
                "session_id": "s1",
                "environment": "test",
                "squad_id": "sq1",
                "root_issue_id": "11111111-1111-1111-1111-111111111111",
                "max_round_seconds": 10,
                "children": [{"task_id": "t1", "title": "设备故障诊断 123", "agent_name": "诊断专家", "duration_ms": 10000}],
                "rounds": [{"round": "query", "task_id": "t-leader", "timings": {"total_seconds": 10}, "terminal_status": "completed"}],
            }
            (root / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            (root / "child-topology.json").write_text(json.dumps(summary["children"]), encoding="utf-8")
            trace = collectors.collect_artifacts(str(root))

        self.assertEqual(trace["target"]["title"], "设备故障诊断 123")
        self.assertEqual(trace["topology"][0]["title"], "设备故障诊断 123")
        self.assertNotEqual(trace["topology"][0]["title"], summary["root_issue_id"])


class FakeClient:
    def request(self, path: str, workspace_slug: str = "") -> object:
        if path.startswith("/api/chat/sessions?"):
            return [{"id": "s1", "title": "Cloud Demo", "squad_id": "sq1"}]
        if path == "/api/chat/sessions/s1/messages":
            return [
                {"role": "user", "content": "go", "task_id": "t1", "run_id": "r1", "created_at": "2026-01-01T00:00:00+00:00"},
                {"role": "assistant", "content": "done", "task_id": "t1", "run_id": "r1", "created_at": "2026-01-01T00:00:20+00:00", "elapsed_ms": 20000},
            ]
        if path == "/api/chat/runs/r1/events-list":
            return {"events": [
                {"sequence": 1, "type": "subtask:created", "task_id": "t1", "created_at": "2026-01-01T00:00:01+00:00", "payload": {"task": {"id": "t2", "is_leader": False}, "agent": {"name": "member", "role": "member"}, "issue": {"title": "child"}}},
                {"sequence": 2, "type": "member:tool_running", "task_id": "t2", "created_at": "2026-01-01T00:00:02+00:00", "content": "Running tool: exec_command"},
                {"sequence": 3, "type": "member:tool_running", "task_id": "t2", "created_at": "2026-01-01T00:00:03+00:00", "content": "正在执行命令"},
                {"sequence": 4, "type": "member:tool_result", "task_id": "t2", "created_at": "2026-01-01T00:00:04+00:00", "content": "ok"},
                {"sequence": 5, "type": "artifact:created", "task_id": "t2", "created_at": "2026-01-01T00:00:05+00:00", "artifact": {"id": "a1", "name": "report.html"}},
            ]}
        if path == "/api/tasks/t1":
            return {
                "id": "t1", "agent_name": "Coordinator", "status": "completed", "source_ref": "root",
                "prompt": "ISS-1: root task", "created_at": "2026-01-01T00:00:00+00:00", "claimed_at": "2026-01-01T00:00:00+00:00",
                "started_at": "2026-01-01T00:00:01+00:00", "completed_at": "2026-01-01T00:00:20+00:00", "duration_ms": 20000,
            }
        if path == "/api/tasks/t1/messages":
            return []
        if path == "/api/tasks/t2":
            return {
                "id": "t2", "agent_name": "member", "status": "completed", "source_ref": "child",
                "prompt": "ISS-2: 设备故障诊断 123", "created_at": "2026-01-01T00:00:01+00:00", "claimed_at": "2026-01-01T00:00:01+00:00",
                "started_at": "2026-01-01T00:00:02+00:00", "completed_at": "2026-01-01T00:00:06+00:00", "duration_ms": 5000,
            }
        if path == "/api/tasks/t2/messages":
            return [
                {"seq": 1, "event_type": "tool_use", "tool": "exec_command", "input": {"command": "python3 run.py"}, "created_at": "2026-01-01T00:00:02+00:00"},
                {"seq": 2, "event_type": "tool_result", "tool": "exec_command", "output": "diagnosis", "status": "completed", "created_at": "2026-01-01T00:00:04+00:00"},
            ]
        if path.startswith("/api/issues/"):
            raise RuntimeError("404")
        raise AssertionError(path)


class CloudCollectorTests(unittest.TestCase):
    def test_deduplicates_double_tool_running_and_builds_task_title(self) -> None:
        with patch.object(collectors, "_load_teams_module", return_value=object()), patch.object(
            collectors, "_cloud_client", return_value=(FakeClient(), {})
        ):
            trace = collectors.collect_cloud("s1", env_name="test", workspace="ws")
            result = enrich_trace(trace)

        self.assertEqual(result["target"]["title"], "Cloud Demo")
        self.assertEqual(result["metrics"]["tool_calls"], 1)
        self.assertEqual(result["metrics"]["artifact_count"], 1)
        member = next(item for item in result["tasks"] if item["id"] == "t2")
        self.assertEqual(member["title"], "设备故障诊断 123")
        tool = next(item for item in result["events"] if item["kind"] == "tool")
        self.assertEqual(tool["sandbox"]["cmd"], "python3 run.py")
        self.assertEqual(tool["end_ts"], "2026-01-01T00:00:04.000Z")
        self.assertEqual(tool["duration_ms"], 2000)
        self.assertEqual(tool["sandbox"]["result_output"], "diagnosis")
        self.assertEqual(tool["sandbox"]["status"], "completed")
        self.assertEqual(tool["sandbox"]["exit"], "not captured")
        self.assertEqual(tool["sandbox"]["cwd"], "")
        self.assertEqual(result["metrics"]["tools_with_end"], 1)
        self.assertEqual(result["metrics"]["tools_with_output"], 1)
        self.assertTrue(any(item["title"] == "设备故障诊断 123" for item in result["topology"]))

    def test_backfills_tool_output_from_task_messages_without_inventing_end_time(self) -> None:
        class NoRuntimeResultClient(FakeClient):
            def request(self, path: str, workspace_slug: str = "") -> object:
                if path == "/api/chat/runs/r1/events-list":
                    return {"events": [
                        {"sequence": 1, "type": "subtask:created", "task_id": "t1", "created_at": "2026-01-01T00:00:01+00:00", "payload": {"task": {"id": "t2", "is_leader": False}, "agent": {"name": "member", "role": "member"}, "issue": {"title": "child"}}},
                        {"sequence": 2, "type": "member:tool_running", "task_id": "t2", "created_at": "2026-01-01T00:00:02+00:00", "content": "Running tool: exec_command"},
                    ]}
                return super().request(path, workspace_slug)

        with patch.object(collectors, "_load_teams_module", return_value=object()), patch.object(
            collectors, "_cloud_client", return_value=(NoRuntimeResultClient(), {})
        ):
            trace = collectors.collect_cloud("s1", env_name="test", workspace="ws")
            result = enrich_trace(trace)

        tool = next(item for item in result["events"] if item["kind"] == "tool")
        self.assertEqual(tool["sandbox"]["result_output"], "diagnosis")
        self.assertEqual(tool["sandbox"]["result_source"], "task-messages")
        self.assertEqual(tool["sandbox"]["status"], "completed")
        self.assertFalse(tool["end_ts"])
        self.assertIsNone(tool["duration_ms"])
        self.assertEqual(result["metrics"]["tools_with_output"], 1)
        self.assertEqual(result["metrics"]["tools_with_end"], 0)

    def test_continuation_uses_wall_time_and_marks_active_round(self) -> None:
        class ContinuationClient:
            def request(self, path: str, workspace_slug: str = "") -> object:
                if path.startswith("/api/chat/sessions?"):
                    return [{"id": "s2", "title": "Continuation Demo", "squad_id": "sq1"}]
                if path == "/api/chat/sessions/s2/messages":
                    return [
                        {"role": "user", "content": "diagnose", "task_id": "t1", "run_id": "r1", "created_at": "2026-01-01T00:00:00+00:00"},
                        {"role": "assistant", "content": "preview", "task_id": "t3", "run_id": "r1", "created_at": "2026-01-01T00:01:00+00:00", "elapsed_ms": 30000},
                        {"role": "user", "content": "确认", "task_id": "t4", "run_id": "r2", "created_at": "2026-01-01T00:02:00+00:00"},
                    ]
                if path == "/api/chat/runs/r1/events-list":
                    return {"events": [
                        {"sequence": 1, "type": "subtask:created", "task_id": "t1", "created_at": "2026-01-01T00:00:00+00:00", "payload": {"task": {"id": "t1", "is_leader": True}}},
                        {"sequence": 2, "type": "subtask:created", "task_id": "t2", "created_at": "2026-01-01T00:00:05+00:00", "payload": {"task": {"id": "t2", "is_leader": False}}},
                        {"sequence": 3, "type": "subtask:created", "task_id": "t3", "created_at": "2026-01-01T00:00:50+00:00", "payload": {"task": {"id": "t3", "is_leader": True}}},
                    ]}
                if path == "/api/chat/runs/r2/events-list":
                    return {"events": []}
                if path == "/api/tasks/t1":
                    return {
                        "id": "t1", "agent_name": "Coordinator", "status": "completed",
                        "source_ref": "11111111-1111-1111-1111-111111111111",
                        "created_at": "2026-01-01T00:00:00+00:00", "started_at": "2026-01-01T00:00:00+00:00",
                        "completed_at": "2026-01-01T00:00:10+00:00",
                    }
                if path == "/api/tasks/t2":
                    return {
                        "id": "t2", "agent_name": "Diagnosis", "status": "completed", "source_ref": "22222222-2222-2222-2222-222222222222",
                        "created_at": "2026-01-01T00:00:05+00:00", "started_at": "2026-01-01T00:00:06+00:00",
                        "completed_at": "2026-01-01T00:00:45+00:00",
                    }
                if path == "/api/tasks/t3":
                    return {
                        "id": "t3", "agent_name": "Coordinator", "status": "completed", "source_ref": "11111111-1111-1111-1111-111111111111",
                        "created_at": "2026-01-01T00:00:50+00:00", "started_at": "2026-01-01T00:00:51+00:00",
                        "completed_at": "2026-01-01T00:00:59+00:00",
                    }
                if path == "/api/tasks/t4":
                    return {
                        "id": "t4", "agent_name": "Coordinator", "status": "running", "source_ref": "33333333-3333-3333-3333-333333333333",
                        "created_at": "2026-01-01T00:02:00+00:00", "started_at": "2026-01-01T00:02:01+00:00",
                    }
                if path.endswith("/messages"):
                    return []
                if path.startswith("/api/issues/"):
                    return {"title": "root issue"}
                raise AssertionError(path)

        with patch.object(collectors, "_load_teams_module", return_value=object()), patch.object(
            collectors, "_cloud_client", return_value=(ContinuationClient(), {})
        ):
            trace = collectors.collect_cloud("s2", env_name="test", workspace="ws")
            result = enrich_trace(trace)

        first, second = result["rounds"]
        self.assertEqual(first["start"], "2026-01-01T00:00:00.000Z")
        self.assertEqual(first["duration_ms"], 60000)
        self.assertEqual(first["final_leader_elapsed_ms"], 30000)
        self.assertEqual(second["status"], "in_progress")
        self.assertFalse(any(event["kind"] == "assistant" and event["round_id"] == "R2" for event in result["events"]))
        self.assertFalse(any(not event["ts"] for event in result["events"]))
        self.assertEqual(result["target"]["root_issue_id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(result["target"]["status"], "in_progress")
        self.assertEqual(next(item for item in result["tasks"] if item["id"] == "t2")["round_id"], "R1")
        self.assertEqual(next(item for item in result["tasks"] if item["id"] == "t3")["round_id"], "R1")
        self.assertEqual(result["window"]["end"], "2026-01-01T00:02:01.000Z")


if __name__ == "__main__":
    unittest.main()
