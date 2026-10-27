#!/usr/bin/env python3
"""Generate a single-expert or expert-team DeepWorks repository skeleton."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, Iterable, List

TOOLS = Path(__file__).resolve().parent
SKILL_ROOT_CANDIDATES = [
    TOOLS.parent / "skill/assets/scaffold/templates",
    TOOLS.parent / "assets/scaffold/templates",
    TOOLS / "templates",
]
COPY_TOOLS = ["runtime.py", "validate_reply.py", "check_marker_contract.py", "state_roundtrip.py"]
SKILL_TOOLS = ["runtime.py", "validate_reply.py"]
SCAFFOLD_MARKER = ".industrial-agent-scaffold.json"


def find_templates() -> Path:
    for candidate in SKILL_ROOT_CANDIDATES:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("scaffold templates not found; reinstall the skill")


def slug(value: str, label: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
        raise ValueError("%s must match [a-z0-9][a-z0-9_-]*: %r" % (label, value))
    return value


def title_for(role: str) -> str:
    return role.replace("-", " ").replace("_", " ").title()


def copy_tree(src: Path, dst: Path, tokens: Dict[str, str]) -> None:
    for path in sorted(src.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(src)
        if any(part in {"__pycache__", ".DS_Store"} for part in relative.parts):
            continue
        target_relative = str(relative)
        for key, value in tokens.items():
            target_relative = target_relative.replace(key, value)
        target = dst / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() in {".py", ".md", ".json", ".txt", ".yml", ".yaml", ".html", ".css", ".js"}:
            text = path.read_text(encoding="utf-8")
            for key, value in tokens.items():
                text = text.replace(key, value)
            target.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(path, target)


def parse_assignments(raw: str, roles: List[str], label: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError("%s entries must use role=value: %r" % (label, item))
        key, value = (part.strip() for part in item.split("=", 1))
        key = slug(key, "%s role" % label)
        if key not in roles:
            raise ValueError("%s role is not declared in --roles: %s" % (label, key))
        if key in values:
            raise ValueError("%s role is declared more than once: %s" % (label, key))
        if not value:
            raise ValueError("%s value must not be empty for role %s" % (label, key))
        values[key] = value
    return values


def write_team_config(
    target: Path,
    domain: str,
    roles: List[str],
    agent_ids: Dict[str, str],
    squad_id: str,
) -> None:
    config = {
        "schema": "industrial-agent/team-runtime/1",
        "squadId": squad_id or None,
        "members": [
            {"role": role, "agentId": agent_ids.get(role) or None}
            for role in roles
        ],
    }
    path = target / "skills" / ("%s-orchestrate" % domain) / "config" / "team.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_manifest(
    target: Path,
    mode: str,
    domain: str,
    title: str,
    roles: List[str],
    *,
    leader_agent_id: str = "",
    agent_ids: Dict[str, str] | None = None,
    squad_id: str = "",
) -> None:
    agent_ids = agent_ids or {}
    if mode == "single":
        entry = "skills/%s/scripts/turn.py" % domain
        skills = ["skills/%s/SKILL.md" % domain]
        members = []
        squad = None
        runtime_mode = "single"
    else:
        entry = "skills/%s-orchestrate/scripts/turn.py" % domain
        skills = ["skills/%s-orchestrate/SKILL.md" % domain]
        members = []
        runtime_mode = "leader"
        for role in roles:
            skills.append("experts/%s/skills/%s/SKILL.md" % (role, role))
            members.append({
                "role": role,
                "runtime": "member",
                "instructions": "experts/%s/AGENTS.md" % role,
                "skill": "experts/%s/skills/%s/SKILL.md" % (role, role),
                "agentId": agent_ids.get(role) or None,
            })
        squad = {
            "id": squad_id or None,
            "name": title,
            "leaderRole": "leader",
            "memberRoles": roles,
        }
    manifest = {
        "schema": "industrial-agent/1",
        "mode": mode,
        "domain": domain,
        "title": title,
        "runtime": runtime_mode,
        "entry": entry,
        "leader": {
            "name": title,
            "instructions": "entry/agents.md",
            "skill": skills[0],
            "agentId": leader_agent_id or None,
        },
        "members": members,
        "skills": skills,
        "squad": squad,
    }
    (target / "deepworks.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if mode == "team":
        plan = {
            "roles": roles,
            "dispatch": "single-ready-child",
            "parallel": False,
            "members": [
                {"role": role, "runtime": "member", "agentId": agent_ids.get(role) or None}
                for role in roles
            ],
        }
        (target / "contracts" / "team-plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_team_config(target, domain, roles, agent_ids, squad_id)


def write_readme(target: Path, mode: str, domain: str, title: str, roles: List[str]) -> None:
    lines = [
        "# %s" % title,
        "",
        "由 industrial-agent-builder 生成。",
        "",
        "- mode: `%s`" % mode,
        "- domain: `%s`" % domain,
    ]
    if roles:
        lines.append("- roles: %s" % ", ".join("`%s`" % role for role in roles))
    lines.extend([
        "",
        "## 下一步",
        "",
        "1. 修改领域实现：`skills/`、`experts/` 下的 `domain.py` / provider，不要把控制流交给 LLM。",
        "2. 单专家在 `deepworks.manifest.json` 填写 Agent ID；团队填写 `skills/%s-orchestrate/config/team.json` 的成员 Agent ID 和 squad ID。" % domain,
        "3. 按业务补全 `contracts/turn-states.md`、`contracts/invariants.json` 和回复事实。",
        "4. 运行 `python3 scripts/doctor.py` 和 `python3 -m unittest discover -s tests -v`；发布前再跑 `doctor.py --release`。",
        "5. 按 `docs/deploy.md` 创建 Agent/squad，再在 DeepWorks test 环境新建会话，按 `docs/checklist.md` 验收。",
        "",
    ])
    (target / "README.md").write_text("\n".join(lines), encoding="utf-8")


def write_checklist(target: Path) -> None:
    source = TOOLS.parent / "skill/references/checklist.md"
    if not source.is_file():
        source = TOOLS.parent / "references/checklist.md"
    if source.is_file():
        destination = target / "docs/checklist.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def ensure_target(target: Path, force: bool) -> None:
    if target.exists() and any(target.iterdir()):
        if not force:
            raise FileExistsError("target is not empty: %s (use --force to replace)" % target)
        marker = target / SCAFFOLD_MARKER
        manifest = target / "deepworks.manifest.json"
        owned = marker.is_file()
        if not owned and manifest.is_file():
            try:
                owned = json.loads(manifest.read_text(encoding="utf-8")).get("schema") == "industrial-agent/1"
            except (OSError, ValueError):
                owned = False
        if not owned:
            raise ValueError(
                "refusing to replace a non-empty directory not generated by this tool: %s" % target
            )
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("single", "team"), required=True)
    parser.add_argument("--domain", required=True, help="lowercase skill/domain slug")
    parser.add_argument("--title", required=True, help="user-facing product name")
    parser.add_argument("--target", required=True, help="output directory")
    parser.add_argument("--roles", default="diagnosis,maintenance,monitor", help="team roles, comma-separated")
    parser.add_argument("--leader-agent-id", default="", help="leader/single Agent ID, when already known")
    parser.add_argument("--agent-ids", default="", help="team member IDs: role=id,role=id")
    parser.add_argument("--squad-id", default="", help="squad ID, when already known")
    parser.add_argument("--force", action="store_true", help="replace a non-empty target")
    args = parser.parse_args(argv)

    domain = slug(args.domain, "domain")
    if not args.title.strip() or "\n" in args.title or "\r" in args.title:
        raise ValueError("title must be a single non-empty line")
    roles = [slug(item, "role") for item in args.roles.split(",") if item.strip()]
    if args.mode == "team":
        if len(set(roles)) != len(roles):
            raise ValueError("team roles must be unique")
        if len(roles) < 2:
            raise ValueError("team mode requires at least two roles")
    else:
        roles = []
        if args.agent_ids or args.squad_id:
            raise ValueError("--agent-ids/--squad-id are only valid in team mode")
    agent_ids = parse_assignments(args.agent_ids, roles, "agent-ids") if args.mode == "team" else {}
    if args.mode == "team":
        known = [value for value in [args.leader_agent_id.strip(), *agent_ids.values()] if value]
        if len(set(known)) != len(known):
            raise ValueError("leader and member Agent IDs must be distinct")

    templates = find_templates()
    target = Path(args.target).expanduser().resolve()
    ensure_target(target, args.force)

    tokens = {
        "__DOMAIN__": domain,
        "__DOMAIN_TITLE__": args.title,
        "__ROLES_JSON__": json.dumps(roles, ensure_ascii=False),
    }
    copy_tree(templates / "common", target, tokens)
    if args.mode == "single":
        copy_tree(templates / "single", target, tokens)
    else:
        copy_tree(templates / "team" / "base", target, tokens)
        for role in roles:
            role_tokens = dict(tokens)
            role_tokens["__ROLE__"] = role
            role_tokens["__ROLE_TITLE__"] = title_for(role)
            copy_tree(templates / "team" / "role", target / "experts" / role, role_tokens)

    scripts = target / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in COPY_TOOLS:
        source = TOOLS / name
        if source.is_file():
            shutil.copy2(source, scripts / name)

    skill_dirs = [target / "skills" / domain]
    if args.mode == "team":
        skill_dirs = [target / "skills" / ("%s-orchestrate" % domain)]
        skill_dirs.extend(target / "experts" / role / "skills" / role for role in roles)
    for skill_dir in skill_dirs:
        skill_scripts = skill_dir / "scripts"
        skill_scripts.mkdir(parents=True, exist_ok=True)
        for name in SKILL_TOOLS:
            source = TOOLS / name
            if source.is_file():
                shutil.copy2(source, skill_scripts / name)

    (target / SCAFFOLD_MARKER).write_text(
        json.dumps({"generator": "industrial-agent-builder", "schema": 1}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_manifest(
        target,
        args.mode,
        domain,
        args.title,
        roles,
        leader_agent_id=args.leader_agent_id.strip(),
        agent_ids=agent_ids,
        squad_id=args.squad_id.strip(),
    )
    write_readme(target, args.mode, domain, args.title, roles)
    write_checklist(target)
    print("generated %s: %s" % (args.mode, target))
    print("next: python3 scripts/doctor.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
