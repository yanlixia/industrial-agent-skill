from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from storylib.render import build_view, render_html
from storylib.metrics import enrich_trace


def base_trace() -> dict:
    return {
        "schema_version": "session-story/v1",
        "target": {
            "kind": "squad_session",
            "id": "session-1",
            "title": "设备诊断 <unsafe>",
            "environment": "test",
            "workspace": "ws",
            "squad_id": "squad-1",
            "root_issue_id": "root-uuid",
            "status": "completed",
        },
        "window": {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-01T00:01:00Z",
            "duration_ms": 60000,
        },
        "rounds": [
            {"id": "R1", "label": "确认", "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:00:20Z", "duration_ms": 20000},
            {"id": "R2", "label": "确认", "start": "2026-01-01T00:00:20Z", "end": "2026-01-01T00:01:00Z", "duration_ms": 40000},
        ],
        "tasks": [
            {"id": "l1", "kind": "leader", "agent_name": "Coordinator", "status": "completed", "created_at": "2026-01-01T00:00:00Z", "started_at": "2026-01-01T00:00:01Z", "completed_at": "2026-01-01T00:00:20Z", "duration_ms": 19000, "round_id": "R1"},
            {"id": "l2", "kind": "leader", "agent_name": "Coordinator", "status": "completed", "created_at": "2026-01-01T00:00:20Z", "started_at": "2026-01-01T00:00:21Z", "completed_at": "2026-01-01T00:01:00Z", "duration_ms": 39000, "round_id": "R2"},
            {"id": "m1", "kind": "member", "title": "设备故障诊断 123", "agent_name": "诊断专家", "status": "completed", "created_at": "2026-01-01T00:00:05Z", "started_at": "2026-01-01T00:00:06Z", "completed_at": "2026-01-01T00:00:15Z", "duration_ms": 9000, "round_id": ""},
        ],
        "events": [
            {"id": "e1", "ts": "2026-01-01T00:00:00Z", "kind": "user", "title": "用户消息", "actor": "user", "source": "test"},
            {"id": "e2", "ts": "2026-01-01T00:00:03Z", "kind": "tool", "title": "工具 · exec", "actor": "诊断专家", "task_id": "m1", "source": "test", "sandbox": {"cmd": "python3 run.py"}},
            {"id": "e3", "ts": "2026-01-01T00:00:04Z", "kind": "artifact", "title": "Artifact · final-message.txt", "actor": "诊断专家", "task_id": "m1", "source": "test"},
            {"id": "e4", "ts": "2026-01-01T00:00:05Z", "kind": "artifact", "title": "Artifact · report.html", "actor": "诊断专家", "task_id": "m1", "source": "test"},
        ],
        "topology": [
            {"id": "root-uuid", "title": "root-uuid", "kind": "leader", "meta": "root issue"},
            {"id": "m1", "title": "设备故障诊断 123", "kind": "member", "meta": "诊断专家"},
        ],
        "artifacts": [],
        "side_effects": [],
        "metrics": {
            "wall_seconds": 60,
            "round_sum_seconds": 60,
            "between_round_gap_seconds": 0,
            "max_round_seconds": 40,
            "tool_calls": 1,
            "exploration_calls": 0,
            "external_calls": 0,
            "artifact_count": None,
            "duplicate_effects": None,
            "leader_run_count": 2,
            "member_task_count": 1,
            "budget_seconds": 120,
        },
        "findings": [],
        "gaps": ["artifact unavailable"],
        "provenance": [],
    }


class RenderTests(unittest.TestCase):
    def test_view_aggregates_leaders_and_filters_runtime_artifacts(self) -> None:
        view = build_view(base_trace())
        self.assertEqual(len(view["lanes"]), 2)
        self.assertEqual(view["lanes"][0]["name"], "Coordinator")
        self.assertEqual(view["lanes"][0]["kind"], "leader · 2 runs · Σ 58s")
        self.assertEqual(view["initial_t"], 20.0)
        self.assertEqual(view["default_view"], "R2")
        self.assertEqual([item["id"] for item in view["zoom_ranges"]], ["session", "R1", "R2"])
        self.assertTrue(view["zoom_ranges"][2]["default"])
        self.assertEqual(len(view["tasks_detail"]), 3)
        self.assertEqual(view["rounds"][0]["label"], "确认 · 1")
        self.assertEqual(view["rounds"][1]["label"], "确认 · 2")
        self.assertEqual(view["event_stats"], {"key": 3, "all": 4, "errors": 0})
        self.assertEqual(view["topology"][0]["title"], "设备诊断 <unsafe>")
        self.assertEqual(view["hero"]["environment"], "TEST")

    def test_html_is_self_contained_and_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "story.html"
            render_html(base_trace(), str(out))
            html = out.read_text(encoding="utf-8")
        self.assertNotIn("__SESSION_STORY_JSON__", html)
        self.assertNotIn("<script src=", html)
        self.assertNotIn("<img", html)
        self.assertIn("\\u003cunsafe>", html)
        self.assertIn("session-story/v1", html)
        self.assertNotIn("TEST ENVIRONMENT", html)
        self.assertIn('id="environmentPill"', html)
        self.assertIn('id="exportBtn"', html)
        self.assertIn('id="taskDrawer"', html)
        self.assertIn('id="viewSwitch"', html)
        self.assertIn('id="rawMode"', html)

    def test_error_detection_and_raw_evidence_layer(self) -> None:
        trace = base_trace()
        trace["raw_evidence"] = {
            "session_messages": [],
            "run_events": {"r1": [{
                "type": "member:tool_result",
                "task_id": "m1",
                "created_at": "2026-01-01T00:00:04Z",
                "output": "deepexi issue: error: argument issue_command: invalid choice: 'comments'",
                "status": "completed",
            }]},
            "task_messages": {},
            "task_details": {},
            "issue_comments": {},
        }
        result = enrich_trace(trace)
        view = build_view(result)
        self.assertEqual(result["metrics"]["error_events"], 1)
        self.assertEqual(view["errors"][0]["category"], "cli_invalid_choice")
        self.assertTrue(any(item["type"] == "ERROR" and item["key"] for item in view["events"]))
        self.assertIn("run_events", view["raw_evidence"])

    def test_visual_horizon_keeps_post_round_evidence_without_changing_wall_time(self) -> None:
        trace = base_trace()
        trace["events"].append({
            "id": "e5",
            "ts": "2026-01-01T00:01:05Z",
            "kind": "artifact",
            "title": "Artifact · final-message.txt",
            "actor": "Coordinator",
            "round_id": "R2",
            "source": "test",
        })
        view = build_view(trace)
        self.assertEqual(view["wall_total"], 60.0)
        self.assertEqual(view["total"], 65.0)
        session_range = view["zoom_ranges"][0]
        r2_range = view["zoom_ranges"][2]
        self.assertEqual(session_range["end"], 65.0)
        self.assertEqual(r2_range["end"], 65.0)
        self.assertIn("轮后证据", r2_range["note"])
        self.assertEqual(view["event_stats"]["all"], 5)
        self.assertTrue(any(item["id"] == "e5" for item in view["events"]))


if __name__ == "__main__":
    unittest.main()
