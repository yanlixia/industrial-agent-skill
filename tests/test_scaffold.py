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
        self.assertTrue((target / "docs/quality-gates.md").is_file())
        for contract in (
            "contracts/expert-contract.schema.json",
            "contracts/performance-budget.json",
            "contracts/evaluation-suite.json",
        ):
            self.assertTrue((target / contract).is_file(), contract)
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

    def test_doctor_rejects_missing_quality_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "single"
            self.generate(
                "--mode", "single", "--domain", "energy", "--title", "能源专家",
                "--target", str(target),
            )
            (target / "contracts/performance-budget.json").unlink()
            doctor = subprocess.run(
                [sys.executable, "scripts/doctor.py"],
                cwd=target, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(doctor.returncode, 0)
            self.assertIn("performance budget missing", doctor.stdout)

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

    def test_runtime_snapshot_gate_requires_online_managed_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "team"
            self.generate(
                "--mode", "team", "--domain", "energy", "--title", "能源专家团",
                "--roles", "diagnosis,maintenance,monitor",
                "--leader-agent-id", "leader-id",
                "--agent-ids", "diagnosis=diag-id,maintenance=fix-id,monitor=watch-id",
                "--squad-id", "squad-id", "--target", str(target),
            )
            agents = [
                {
                    "id": "leader-id", "runtime_id": "r-leader", "runtime_mode": "managed_sandbox",
                    "skills": [{"name": "energy-orchestrate"}],
                    "custom_env": {
                        "DEEPWORKS_AGENT_ID_DIAGNOSIS": "diag-id",
                        "DEEPWORKS_AGENT_ID_MAINTENANCE": "fix-id",
                        "DEEPWORKS_AGENT_ID_MONITOR": "watch-id",
                    },
                },
                {"id": "diag-id", "runtime_id": "r-diag", "runtime_mode": "managed_sandbox", "skills": [{"name": "diagnosis"}]},
                {"id": "fix-id", "runtime_id": "r-fix", "runtime_mode": "managed_sandbox", "skills": [{"name": "maintenance"}]},
                {"id": "watch-id", "runtime_id": "r-watch", "runtime_mode": "managed_sandbox", "skills": [{"name": "monitor"}]},
            ]
            runtimes = [
                {"id": "r-leader", "status": "online", "runtime_mode": "managed_sandbox"},
                {"id": "r-diag", "status": "online", "runtime_mode": "managed_sandbox"},
                {"id": "r-fix", "status": "online", "runtime_mode": "managed_sandbox"},
                {"id": "r-watch", "status": "online", "runtime_mode": "managed_sandbox"},
            ]
            agents_path = Path(tmp) / "agents.json"
            runtimes_path = Path(tmp) / "runtimes.json"
            squad_path = Path(tmp) / "squad.json"
            agents_path.write_text(json.dumps(agents), encoding="utf-8")
            runtimes_path.write_text(json.dumps(runtimes), encoding="utf-8")
            squad_path.write_text(json.dumps({
                "id": "squad-id",
                "leader_agent_id": "leader-id",
                "members": [
                    {"member_type": "agent", "member_id": agent_id}
                    for agent_id in ("leader-id", "diag-id", "fix-id", "watch-id")
                ],
            }), encoding="utf-8")
            healthy = subprocess.run(
                [
                    sys.executable, "scripts/doctor.py",
                    "--agents-json", str(agents_path),
                    "--runtimes-json", str(runtimes_path),
                    "--squad-json", str(squad_path),
                ],
                cwd=target, text=True, capture_output=True, check=False,
            )
            self.assertEqual(healthy.returncode, 0, healthy.stdout + healthy.stderr)

            runtimes[1]["config"] = {
                "network": {"mode": "disabled"},
                "lifecycle": {"mode": "session"},
                "volume": {"mode": "task_volume"},
            }
            runtimes_path.write_text(json.dumps(runtimes), encoding="utf-8")
            warned = subprocess.run(
                [
                    sys.executable, "scripts/doctor.py",
                    "--agents-json", str(agents_path),
                    "--runtimes-json", str(runtimes_path),
                    "--squad-json", str(squad_path),
                ],
                cwd=target, text=True, capture_output=True, check=False,
            )
            self.assertEqual(warned.returncode, 0, warned.stdout + warned.stderr)
            self.assertIn("network.mode=disabled", warned.stdout)
            self.assertIn("volume.mode=task_volume", warned.stdout)

            runtimes[1]["config"]["lifecycle"]["idle_ttl_seconds"] = 5
            runtimes_path.write_text(json.dumps(runtimes), encoding="utf-8")
            bad_ttl = subprocess.run(
                [
                    sys.executable, "scripts/doctor.py",
                    "--agents-json", str(agents_path),
                    "--runtimes-json", str(runtimes_path),
                    "--squad-json", str(squad_path),
                ],
                cwd=target, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(bad_ttl.returncode, 0)
            self.assertIn("out of platform range", bad_ttl.stdout)
            runtimes[1]["config"] = {}

            runtimes[1]["status"] = "offline"
            runtimes_path.write_text(json.dumps(runtimes), encoding="utf-8")
            offline = subprocess.run(
                [
                    sys.executable, "scripts/doctor.py",
                    "--agents-json", str(agents_path),
                    "--runtimes-json", str(runtimes_path),
                    "--squad-json", str(squad_path),
                ],
                cwd=target, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(offline.returncode, 0)
            self.assertIn("not online", offline.stdout)

            runtimes[1]["status"] = "online"
            runtimes_path.write_text(json.dumps(runtimes), encoding="utf-8")
            agents[0]["custom_env"]["DEEPWORKS_AGENT_ID_DIAGNOSIS"] = "stale-id"
            agents_path.write_text(json.dumps(agents), encoding="utf-8")
            stale = subprocess.run(
                [
                    sys.executable, "scripts/doctor.py",
                    "--agents-json", str(agents_path),
                    "--runtimes-json", str(runtimes_path),
                    "--squad-json", str(squad_path),
                ],
                cwd=target, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("does not match current squad agentId", stale.stdout)

    def test_doctor_rejects_hardcoded_agent_uuid_in_dispatch_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "team"
            self.generate(
                "--mode", "team", "--domain", "energy", "--title", "能源专家团",
                "--roles", "diagnosis,maintenance",
                "--leader-agent-id", "leader-id",
                "--agent-ids", "diagnosis=diag-id,maintenance=fix-id",
                "--squad-id", "squad-id", "--target", str(target),
            )
            dispatch = target / "skills" / "energy-orchestrate" / "scripts" / "dispatch.py"
            dispatch.write_text(
                dispatch.read_text(encoding="utf-8")
                + '\nDEFAULT_DIAGNOSIS_AGENT_ID = "417a46df-6ea3-4804-ae06-58a137b380ae"\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, "scripts/doctor.py"],
                cwd=target, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("hardcoded UUID", result.stdout)
            self.assertIn("DEEPWORKS_AGENT_ID_", result.stdout)



if __name__ == "__main__":
    unittest.main()
