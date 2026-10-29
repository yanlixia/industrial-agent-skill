from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from storylib.metrics import enrich_trace
from storylib.model import new_trace


def event(ts: str, kind: str, command: str = "") -> dict:
    item = {
        "id": f"evt-{ts}",
        "ts": ts,
        "end_ts": "",
        "kind": kind,
        "title": kind,
        "detail": "",
        "actor": "agent",
        "task_id": "",
        "source": "test",
        "confidence": "exact",
    }
    if command:
        item["sandbox"] = {"cmd": command}
    return item


class MetricsTests(unittest.TestCase):
    def test_wall_round_sum_gap_and_missing_data(self) -> None:
        trace = new_trace({"id": "s1", "kind": "squad_session", "title": "demo"})
        trace["rounds"] = [
            {"id": "R1", "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:00:10Z"},
            {"id": "R2", "start": "2026-01-01T00:00:30Z", "end": "2026-01-01T00:01:00Z"},
        ]
        trace["tasks"] = [
            {
                "id": "t1", "kind": "leader", "agent_name": "leader", "issue_id": "root",
                "created_at": "2026-01-01T00:00:00Z", "started_at": "2026-01-01T00:00:01Z",
                "completed_at": "2026-01-01T00:00:10Z", "duration_ms": 9000, "round_id": "R1",
            },
            {
                "id": "t2", "kind": "member", "agent_name": "member", "issue_id": "child",
                "created_at": "2026-01-01T00:00:31Z", "started_at": "2026-01-01T00:00:32Z",
                "completed_at": "2026-01-01T00:00:52Z", "duration_ms": 20000,
            },
            {
                "id": "t3", "kind": "member", "agent_name": "member", "issue_id": "child",
                "created_at": "2026-01-01T00:00:35Z", "started_at": "2026-01-01T00:00:36Z",
                "completed_at": "2026-01-01T00:00:49Z", "duration_ms": 13000,
            },
        ]
        trace["events"] = [
            event("2026-01-01T00:00:02Z", "tool", "ls -la /tmp"),
            event("2026-01-01T00:00:03Z", "tool", "python3 run.py auto"),
            event("2026-01-01T00:00:04Z", "tool", "python3 -c 'read context/skills.json'"),
        ]

        result = enrich_trace(trace, budget_seconds=25)
        metrics = result["metrics"]

        self.assertEqual(metrics["wall_seconds"], 60.0)
        self.assertEqual(metrics["round_sum_seconds"], 40.0)
        self.assertEqual(metrics["between_round_gap_seconds"], 20.0)
        self.assertEqual(metrics["max_round_seconds"], 30.0)
        self.assertEqual(metrics["member_task_count"], 2)
        self.assertEqual(metrics["exploration_calls"], 1)
        self.assertIsNone(metrics["artifact_count"])
        self.assertIsNone(metrics["duplicate_effects"])
        self.assertEqual(metrics["auxiliary_leader_runs"], 0)
        self.assertTrue(any("多个 Task run" in item["title"] for item in result["findings"]))
        self.assertTrue(any("Artifact" in item["title"] for item in result["findings"]))
        self.assertTrue(any("artifact" in gap for gap in result["gaps"]))

    def test_duplicate_effects_require_ledger(self) -> None:
        trace = new_trace({"id": "s1", "kind": "agent_session"})
        trace["rounds"] = [{"id": "R1", "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:00:01Z"}]
        trace["artifacts"] = [{"id": "a1"}]
        trace["side_effects"] = [
            {"idempotency_key": "k1"}, {"idempotency_key": "k1"},
        ]
        result = enrich_trace(trace)
        self.assertEqual(result["metrics"]["duplicate_effects"], 1)
        self.assertEqual(result["metrics"]["artifact_count"], 1)
        self.assertFalse(any("副作用账本" in gap for gap in result["gaps"]))

    def test_detects_structured_errors_from_raw_evidence(self) -> None:
        trace = new_trace({"id": "s1", "kind": "squad_session"})
        trace["rounds"] = [{"id": "R1", "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:00:30Z"}]
        trace["tasks"] = [{
            "id": "t1", "kind": "member", "agent_name": "member", "round_id": "R1",
            "started_at": "2026-01-01T00:00:01Z", "completed_at": "2026-01-01T00:00:20Z",
        }]
        trace["raw_evidence"] = {
            "run_events": {"r1": [
                {"type": "member:tool_result", "task_id": "t1", "created_at": "2026-01-01T00:00:02Z", "output": "deepexi issue: error: argument issue_command: invalid choice: 'comments'"},
                {"type": "member:tool_result", "task_id": "t1", "created_at": "2026-01-01T00:00:03Z", "output": "Foil 授权失败（production）：Workbench product-user authentication is incomplete"},
                {"type": "member:tool_result", "task_id": "t1", "created_at": "2026-01-01T00:00:04Z", "output": "Traceback (most recent call last):\nModuleNotFoundError: No module named 'foo'"},
                {"type": "member:tool_result", "task_id": "t1", "created_at": "2026-01-01T00:00:05Z", "output": "HTTP 502 Bad Gateway"},
            ]},
            "task_messages": {},
            "task_details": {},
            "issue_comments": {},
            "session_messages": [],
        }
        result = enrich_trace(trace)
        self.assertEqual(result["metrics"]["error_events"], 4)
        categories = result["metrics"]["error_categories"]
        for name in ("cli_invalid_choice", "foil_auth", "traceback", "http_error"):
            self.assertIn(name, categories)
        self.assertIn("module_not_found", next(
            item["error"]["matches"] for item in result["errors"]
            if item["error"]["category"] == "traceback"
        ))
        self.assertTrue(all(item["kind"] == "error" for item in result["errors"]))
        self.assertTrue(all(item.get("error", {}).get("detected_by") == "storylib" for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
