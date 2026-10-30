"""The generated doctor must not fail on control-plane visibility quirks."""

import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "skill/assets/scaffold/templates/common/scripts/doctor.py"
SPEC = importlib.util.spec_from_file_location("doctor_template", DOCTOR)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def payload():
    return {
        "leader": {"agentId": "leader-1", "skill": "skills/x-orchestrate/SKILL.md"},
        "members": [
            {"role": "diagnosis", "agentId": "diag-1", "skill": "skills/x-diagnose/SKILL.md"},
        ],
    }


def agents_payload(leader_env="***"):
    return {
        "items": [
            {
                "id": "leader-1",
                "runtime_id": "runtime-1",
                "skills": ["x-orchestrate"],
                "custom_env": {"DEEPWORKS_AGENT_ID_DIAGNOSIS": leader_env},
            }
        ]
    }


RUNTIMES = {"items": [{"id": "runtime-1", "status": "online", "runtime_mode": "managed_sandbox"}]}
SQUAD = {
    "leader_agent_id": "leader-1",
    "members": [{"agent_id": "leader-1"}, {"agent_id": "diag-1"}],
}
DETAILS = [{"id": "diag-1", "runtime_id": "runtime-1", "skills": ["x-diagnose"], "custom_env": {}}]


class DoctorPreflightTests(unittest.TestCase):
    def run_check(self, details=None):
        output = io.StringIO()
        with redirect_stdout(output):
            MODULE.check_runtime_binding(payload(), agents_payload(), RUNTIMES, SQUAD, details)
        return output.getvalue()

    def test_squad_only_agent_is_accepted_from_detail_export(self):
        output = self.run_check(DETAILS)
        self.assertIn("masked by the control plane", output)

    def test_missing_agent_points_at_detail_export(self):
        with self.assertRaises(ValueError) as caught:
            self.run_check(None)
        self.assertIn("--agent-details-json", str(caught.exception))

    def test_redacted_env_is_not_reported_as_mismatch(self):
        self.run_check(DETAILS)  # would raise before the fix

    def test_real_env_mismatch_still_fails(self):
        agents = agents_payload("stale-agent")
        with self.assertRaises(ValueError) as caught:
            MODULE.check_runtime_binding(payload(), agents, RUNTIMES, SQUAD, DETAILS)
        self.assertIn("does not match", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
