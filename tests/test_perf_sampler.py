"""perf_sampler must turn probe payloads into an honest P95 verdict."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "perf_sampler.py"

FAKE_PROBE = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]


def value(flag, default=""):
    return args[args.index(flag) + 1] if flag in args else default


state_path = Path(os.environ["FAKE_PROBE_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"calls": 0}
state["calls"] += 1
state_path.write_text(json.dumps(state), encoding="utf-8")

label = value("--round")
session = value("--session")
fail_label = os.environ.get("FAKE_PROBE_FAIL_ROUND", "")
status = "failed" if label == fail_label else "completed"
total = float(os.environ.get("FAKE_PROBE_SECONDS", "30")) + state["calls"]
print(json.dumps({
    "round": label,
    "message_seen": value("--message"),
    "session_id": session or ("sess-%d" % state["calls"]),
    "terminal_status": status,
    "failure_code": "boom" if status == "failed" else None,
    "timings": {"total_seconds": total},
}, ensure_ascii=False))
'''


class PerfSamplerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.probe = self.base / "probe.py"
        self.probe.write_text(FAKE_PROBE, encoding="utf-8")
        self.state = self.base / "state.json"
        self.out = self.base / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def run_sampler(self, *extra, env=None):
        command = [
            sys.executable, str(SCRIPT),
            "--round", "query:查询 维修工单",
            "--round", "selection:1",
            "--samples", "3",
            "--out-dir", str(self.out),
            "--squad", "squad-1",
            "--min-samples", "3",
            "--probe-cmd",
            "%s %s --round {label} --message {message} {session}" % (sys.executable, self.probe),
        ]
        command.extend(extra)
        full_env = dict(os.environ)
        full_env["FAKE_PROBE_STATE"] = str(self.state)
        full_env.update(env or {})
        return subprocess.run(command, capture_output=True, text=True, env=full_env, check=False)

    def summary(self):
        return json.loads((self.out / "summary.json").read_text(encoding="utf-8"))

    def test_multi_round_samples_reuse_one_session(self):
        completed = self.run_sampler()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = self.summary()
        self.assertEqual(report["summary"]["complete_samples"], 3)
        self.assertEqual(report["findings"], [])
        first = json.loads((self.out / "sample-01-query.json").read_text(encoding="utf-8"))
        second = json.loads((self.out / "sample-01-selection.json").read_text(encoding="utf-8"))
        self.assertEqual(first["message_seen"], "查询 维修工单")
        self.assertEqual(second["session_id"], first["session_id"])

    def test_insufficient_samples_is_not_a_pass(self):
        completed = self.run_sampler("--min-samples", "20")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("insufficient_samples", " ".join(self.summary()["findings"]))

    def test_failed_round_aborts_and_reports(self):
        completed = self.run_sampler(env={"FAKE_PROBE_FAIL_ROUND": "selection"})
        self.assertEqual(completed.returncode, 1)
        report = self.summary()
        self.assertTrue(report["aborted"])
        self.assertEqual(report["summary"]["failures"][0]["round"], "selection")

    def test_budget_violation_is_a_finding(self):
        completed = self.run_sampler("--round-budget", "10", env={"FAKE_PROBE_SECONDS": "50"})
        self.assertEqual(completed.returncode, 1)
        self.assertIn("p95_slow", " ".join(self.summary()["findings"]))


if __name__ == "__main__":
    unittest.main()
