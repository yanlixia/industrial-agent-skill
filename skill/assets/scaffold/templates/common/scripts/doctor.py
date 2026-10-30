#!/usr/bin/env python3
"""Local checks, including a simulated skill-only deployment layout."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deepworks.manifest.json"
UUID_LITERAL = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
UUID_SCAN_DIRS = ("skills", "experts")


def fail(message):
    print("doctor failed: %s" % message)
    return 1


def warn(message):
    print("doctor warning: %s" % message)


def _is_redacted(value):
    """Control planes mask secrets as ***; that is 'present', not 'mismatched'."""
    text = str(value or "").strip()
    return bool(text) and set(text) <= {"*"} and len(text) >= 3


def _item_index(payload, keys):
    """Accept both ``[...]`` and ``{"items": [...]}`` control-plane shapes."""
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                payload = value
                break
        else:
            payload = [payload] if payload.get("id") else []
    return {
        str(item.get("id") or ""): item
        for item in payload or []
        if isinstance(item, dict)
    }


def _agent_index(payload):
    """Flatten /api/agents or an agent-detail export into {id: agent}."""
    return _item_index(payload, ("items", "agents", "details"))


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


def _names(items):
    result = set()
    if isinstance(items, dict):
        items = [items]
    for item in items or []:
        if isinstance(item, str):
            result.add(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("skill_name") or item.get("slug")
            if name:
                result.add(str(name))
    return result


def _squad_ids(squad_payload):
    if not isinstance(squad_payload, dict):
        return "", set()
    leader = str(squad_payload.get("leader_agent_id") or squad_payload.get("leader_id") or "")
    members = {
        str(item.get("member_id") or item.get("agent_id") or item.get("id") or "")
        for item in squad_payload.get("members") or []
        if isinstance(item, dict) and str(item.get("member_type") or "agent") == "agent"
    }
    members.discard("")
    return leader, members


def check_hardcoded_agent_ids():
    """Dispatch must resolve member IDs from config/env, never from a literal UUID."""
    for directory in UUID_SCAN_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                match = UUID_LITERAL.search(line)
                if match is None:
                    continue
                raise ValueError(
                    "hardcoded UUID %s in %s:%s; dispatch must read config/team.json or DEEPWORKS_AGENT_ID_<ROLE>"
                    % (match.group(0), path.relative_to(ROOT), number)
                )


def check_sandbox_profile(role, runtime):
    """Cross-check sandbox profile fields that silently break execution."""
    config = runtime.get("config") if isinstance(runtime.get("config"), dict) else {}
    if not config:
        return
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    lifecycle = config.get("lifecycle") if isinstance(config.get("lifecycle"), dict) else {}
    volume = config.get("volume") if isinstance(config.get("volume"), dict) else {}
    network_mode = str(network.get("mode") or "restricted")
    if network_mode == "disabled":
        warn("%s: sandbox network.mode=disabled; outbound API calls will fail like business timeouts" % role)
    allowed = network.get("allowed_domains") or []
    if network_mode == "restricted" and not allowed:
        warn("%s: sandbox network.mode=restricted with empty allowed_domains; confirm no outbound call is needed" % role)
    ttl = lifecycle.get("idle_ttl_seconds")
    if ttl is not None:
        try:
            ttl_value = int(ttl)
        except (TypeError, ValueError):
            raise ValueError("%s: lifecycle.idle_ttl_seconds must be an integer" % role)
        if ttl_value < 60 or ttl_value > 86400:
            raise ValueError("%s: lifecycle.idle_ttl_seconds=%s out of platform range 60..86400" % (role, ttl_value))
    lifecycle_mode = str(lifecycle.get("mode") or "per_task")
    volume_mode = str(volume.get("mode") or "task_volume")
    if lifecycle_mode == "session" and volume_mode == "task_volume":
        warn("%s: lifecycle.mode=session with volume.mode=task_volume; sandbox is reused but the workspace is not" % role)


def check_runtime_binding(payload, agents_payload, runtimes_payload, squad_payload=None, details_payload=None):
    """Machine gate for native member Tasks, based on captured control-plane JSON.

    ``GET /api/agents`` omits agents that only declare ``summon_modes=["squad"]``;
    pass the per-agent detail exports via ``details_payload`` so squad-only
    members are checked instead of being reported as missing.
    """
    agents = _agent_index(agents_payload)
    agents.update(_agent_index(details_payload))
    runtimes = _item_index(runtimes_payload, ("items", "runtimes"))
    rows = []
    leader = payload.get("leader") or {}
    if leader.get("agentId"):
        rows.append(("leader", str(leader["agentId"]), Path(str(leader.get("skill") or "")).parent.name))
    for member in payload.get("members") or []:
        rows.append((
            str(member.get("role") or ""),
            str(member.get("agentId") or ""),
            Path(str(member.get("skill") or "")).parent.name,
        ))
    if squad_payload is not None:
        squad_leader, squad_members = _squad_ids(squad_payload)
        leader_id = str(leader.get("agentId") or "")
        if squad_leader and squad_leader != leader_id:
            raise ValueError("squad leader %s does not match leader.agentId %s" % (squad_leader, leader_id))
        for role, agent_id, _skill in rows:
            if agent_id not in squad_members:
                raise ValueError("%s: agent %s is not a member of the current squad" % (role, agent_id))

    leader_agent = agents.get(str(leader.get("agentId") or ""), {})
    leader_env = leader_agent.get("custom_env") if isinstance(leader_agent.get("custom_env"), dict) else {}
    for role, agent_id, skill in rows:
        agent = agents.get(agent_id)
        if agent is None:
            raise ValueError(
                "%s: agent %s is not visible in /api/agents; "
                "fetch /api/agents/{id} and pass --agent-details-json "
                "(squad-only agents are omitted from the list endpoint)" % (role, agent_id or "<empty>")
            )
        runtime_id = str(agent.get("runtime_id") or "")
        runtime = runtimes.get(runtime_id)
        if not runtime_id:
            raise ValueError("%s: agent %s has no runtime_id" % (role, agent_id))
        if runtime is None:
            raise ValueError("%s: runtime %s is not visible in /api/runtimes" % (role, runtime_id))
        if str(runtime.get("status") or "") != "online":
            raise ValueError("%s: runtime %s is not online" % (role, runtime_id))
        runtime_mode = str(runtime.get("runtime_mode") or agent.get("runtime_mode") or "")
        if runtime_mode != "managed_sandbox":
            raise ValueError("%s: runtime mode is %r, expected managed_sandbox" % (role, runtime_mode))
        check_sandbox_profile(role, runtime)
        if skill not in _names(agent.get("skills")):
            raise ValueError("%s: skill %s is not bound to agent %s" % (role, skill, agent_id))
        env_name = "DEEPWORKS_AGENT_ID_%s" % role.upper().replace("-", "_")
        configured = str(leader_env.get(env_name) or "").strip()
        if _is_redacted(configured):
            warn("%s: %s is masked by the control plane; verify dispatch through a new session" % (role, env_name))
        elif configured and configured != agent_id:
            raise ValueError("%s: %s=%s does not match current squad agentId %s" % (role, env_name, configured, agent_id))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true", help="also require real Agent/squad IDs")
    parser.add_argument("--agents-json", help="captured /api/agents response for runtime binding gate")
    parser.add_argument("--runtimes-json", help="captured /api/runtimes response for runtime binding gate")
    parser.add_argument("--squad-json", help="captured /api/squads/{id} response with members")
    parser.add_argument(
        "--agent-details-json",
        help="captured /api/agents/{id} detail(s); required for agents omitted from the list endpoint",
    )
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
        check_hardcoded_agent_ids()
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
        if bool(args.agents_json) != bool(args.runtimes_json):
            raise ValueError("--agents-json and --runtimes-json must be provided together")
        if args.agents_json and args.runtimes_json:
            if not str((payload.get("leader") or {}).get("agentId") or "").strip():
                raise ValueError("runtime gate requires leader.agentId")
            check_runtime_binding(
                payload,
                json.loads(Path(args.agents_json).read_text(encoding="utf-8")),
                json.loads(Path(args.runtimes_json).read_text(encoding="utf-8")),
                json.loads(Path(args.squad_json).read_text(encoding="utf-8")) if args.squad_json else None,
                json.loads(Path(args.agent_details_json).read_text(encoding="utf-8")) if args.agent_details_json else None,
            )
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
