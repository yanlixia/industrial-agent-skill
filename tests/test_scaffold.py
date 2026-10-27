import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD = ROOT / "tools" / "scaffold.py"


class ScaffoldAcceptanceTests(unittest.TestCase):
    def generate(self, *args):
        completed = subprocess.run(
            [sys.executable, str(SCAFFOLD), *args],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)

    def verify_generated_repo(self, target: Path, mode: str):
        doctor = subprocess.run(
            [sys.executable, "scripts/doctor.py"],
            cwd=target,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(doctor.returncode, 0, doctor.stderr + doctor.stdout)
        tests = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=target,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(tests.returncode, 0, tests.stderr + tests.stdout)
        marker = subprocess.run(
            [sys.executable, "scripts/check_marker_contract.py", "."],
            cwd=target,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(marker.returncode, 0, marker.stderr + marker.stdout)
        manifest = json.loads((target / "deepworks.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["mode"], mode)
        self.assertTrue((target / "docs/checklist.md").is_file())
        self.assertTrue((target / "docs/deploy.md").is_file())
        self.assertTrue((target / ".industrial-agent-scaffold.json").is_file())
        for skill in manifest["skills"]:
            scripts = (target / skill).parent / "scripts"
            self.assertTrue((scripts / "runtime.py").is_file(), scripts)
            self.assertTrue((scripts / "validate_reply.py").is_file(), scripts)
        if mode == "team":
            config_path = target / "skills" / ("%s-orchestrate" % manifest["domain"]) / "config" / "team.json"
            self.assertTrue(config_path.is_file())
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["schema"], "industrial-agent/team-runtime/1")
            self.assertEqual(
                [member["role"] for member in config["members"]],
                [member["role"] for member in manifest["members"]],
            )

    def test_single_expert_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "single"
            self.generate(
                "--mode", "single", "--domain", "energy", "--title", "能源专家",
                "--target", str(target),
            )
            self.verify_generated_repo(target, "single")
            self.assertFalse((target / "experts").exists())

    def test_expert_team_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "team"
            self.generate(
                "--mode", "team", "--domain", "energy", "--title", "能源专家团",
                "--roles", "diagnosis,maintenance,monitor", "--target", str(target),
            )
            self.verify_generated_repo(target, "team")
            for role in ("diagnosis", "maintenance", "monitor"):
                self.assertTrue((target / "experts" / role / "AGENTS.md").is_file())
                self.assertTrue(
                    (target / "experts" / role / "skills" / role / "scripts" / "run.py").is_file()
                )
            self.assertTrue((target / "contracts" / "dispatch.schema.json").is_file())

    def test_release_gate_requires_real_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "team"
            self.generate(
                "--mode", "team", "--domain", "energy", "--title", "能源专家团",
                "--roles", "diagnosis,maintenance,monitor", "--target", str(target),
            )
            release = subprocess.run(
                [sys.executable, "scripts/doctor.py", "--release"],
                cwd=target, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(release.returncode, 0)
            self.assertIn("release requires", release.stdout)

            filled = Path(tmp) / "filled"
            self.generate(
                "--mode", "team", "--domain", "energy", "--title", "能源专家团",
                "--roles", "diagnosis,maintenance,monitor",
                "--leader-agent-id", "leader-id",
                "--agent-ids", "diagnosis=diag-id,maintenance=fix-id,monitor=watch-id",
                "--squad-id", "squad-id", "--target", str(filled),
            )
            release = subprocess.run(
                [sys.executable, "scripts/doctor.py", "--release"],
                cwd=filled, text=True, capture_output=True, check=False,
            )
            self.assertEqual(release.returncode, 0, release.stdout + release.stderr)
            config = json.loads(
                (filled / "skills" / "energy-orchestrate" / "config" / "team.json").read_text(encoding="utf-8")
            )
            self.assertEqual(config["squadId"], "squad-id")
            self.assertEqual(
                {member["role"]: member["agentId"] for member in config["members"]},
                {"diagnosis": "diag-id", "maintenance": "fix-id", "monitor": "watch-id"},
            )

    def test_force_refuses_unowned_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "not-ours"
            target.mkdir()
            (target / "keep.txt").write_text("do not delete", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable, str(SCAFFOLD), "--mode", "single",
                    "--domain", "energy", "--title", "能源专家",
                    "--target", str(target), "--force",
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertTrue((target / "keep.txt").is_file())

    def test_team_rejects_duplicate_roles_and_agent_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            duplicate_roles = subprocess.run(
                [
                    sys.executable, str(SCAFFOLD), "--mode", "team",
                    "--domain", "energy", "--title", "能源专家团",
                    "--roles", "diagnosis,diagnosis",
                    "--target", str(Path(tmp) / "roles"),
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(duplicate_roles.returncode, 0)
            self.assertIn("unique", duplicate_roles.stderr)

            duplicate_ids = subprocess.run(
                [
                    sys.executable, str(SCAFFOLD), "--mode", "team",
                    "--domain", "energy", "--title", "能源专家团",
                    "--roles", "diagnosis,maintenance",
                    "--agent-ids", "diagnosis=same-id,maintenance=same-id",
                    "--target", str(Path(tmp) / "ids"),
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(duplicate_ids.returncode, 0)
            self.assertIn("distinct", duplicate_ids.stderr)

    def test_release_gate_rejects_stale_runtime_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "team"
            self.generate(
                "--mode", "team", "--domain", "energy", "--title", "能源专家团",
                "--roles", "diagnosis,maintenance,monitor",
                "--leader-agent-id", "leader-id",
                "--agent-ids", "diagnosis=diag-id,maintenance=fix-id,monitor=watch-id",
                "--squad-id", "squad-id", "--target", str(target),
            )
            config_path = target / "skills" / "energy-orchestrate" / "config" / "team.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["members"][0]["agentId"] = "stale-id"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            release = subprocess.run(
                [sys.executable, "scripts/doctor.py", "--release"],
                cwd=target, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(release.returncode, 0)
            self.assertIn("does not match", release.stdout)


if __name__ == "__main__":
    unittest.main()
