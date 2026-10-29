import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
STORYBOARD_SCRIPTS = ROOT / "session-storyboard" / "scripts"
sys.path.insert(0, str(STORYBOARD_SCRIPTS))

import feedback_issue


CANDIDATE = {
    "schema_version": feedback_issue.SCHEMA_VERSION,
    "scope": "general",
    "title": "Avoid duplicate member hops",
    "observation": "Two member tasks cover one business root. password=secret appears in raw context.",
    "mechanism": "A second member hop pays platform claim and model-turn costs without adding a business capability.",
    "recommendation": "Keep confirmation and execution in one self-contained member command when they share an idempotency key.",
    "validation": "Compare child count and end-to-end duration in a new DeepWorks test session.",
    "affected_areas": ["scaffold", "test"],
    "evidence": [{"ref": "task:t1", "summary": "Two member runs share the same child issue."}],
}


TRACE = {
    "schema_version": "session-story/v1",
    "target": {"id": "session-1", "kind": "squad_session", "title": "Demo"},
    "metrics": {"wall_seconds": 120, "max_round_seconds": 90},
}


class FeedbackCandidateTests(unittest.TestCase):
    def test_valid_candidate_renders_redacted_idempotent_issue(self):
        first = feedback_issue.feedback_fingerprint(CANDIDATE)
        second = feedback_issue.feedback_fingerprint(dict(reversed(list(CANDIDATE.items()))))
        self.assertEqual(first, second)

        body = feedback_issue.render_issue_body(CANDIDATE, TRACE)
        self.assertIn(f"session-storyboard-feedback-id:{first}", body)
        self.assertIn("password=[REDACTED]", body)
        self.assertIn("`task:t1`", body)

    def test_rejects_session_specific_or_unevidenced_candidate(self):
        scoped = dict(CANDIDATE, scope="session")
        with self.assertRaises(feedback_issue.FeedbackError):
            feedback_issue.validate_candidate(scoped)

        unevidenced = dict(CANDIDATE, evidence=[])
        with self.assertRaises(feedback_issue.FeedbackError):
            feedback_issue.validate_candidate(unevidenced)


class FeedbackIssueCliTests(unittest.TestCase):
    def write_inputs(self, tmp: Path):
        candidate = tmp / "candidate.json"
        trace = tmp / "trace.json"
        candidate.write_text(json.dumps(CANDIDATE, ensure_ascii=False), encoding="utf-8")
        trace.write_text(json.dumps(TRACE), encoding="utf-8")
        return candidate, trace

    def test_existing_fingerprint_does_not_create_another_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate, trace = self.write_inputs(Path(tmp))
            fingerprint = feedback_issue.feedback_fingerprint(CANDIDATE)
            commands = []

            def fake_run(command, **_):
                commands.append(command)
                self.assertTrue(command[0].endswith("/gh-test"))
                if command[1:3] == ["issue", "list"]:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout=json.dumps([
                            {
                                "number": 7,
                                "url": "https://example/7",
                                "body": f"session-storyboard-feedback-id:{fingerprint}",
                            }
                        ]),
                        stderr="",
                    )
                raise AssertionError("issue create must not run when an existing issue matches")

            with patch.object(feedback_issue.subprocess, "run", side_effect=fake_run):
                result = feedback_issue.main([
                    "--candidate", str(candidate), "--trace", str(trace),
                    "--gh", "/bin/gh-test", "--repo", "owner/repo",
                ])

            self.assertEqual(result, 0)
            self.assertEqual(len(commands), 1)

    def test_new_candidate_creates_one_issue_with_marker_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate, trace = self.write_inputs(Path(tmp))
            commands = []
            bodies = []

            def fake_run(command, **_):
                commands.append(command)
                if command[1:3] == ["issue", "list"]:
                    return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
                if command[1:3] == ["issue", "create"]:
                    bodies.append(Path(command[command.index("--body-file") + 1]).read_text(encoding="utf-8"))
                    return subprocess.CompletedProcess(
                        command, 0, stdout="https://example/new", stderr=""
                    )
                raise AssertionError(command)

            with patch.object(feedback_issue.subprocess, "run", side_effect=fake_run):
                result = feedback_issue.main([
                    "--candidate", str(candidate), "--trace", str(trace),
                    "--gh", "/bin/gh-test", "--repo", "owner/repo",
                ])

            self.assertEqual(result, 0)
            self.assertEqual(len(commands), 2)
            self.assertTrue(commands[1][1:3] == ["issue", "create"])
            self.assertIn("session-storyboard-feedback-id:", bodies[0])


class BundleInstallTests(unittest.TestCase):
    def test_installer_bundles_both_skills_and_runs_storyboard_tests(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('STORYBOARD_NAME="session-storyboard"', installer)
        self.assertIn('"${STORYBOARD_TARGET}/tests"', installer)
        self.assertIn('"${STORYBOARD_LINK}"', installer)
        self.assertTrue((ROOT / "session-storyboard" / "references" / "feedback-loop.md").is_file())


if __name__ == "__main__":
    unittest.main()
