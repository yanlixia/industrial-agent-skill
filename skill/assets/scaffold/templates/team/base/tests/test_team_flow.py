import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("INDUSTRIAL_AGENT_OFFLINE", "1")
MANIFEST = json.loads((ROOT / "deepworks.manifest.json").read_text(encoding="utf-8"))
ENTRY = ROOT / MANIFEST["entry"]
SCRIPT_DIR = ENTRY.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(ROOT / "scripts"))

import plan  # noqa: E402
import state as state_module  # noqa: E402


class TeamFlowTests(unittest.TestCase):
    def test_serial_plan_dispatches_one_ready_role(self):
        action = plan.next_action({}, "处理设备故障")
        self.assertEqual(action["action"], "dispatch")
        self.assertEqual(action["role"], plan.ROLES[0])

    def test_plan_waits_for_active_member(self):
        action = plan.next_action(
            {
                "active": {plan.ROLES[0]: {"status": "in_progress"}},
                "dispatched_roles": {plan.ROLES[0]},
                "completed": {},
                "failed": {},
            },
            "处理设备故障",
        )
        self.assertEqual(action["action"], "wait")

    def test_done_without_structured_result_is_not_success(self):
        child = {"role": plan.ROLES[0], "status": "done", "result": None}
        state = state_module.rebuild(
            [
                {
                    "input": "处理设备故障",
                    "source_type": "issue",
                    "source_ref": "11111111-1111-4111-8111-111111111111",
                    "team_children": [child],
                }
            ]
        )
        self.assertIn(plan.ROLES[0], state["failed"])

    def test_latest_success_wins_over_an_older_failure(self):
        role = plan.ROLES[0]
        state = state_module.rebuild(
            [
                {
                    "input": "处理设备故障",
                    "team_children": [
                        {"role": role, "status": "failed"},
                        {"role": role, "status": "done", "result": {"summary": "已完成"}},
                    ],
                }
            ]
        )
        self.assertIn(role, state["completed"])
        self.assertNotIn(role, state["failed"])

    def test_plan_finishes_after_all_members(self):
        completed = {
            role: {"status": "done", "result": {"summary": role + " 完成"}}
            for role in plan.ROLES
        }
        action = plan.next_action(
            {
                "active": {},
                "dispatched_roles": set(plan.ROLES),
                "completed": completed,
                "failed": {},
            },
            "处理设备故障",
        )
        self.assertEqual(action["action"], "final")
        for role in plan.ROLES:
            self.assertIn(role + " 完成", action["text"])

    def _run_dispatch_turn(self, tmp, extra_env=None):
        task = {
            "input": "处理设备故障",
            "source_type": "issue",
            "source_ref": "11111111-1111-4111-8111-111111111111",
        }
        task_path = Path(tmp) / "task.json"
        output = Path(tmp) / "turn.json"
        task_path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ)
        env["DRY_RUN"] = "1"
        env.update(extra_env or {})
        process = subprocess.run(
            [sys.executable, str(ENTRY), "--context", str(task_path), "--output", str(output)],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(output.read_text(encoding="utf-8"))

    def test_missing_member_binding_is_a_configuration_error(self):
        import turn as turn_module

        saved = os.environ.pop("DRY_RUN", None)
        try:
            dispatched, error, error_class = turn_module._dispatch("no-such-role", "root-1", {})
        finally:
            if saved is not None:
                os.environ["DRY_RUN"] = saved
        self.assertIsNone(dispatched)
        self.assertIn("missing agent id", error)
        self.assertEqual(error_class, "configuration")

    def test_default_dispatch_replies_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._run_dispatch_turn(tmp)
            self.assertTrue(payload["ok"], payload)
            self.assertTrue(payload["reply_required"])
            self.assertFalse(payload["continue_loop"])
            self.assertEqual(payload["reply_intent"]["kind"], "progress")

    def test_inline_wait_hides_progress_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._run_dispatch_turn(
                tmp, {"TEAM_INLINE_WAIT": "1", "TEAM_SLICE_SECONDS": "0.05"}
            )
            self.assertTrue(payload["ok"], payload)
            self.assertTrue(payload["continue_loop"])
            self.assertFalse(payload["reply_required"])
            self.assertNotIn("reply_intent", payload)

    def test_stalled_member_is_reported_without_redispatch(self):
        role = plan.ROLES[0]
        child = {
            "id": "child-1",
            "role": role,
            "status": "queued",
            "created_at": "2020-01-01T00:00:00Z",
        }
        state = state_module.rebuild(
            [{"input": "处理设备故障", "team_children": [child]}]
        )
        self.assertIn(role, state["stalled"])
        action = plan.next_action(state, "处理设备故障")
        self.assertEqual(action["action"], "member_stalled")
        self.assertEqual(action["role"], role)
        self.assertEqual(action["error_class"], "member_runtime_stalled")

    def test_entry_surfaces_member_runtime_stalled(self):
        role = plan.ROLES[0]
        task = {
            "input": "处理设备故障",
            "source_type": "issue",
            "source_ref": "11111111-1111-4111-8111-111111111111",
            "team_children": [
                {
                    "id": "child-1",
                    "role": role,
                    "status": "queued",
                    "created_at": "2020-01-01T00:00:00Z",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            task_path = Path(tmp) / "task.json"
            output = Path(tmp) / "turn.json"
            task_path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
            env = dict(os.environ)
            env["DRY_RUN"] = "1"
            process = subprocess.run(
                [
                    sys.executable,
                    str(ENTRY),
                    "--context",
                    str(task_path),
                    "--output",
                    str(output),
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(payload["ok"], payload)
            self.assertEqual(payload["error"]["class"], "member_runtime_stalled")
            self.assertEqual(payload["reply_intent"]["kind"], "error")

    def test_entry_returns_final_from_child_results(self):
        completed = [
            {
                "role": role,
                "status": "done",
                "result": {"summary": role + " 已完成", "facts": {}},
            }
            for role in plan.ROLES
        ]
        task = {
            "input": "处理设备故障",
            "source_type": "issue",
            "source_ref": "11111111-1111-4111-8111-111111111111",
            "team_children": completed,
        }
        with tempfile.TemporaryDirectory() as tmp:
            task_path = Path(tmp) / "task.json"
            output = Path(tmp) / "turn.json"
            task_path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
            env = dict(os.environ)
            env["DRY_RUN"] = "1"
            completed_process = subprocess.run(
                [
                    sys.executable,
                    str(ENTRY),
                    "--context",
                    str(task_path),
                    "--output",
                    str(output),
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(completed_process.returncode, 0, completed_process.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(payload["ok"], payload)
            self.assertEqual(payload["reply_intent"]["kind"], "final")
            for role in plan.ROLES:
                self.assertIn(role + " 已完成", payload["reply_intent"]["tone_context"]["draft"])


if __name__ == "__main__":
    unittest.main()
