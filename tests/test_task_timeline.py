import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "task_timeline.py"
NOW = "2026-10-28T08:00:00Z"


def task(**overrides):
    base = {
        "id": "t" * 8,
        "agent_id": "a" * 8,
        "runtime_id": "r" * 8,
        "status": "completed",
        "created_at": "2026-10-28T07:00:00Z",
        "claimed_at": "2026-10-28T07:00:02Z",
        "started_at": "2026-10-28T07:00:20Z",
        "finished_at": "2026-10-28T07:01:20Z",
    }
    base.update(overrides)
    return base


class TaskTimelineTests(unittest.TestCase):
    def run_tool(self, payload, *args):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task-runs.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(TOOL), str(path), "--now", NOW, *args],
                text=True, capture_output=True, check=False,
            )

    def run_files(self, payloads, *args):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for index, payload in enumerate(payloads):
                path = Path(tmp) / f"session-{index}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                paths.append(str(path))
            return subprocess.run(
                [sys.executable, str(TOOL), *paths, "--now", NOW, *args],
                text=True, capture_output=True, check=False,
            )

    def test_healthy_timeline_passes_strict(self):
        result = self.run_tool([task()], "--strict")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("completed", result.stdout)

    def test_never_claimed_is_flagged(self):
        queued = task(
            status="queued",
            created_at="2026-10-28T07:50:00Z",
            claimed_at=None,
            started_at=None,
            finished_at=None,
        )
        result = self.run_tool({"items": [queued]}, "--strict")
        self.assertEqual(result.returncode, 1)
        self.assertIn("never_claimed", result.stdout)
        self.assertIn("squad-issue serialization", result.stdout)

    def test_slow_claim_is_flagged_even_if_completed(self):
        slow = task(claimed_at="2026-10-28T07:00:45Z")
        result = self.run_tool([slow], "--json")
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        row = payload["tasks"][0]
        self.assertEqual(row["claim_seconds"], 45.0)
        self.assertTrue(any("claim_slow" in item for item in row["findings"]))

    def test_api_field_names_dispatched_at_and_completed_at_are_audited(self):
        api_task = task(
            created_at="2026-10-28T07:00:00Z",
            claimed_at=None,
            dispatched_at="2026-10-28T07:00:02Z",
            started_at="2026-10-28T07:00:20Z",
            finished_at=None,
            completed_at="2026-10-28T07:01:20Z",
        )
        result = self.run_tool([api_task], "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        row = json.loads(result.stdout)["tasks"][0]
        self.assertEqual(row["claim_seconds"], 2.0)
        self.assertEqual(row["run_seconds"], 60.0)

    def test_p95_session_budget_and_sample_gate_are_enforced(self):
        first = task(id="task-a", finished_at="2026-10-28T07:01:20Z")
        second = task(
            id="task-b",
            created_at="2026-10-28T07:10:00Z",
            claimed_at="2026-10-28T07:10:03Z",
            started_at="2026-10-28T07:10:30Z",
            finished_at="2026-10-28T07:13:10Z",
        )
        gated = self.run_files(
            [[first], [second]], "--strict", "--min-samples", "3"
        )
        self.assertEqual(gated.returncode, 1, gated.stdout + gated.stderr)
        self.assertIn("insufficient_samples", gated.stdout)
        passed = self.run_files(
            [[first], [second]],
            "--strict",
            "--min-samples",
            "2",
            "--total-budget",
            "200",
        )
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)

    def test_naive_platform_timestamps_are_local_not_utc(self):
        invoked = task(
            status="queued",
            created_at="2026-10-28T15:50:00",
            claimed_at=None,
            started_at=None,
            finished_at=None,
        )
        result = self.run_tool([invoked], "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["sessions"][0]["window_seconds"], 600.0)
        self.assertTrue(any("never_claimed" in f for f in payload["tasks"][0]["findings"]))

    def test_rejects_unparseable_payload(self):
        result = self.run_tool({"unexpected": True})
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
