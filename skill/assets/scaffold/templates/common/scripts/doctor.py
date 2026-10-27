#!/usr/bin/env python3
"""Local checks, including a simulated skill-only deployment layout."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deepworks.manifest.json"


def fail(message):
    print("doctor failed: %s" % message)
    return 1


def skill_root_for(path):
    if path.parent.name == "scripts":
        return path.parent.parent
    return path.parent


def check_skill_files(skill_md):
    scripts = skill_root_for(skill_md) / "scripts"
    for name in ("runtime.py", "validate_reply.py"):
        if not (scripts / name).is_file():
            raise ValueError("skill is not self-contained: missing %s beside %s" % (name, skill_md))


def deployed_skill_copy(skill_md, tmp):
    source = skill_root_for(skill_md)
    target = Path(tmp) / "skills" / source.name
    shutil.copytree(source, target)
    return target


def run_script(script, args, tmp, env):
    completed = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=tmp,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("script failed: %s\n%s" % (script, completed.stderr or completed.stdout))
    return completed


def check_turn(skill_md, tmp, env):
    skill_copy = deployed_skill_copy(skill_md, tmp)
    output = Path(tmp) / "turn.json"
    run_script(
        skill_copy / "scripts" / "turn.py",
        [
            "--context", str(ROOT / "tests/fixtures/task.json"),
            "--context", str(ROOT / "tests/fixtures/agent.json"),
            "--context", str(ROOT / "tests/fixtures/previous_tasks.json"),
            "--output", str(output),
        ],
        tmp,
        env,
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise ValueError("entry returned ok=false: %s" % result.get("error"))
    for key in ("continue_loop", "reply_required", "timings"):
        if key not in result:
            raise ValueError("turn result missing %s" % key)
    if result.get("reply_required"):
        intent_path = Path(tmp) / "intent.json"
        draft_path = Path(tmp) / "draft.md"
        intent_path.write_text(json.dumps(result.get("reply_intent") or {}, ensure_ascii=False), encoding="utf-8")
        marker = str(((result.get("reply_intent") or {}).get("frozen") or {}).get("marker") or "")
        draft_path.write_text((marker + "\n\n已完成示例任务。").strip(), encoding="utf-8")
        run_script(
            skill_copy / "scripts" / "validate_reply.py",
            ["--intent", str(intent_path), "--rendered", str(draft_path)],
            tmp,
            env,
        )


def check_member(member, tmp, env):
    skill_md = ROOT / str(member.get("skill") or "")
    role = str(member.get("role") or "")
    root_id = "11111111-1111-4111-8111-111111111111"
    child_id = "22222222-2222-4222-8222-222222222222"
    task = {
        "id": "33333333-3333-4333-8333-333333333333",
        "source_type": "issue",
        "source_ref": child_id,
        "input": (
            "ISS-2: [%s] %s 任务\n\n角色：%s\n\n"
            '{"request":"测试成员任务","completed_roles":[]}\n\n'
            "<!-- team_root_issue_id:%s -->\n"
            "<!-- team_role:%s -->\n"
            "<!-- role_dispatch_key:test-key -->\n"
        ) % (role, role, role, root_id, role),
    }
    task_path = Path(tmp) / ("member-%s.json" % role)
    output = Path(tmp) / ("member-%s-result.json" % role)
    task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    skill_copy = deployed_skill_copy(skill_md, tmp)
    run_script(
        skill_copy / "scripts" / "run.py",
        ["--context", str(task_path), "--output", str(output)],
        tmp,
        env,
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    if not result.get("ok") or result.get("role") != role:
        raise ValueError("member %s returned invalid result: %s" % (role, result))
    summary = ((result.get("result") or {}).get("summary") or "")
    if "测试成员任务" not in summary:
        raise ValueError("member %s did not consume the child input payload" % role)
    artifact = Path(tmp) / "artifacts" / "member-result.json"
    if not artifact.is_file():
        raise ValueError("member %s did not write artifacts/member-result.json" % role)
    artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
    if artifact_payload.get("ok") is not True or artifact_payload.get("role") != role:
        raise ValueError("member %s wrote an invalid result artifact" % role)


def check_team_config(payload):
    leader_skill = ROOT / str((payload.get("leader") or {}).get("skill") or "")
    config = skill_root_for(leader_skill) / "config/team.json"
    if not config.is_file():
        raise ValueError("team runtime config missing: %s" % config)
    team = json.loads(config.read_text(encoding="utf-8"))
    if team.get("schema") != "industrial-agent/team-runtime/1":
        raise ValueError("team runtime config has an unsupported schema")
    if "squadId" not in team:
        raise ValueError("team runtime config is missing squadId")
    rows = [item for item in team.get("members") or [] if isinstance(item, dict)]
    configured = {str(item.get("role") or ""): item for item in rows}
    expected = [str(member.get("role") or "") for member in payload.get("members") or []]
    if len(rows) != len(configured) or sorted(configured) != sorted(expected):
        raise ValueError("team runtime config roles do not match the manifest")
    return config, team


def check_quality_contracts():
    required = {
        "contracts/expert-contract.schema.json": "expert contract schema",
        "contracts/performance-budget.json": "performance budget",
        "contracts/evaluation-suite.json": "evaluation suite",
        "docs/quality-gates.md": "quality gate documentation",
    }
    for path, label in required.items():
        if not (ROOT / path).is_file():
            raise ValueError("%s missing: %s" % (label, path))
    budget = json.loads((ROOT / "contracts/performance-budget.json").read_text(encoding="utf-8"))
    if int((budget.get("userTurn") or {}).get("maxSeconds") or 0) <= 0:
        raise ValueError("performance budget must define a positive userTurn.maxSeconds")
    if int((budget.get("toolCall") or {}).get("maxCountPerTurn") or 0) <= 0:
        raise ValueError("performance budget must define a positive toolCall.maxCountPerTurn")
    evaluation = json.loads((ROOT / "contracts/evaluation-suite.json").read_text(encoding="utf-8"))
    for key in ("golden", "edgeCases", "concurrency"):
        if not isinstance(evaluation.get(key), list) or not evaluation[key]:
            raise ValueError("evaluation suite requires at least one %s case" % key)


def check_release_ids(payload):
    if payload.get("mode") == "single":
        if not str((payload.get("leader") or {}).get("agentId") or "").strip():
            raise ValueError("release requires leader.agentId for a single expert")
        return
    if not str((payload.get("squad") or {}).get("id") or "").strip():
        raise ValueError("release requires squad.id")
    if not str((payload.get("leader") or {}).get("agentId") or "").strip():
        raise ValueError("release requires leader.agentId for a team")
    for member in payload.get("members") or []:
        if not str(member.get("agentId") or "").strip():
            raise ValueError("release requires agentId for member %s" % member.get("role"))
    config, team = check_team_config(payload)
    if not str(team.get("squadId") or "").strip():
        raise ValueError("release requires squadId in %s" % config)
    configured = {
        str(item.get("role") or ""): str(item.get("agentId") or "").strip()
        for item in team.get("members") or []
        if isinstance(item, dict)
    }
    for member in payload.get("members") or []:
        role = str(member.get("role") or "")
        if configured.get(role) != str(member.get("agentId") or "").strip():
            raise ValueError("release config agentId does not match manifest for member %s" % role)
    agent_ids = [str((payload.get("leader") or {}).get("agentId") or "").strip()]
    agent_ids.extend(str(member.get("agentId") or "").strip() for member in payload.get("members") or [])
    if len(agent_ids) != len(set(agent_ids)):
        raise ValueError("release requires distinct Agent IDs for the leader and every member")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true", help="also require real Agent/squad IDs")
    args = parser.parse_args(argv)

    if not MANIFEST.is_file():
        return fail("deepworks.manifest.json missing")
    try:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return fail("manifest is not readable JSON: %s" % error)
    if payload.get("mode") not in {"single", "team"}:
        return fail("manifest mode must be single or team")
    entry = ROOT / str(payload.get("entry") or "")
    if not entry.is_file():
        return fail("entry missing: %s" % entry)
    for key in ("contracts", "scripts", "tests", "docs"):
        if not (ROOT / key).is_dir():
            return fail("%s directory missing" % key)
    for doc in ("docs/deploy.md", "docs/checklist.md"):
        if not (ROOT / doc).is_file():
            return fail("documentation missing: %s" % doc)
    leader = ROOT / str((payload.get("leader") or {}).get("instructions") or "")
    if not leader.is_file():
        return fail("leader instructions missing: %s" % leader)
    for skill in payload.get("skills") or []:
        if not (ROOT / str(skill)).is_file():
            return fail("declared skill missing: %s" % skill)
    try:
        check_skill_files(entry)
        check_quality_contracts()
        if payload.get("mode") == "team":
            for contract in ("contracts/dispatch.schema.json", "contracts/team-plan.json"):
                if not (ROOT / contract).is_file():
                    return fail("team contract missing: %s" % contract)
            check_team_config(payload)
            for member in payload.get("members") or []:
                for key in ("instructions", "skill"):
                    path = ROOT / str(member.get(key) or "")
                    if not path.is_file():
                        return fail("member %s missing: %s" % (key, path))
                check_skill_files(ROOT / str(member.get("skill") or ""))
        if args.release:
            check_release_ids(payload)
    except ValueError as error:
        return fail(str(error))

    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env["DRY_RUN"] = "1"
        env["INDUSTRIAL_AGENT_OFFLINE"] = "1"
        try:
            check_turn(entry, tmp, env)
            if payload.get("mode") == "team":
                for member in payload.get("members") or []:
                    check_member(member, tmp, env)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return fail(str(error))

    check = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_marker_contract.py"), str(ROOT)],
        text=True, capture_output=True, check=False,
    )
    if check.returncode != 0:
        return fail("marker contract: %s" % (check.stdout + check.stderr))
    print("doctor ok: %s (%s)%s" % (
        payload.get("title"),
        payload.get("mode"),
        " release" if args.release else "",
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
