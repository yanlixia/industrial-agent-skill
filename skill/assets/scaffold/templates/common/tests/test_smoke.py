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
FIXTURES = ROOT / "tests" / "fixtures"


class SmokeTests(unittest.TestCase):
    def test_entry_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "turn.json"
            env = dict(os.environ)
            env["DRY_RUN"] = "1"
            command = [
                sys.executable,
                str(ENTRY),
                "--context", str(FIXTURES / "task.json"),
                "--context", str(FIXTURES / "agent.json"),
                "--context", str(FIXTURES / "previous_tasks.json"),
                "--output", str(output),
            ]
            completed = subprocess.run(command, text=True, capture_output=True, env=env, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(payload["ok"], payload)
            self.assertIn("timings", payload)
            self.assertIsInstance(payload["timings"]["total_ms"], int)
            self.assertIn("continue_loop", payload)
            self.assertIn("reply_required", payload)
            if payload.get("reply_required"):
                intent_path = Path(tmp) / "intent.json"
                draft_path = Path(tmp) / "draft.md"
                intent_path.write_text(json.dumps(payload["reply_intent"], ensure_ascii=False), encoding="utf-8")
                marker = payload["reply_intent"].get("frozen", {}).get("marker", "")
                draft_path.write_text((marker + "\n\n已处理示例任务。").strip(), encoding="utf-8")
                check = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "validate_reply.py"), "--intent", str(intent_path), "--rendered", str(draft_path)],
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(check.returncode, 0, check.stdout + check.stderr)


if __name__ == "__main__":
    unittest.main()
