import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["INDUSTRIAL_AGENT_OFFLINE"] = "1"
sys.path.insert(0, str(ROOT / "scripts"))

STATE_PATH = next((ROOT / "skills").glob("*/scripts/state.py"))
spec = importlib.util.spec_from_file_location("scaffold_state_under_test", STATE_PATH)
state_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(state_module)


class StateRecoveryTests(unittest.TestCase):
    def test_source_issue_recovers_root_without_reply_marker(self):
        root_id = "11111111-1111-4111-8111-111111111111"
        contexts = [
            {
                "input": "处理问题",
                "source_type": "issue",
                "source_ref": root_id,
                "history": [],
            }
        ]
        state = state_module.rebuild(contexts)
        self.assertEqual(state["root_issue_id"], root_id)

    def test_root_marker_wins_and_is_stable(self):
        root_id = "22222222-2222-4222-8222-222222222222"
        contexts = [
            {
                "input": "继续处理",
                "source_type": "issue",
                "source_ref": "11111111-1111-4111-8111-111111111111",
                "previous_task": "<!-- squad_root_issue_id:%s -->" % root_id,
            }
        ]
        state = state_module.rebuild(contexts)
        self.assertEqual(state["root_issue_id"], root_id)


if __name__ == "__main__":
    unittest.main()
