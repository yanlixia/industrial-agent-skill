from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import runtime


def _team_config() -> Dict[str, Any]:
    skill_root = Path(__file__).resolve().parents[1]
    path = skill_root / "config/team.json"
    if not path.is_file():
        return {}
    payload = runtime.load_json(path, default=None)
    if not isinstance(payload, dict):
        raise ValueError("invalid team runtime config: %s" % path)
    return payload


def _member(role: str) -> Dict[str, Any]:
    config = _team_config()
    for member in config.get("members") or []:
        if isinstance(member, dict) and member.get("role") == role:
            return member
    return {}


def _assignee(role: str) -> str:
    env_key = "DEEPWORKS_AGENT_ID_" + role.upper().replace("-", "_")
    configured = os.environ.get(env_key, "").strip()
    if configured:
        return configured
    return str(_member(role).get("agentId") or "").strip()


def dispatch(role: str, root: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not root:
        raise ValueError("root issue id is required before dispatching a member")
    assignee = _assignee(role)
    if not assignee and os.environ.get("DRY_RUN") != "1":
        raise ValueError(
            "missing agent id for role %s; fill config/team.json or set DEEPWORKS_AGENT_ID_%s"
            % (role, role.upper().replace("-", "_"))
        )
    return runtime.dispatch_child(
        parent_issue_id=root,
        role=role,
        title="[%s] %s 任务" % (role, role),
        input_payload=payload,
        assignee_id=assignee or "dry-run-agent",
        acceptance="任务完成后在 artifacts/member-result.json 写结构化结果；包含 ok、role、result.summary、facts 和必要 artifact",
    )
