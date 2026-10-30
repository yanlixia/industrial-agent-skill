#!/usr/bin/env python3
"""Shared DeepWorks runtime helpers for generated experts and teams."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT_MARKERS = (
    "squad_root_issue_id",
    "team_root_issue_id",
    "expert_root_issue_id",
)
DONE_STATUSES = {"done", "completed", "in_review"}
FAILED_STATUSES = {"failed", "cancelled"}
ACTIVE_STATUSES = {"todo", "in_progress", "running", "queued", "blocked", "waiting"}
DEFAULT_MEMBER_STALL_SECONDS = 180.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse a platform ISO8601 timestamp; return None instead of guessing."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def idle_seconds(*values: Any, now: Optional[datetime] = None) -> Optional[float]:
    """Seconds since the newest of the supplied timestamps."""
    moments = [moment for moment in (parse_timestamp(value) for value in values) if moment is not None]
    if not moments:
        return None
    reference = now or utc_now()
    return round((reference - max(moments)).total_seconds(), 3)


def member_stall_seconds() -> float:
    raw = os.environ.get("TEAM_MEMBER_STALL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_MEMBER_STALL_SECONDS
    try:
        return max(float(raw), 1.0)
    except ValueError:
        return DEFAULT_MEMBER_STALL_SECONDS
USER_INPUT_KEYS = (
    "input",
    "user_input",
    "user_request",
    "current_user_request",
    "message",
    "text",
)
REQUEST_MARKERS = (
    "Current user request:",
    "当前用户请求：",
    "User request:",
    "用户请求：",
)
REQUEST_STOPS = (
    "\n\nDeepWorks chat squad run.",
    "\n\nPrior visible messages",
    "\n\nPrior squad run outputs",
    "\n\nOriginal issue task:",
    "\n\n[SQUAD MEMBER RESULT]",
    "\n\nTrigger comment content:",
)


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_contexts(paths: Iterable[str]) -> List[Dict[str, Any]]:
    contexts: List[Dict[str, Any]] = []
    for raw in paths:
        value = load_json(Path(raw), default=None)
        if isinstance(value, dict):
            contexts.append(value)
    return contexts


def iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)


def current_request(contexts: Iterable[Dict[str, Any]]) -> str:
    contexts = list(contexts)
    for context in contexts:
        if str(context.get("trigger_author_type") or "").lower() == "agent":
            continue
        trigger = context.get("trigger_comment_content")
        if isinstance(trigger, str) and trigger.strip() and "[SQUAD MEMBER RESULT]" not in trigger:
            value = _clean_request(trigger)
            if value:
                return value
    for marker in REQUEST_MARKERS:
        for context in contexts:
            for text in iter_strings(context):
                if marker in text:
                    value = _clean_request(text)
                    if value:
                        return value
    for context in contexts:
        for key in USER_INPUT_KEYS:
            value = context.get(key)
            if isinstance(value, str) and value.strip():
                cleaned = _clean_request(value)
                if cleaned:
                    return cleaned
    return ""


def _clean_request(text: str) -> str:
    text = text.strip()
    for marker in REQUEST_MARKERS:
        if marker in text:
            text = text.rsplit(marker, 1)[1].strip()
            break
    if "Original issue task:" in text:
        text = text.rsplit("Original issue task:", 1)[1].strip()
    for stop in REQUEST_STOPS:
        if stop in text:
            text = text.split(stop, 1)[0]
    text = re.sub(r"^ISS-\d+:\s*", "", text, count=1)
    if "<!-- team_role:" in text or "<!-- role_dispatch_key:" in text:
        return text.splitlines()[0].strip() if text else ""
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def task_identity(contexts: Iterable[Dict[str, Any]]) -> str:
    for context in contexts:
        for key in ("id", "task_id", "execution_id", "attempt"):
            value = context.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def child_payload(contexts: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Read the input JSON that the leader wrote into a child issue description."""
    contexts = list(contexts)
    for context in contexts:
        for key in ("child_payload", "member_payload", "payload"):
            value = context.get(key)
            if isinstance(value, dict):
                return value
    for context in contexts:
        for text in iter_strings(context):
            if "role_dispatch_key" not in text and "team_role" not in text:
                continue
            value = _json_object_from_text(text)
            if isinstance(value, dict):
                return value
    return {}


def root_issue_id(contexts: Iterable[Dict[str, Any]]) -> str:
    pattern = re.compile(r"<!--\s*(?:%s):([0-9a-f-]{36})" % "|".join(ROOT_MARKERS), re.I)
    for context in contexts:
        for text in iter_strings(context):
            match = pattern.search(text)
            if match:
                return match.group(1)
    return ""


def source_issue_id(contexts: Iterable[Dict[str, Any]]) -> str:
    """Return the current chat/issue source when no hidden root marker exists."""
    for context in contexts:
        if str(context.get("source_type") or "") != "issue":
            continue
        value = str(context.get("source_ref") or context.get("issue_id") or "").strip()
        if value:
            return value
    return ""


def marker_value(text: str, namespace: str) -> str:
    match = re.search(r"<!--\s*%s:([^>]*?)\s*-->" % re.escape(namespace), text or "", re.I)
    return match.group(1).strip() if match else ""


def marker(root_id: str) -> str:
    return "<!-- squad_root_issue_id:%s -->" % root_id


def team_root_marker(root_id: str) -> str:
    return "<!-- team_root_issue_id:%s -->" % root_id


def role_marker(role: str) -> str:
    return "<!-- team_role:%s -->" % role


def idempotency_key(*parts: Any) -> str:
    stable: List[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (dict, list)):
            stable.append(json.dumps(part, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        else:
            stable.append(str(part))
    material = "|".join(stable)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def resolve_skill_path(skill_name: str, context_file: str = "context/skills.json") -> str:
    payload = load_json(Path(context_file), default={})

    def find(value: Any) -> str:
        if isinstance(value, dict):
            if value.get("name") == skill_name:
                return str(value.get("path") or "")
            for item in value.values():
                result = find(item)
                if result:
                    return result
        elif isinstance(value, list):
            for item in value:
                result = find(item)
                if result:
                    return result
        return ""

    return find(payload)


def resolve_deepexi_bin(configured: str = "") -> str:
    candidates = [
        configured,
        os.environ.get("DEEPEXI_BIN", ""),
        "/workspace/bin/deepexi",
        "/app/bin/deepexi",
        "/opt/deepexi/bin/deepexi",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    located = shutil.which("deepexi")
    if located:
        return located
    raise FileNotFoundError("deepexi CLI is unavailable in the worker runtime")


def run_json(args: List[str], timeout: int = 30, input_text: Optional[str] = None) -> Dict[str, Any]:
    completed = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input_text,
        stdin=None if input_text is not None else subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        if not detail:
            try:
                payload = json.loads(completed.stdout)
                detail = str(payload.get("detail") or payload.get("message") or payload)
            except (json.JSONDecodeError, AttributeError):
                detail = (completed.stdout or "").strip()
        raise RuntimeError(detail[-2000:] or "command failed: %s" % " ".join(args[:3]))
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("command did not return JSON: %s" % error)
    if not isinstance(payload, dict):
        raise RuntimeError("command returned non-object JSON")
    return payload


def payload_items(payload: Any, *keys: str) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys + ("items", "issues", "children", "comments", "artifacts"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _child_role(issue: Dict[str, Any]) -> str:
    description = str(issue.get("description") or "")
    role = marker_value(description, "team_role")
    if role:
        return role
    title = str(issue.get("title") or "")
    match = re.match(r"^\[([^\]]+)\]", title)
    return match.group(1).strip() if match else ""


def _json_object_from_text(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    return None


def extract_member_result(content: str, role: str = "") -> Optional[Dict[str, Any]]:
    payload = _json_object_from_text(content)
    if not payload:
        return None
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    if not isinstance(result, dict):
        return None
    normalized = dict(result)
    normalized.setdefault("role", payload.get("role") or role)
    normalized.setdefault("summary", normalized.get("summary") or str(normalized.get("text") or ""))
    return normalized


def _member_result(payload: Optional[Dict[str, Any]], role: str) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("ok") is False:
        return None
    result_role = str(payload.get("role") or role).strip()
    if role and result_role != role:
        return None
    summary = str(payload.get("summary") or "").strip()
    nested = payload.get("result")
    if isinstance(nested, dict):
        summary = summary or str(nested.get("summary") or nested.get("text") or "").strip()
    if not summary:
        return None
    normalized = dict(payload)
    normalized["role"] = result_role or role
    normalized["summary"] = summary
    return normalized


def collect_team_children(root_id: str, deepexi_bin: str = "") -> List[Dict[str, Any]]:
    """Read direct child issues and structured member results from DeepWorks."""
    if not root_id:
        return []
    binary = resolve_deepexi_bin(deepexi_bin)
    listing = run_json(
        [binary, "issue", "list", "--parent", root_id, "--limit", "200", "--output", "json"]
    )
    children: List[Dict[str, Any]] = []
    for issue in payload_items(listing, "issues", "children"):
        issue_id = str(issue.get("id") or "").strip()
        if not issue_id:
            continue
        role = _child_role(issue)
        if not role:
            try:
                issue = run_json([binary, "issue", "get", issue_id, "--output", "json"])
            except RuntimeError:
                pass
            role = _child_role(issue)
        item: Dict[str, Any] = {
            "id": issue_id,
            "role": role,
            "status": str(issue.get("status") or "").lower(),
            "title": str(issue.get("title") or ""),
            "idempotency_key": marker_value(str(issue.get("description") or ""), "role_dispatch_key"),
            "created_at": issue.get("created_at"),
            "updated_at": issue.get("updated_at"),
        }
        input_payload = _json_object_from_text(str(issue.get("description") or ""))
        if isinstance(input_payload, dict):
            item["input"] = input_payload
        if role and item["status"] not in FAILED_STATUSES:
            try:
                comments = run_json(
                    [binary, "issue", "comment", "list", issue_id, "--output", "json"]
                )
            except RuntimeError:
                comments = {}
            parsed = _member_result(
                extract_member_result(_artifact_result_text(binary, issue_id), role),
                role,
            )
            latest_comment_at = ""
            for comment in payload_items(comments, "comments"):
                created_at = str(comment.get("created_at") or "")
                if created_at and created_at > latest_comment_at:
                    latest_comment_at = created_at
            if latest_comment_at:
                item["last_comment_at"] = latest_comment_at
            if parsed is None:
                for comment in reversed(payload_items(comments, "comments")):
                    if str(comment.get("author_type") or "") != "agent":
                        continue
                    candidate = extract_member_result(str(comment.get("content") or ""), role)
                    if isinstance(candidate, dict) and candidate.get("member_result_key"):
                        parsed = _member_result(candidate, role)
                        if parsed is not None:
                            break
            if parsed is not None:
                item["result"] = parsed
                item["summary"] = str(parsed.get("summary") or "")
        children.append(item)
    return children


def find_child_by_key(root_id: str, key: str, deepexi_bin: str = "") -> Optional[Dict[str, Any]]:
    """Serial duplicate guard. The platform must still enforce a unique key for races."""
    if not root_id or not key:
        return None
    binary = resolve_deepexi_bin(deepexi_bin)
    listing = run_json(
        [binary, "issue", "list", "--parent", root_id, "--limit", "200", "--output", "json"]
    )
    for issue in payload_items(listing, "issues", "children"):
        issue_id = str(issue.get("id") or "").strip()
        if not issue_id:
            continue
        description = str(issue.get("description") or "")
        current_key = marker_value(description, "role_dispatch_key")
        if not current_key:
            try:
                detail = run_json([binary, "issue", "get", issue_id, "--output", "json"])
            except RuntimeError:
                continue
            current_key = marker_value(str(detail.get("description") or ""), "role_dispatch_key")
        if current_key == key:
            return issue
    return None


def _artifact_result_text(binary: str, issue_id: str) -> str:
    try:
        listing = run_json([binary, "artifact", "list", issue_id, "--output", "json"])
    except RuntimeError:
        return ""
    candidates = payload_items(listing, "artifacts", "items")
    if any(item.get("created_at") or item.get("updated_at") for item in candidates):
        candidates = sorted(
            candidates,
            key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""),
            reverse=True,
        )
    else:
        candidates = list(reversed(candidates))
    for artifact in candidates:
        name = str(artifact.get("name") or artifact.get("filename") or "")
        artifact_id = str(artifact.get("id") or "")
        if not artifact_id or name not in {"member-result.json", "result.json", "final-message.txt"}:
            continue
        target = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="member-result-", suffix=".txt", delete=False) as handle:
                target = handle.name
            run_json(
                [binary, "artifact", "download", artifact_id, "--output-file", target]
            )
            return Path(target).read_text(encoding="utf-8")
        except (OSError, UnicodeError, RuntimeError):
            continue
        finally:
            if target:
                try:
                    Path(target).unlink()
                except OSError:
                    pass
    return ""


def set_issue_status(issue_id: str, status: str, deepexi_bin: str = "") -> Dict[str, Any]:
    if not issue_id or not status:
        raise ValueError("issue_id and status are required")
    if os.environ.get("DRY_RUN") == "1":
        return {"ok": True, "dry_run": True, "issue_id": issue_id, "status": status}
    return run_json(
        [resolve_deepexi_bin(deepexi_bin), "issue", "status", issue_id, status, "--output", "json"]
    )


def post_issue_comment(
    issue_id: str,
    content: str,
    deepexi_bin: str = "",
) -> Dict[str, Any]:
    if not issue_id or not content.strip():
        raise ValueError("issue_id and content are required")
    if os.environ.get("DRY_RUN") == "1":
        return {"ok": True, "dry_run": True, "issue_id": issue_id}
    return run_json(
        [
            resolve_deepexi_bin(deepexi_bin),
            "issue",
            "comment",
            "add",
            issue_id,
            "--content-stdin",
            "--output",
            "json",
        ],
        input_text=content,
    )


def post_issue_comment_once(
    issue_id: str,
    content: str,
    result_key: str,
    deepexi_bin: str = "",
) -> Dict[str, Any]:
    """Best-effort duplicate guard for the member's machine-readable result."""
    if not issue_id or not content.strip() or not result_key:
        raise ValueError("issue_id, content and result_key are required")
    if os.environ.get("DRY_RUN") == "1":
        return {"ok": True, "dry_run": True, "issue_id": issue_id, "result_key": result_key}
    binary = resolve_deepexi_bin(deepexi_bin)
    try:
        listing = run_json([binary, "issue", "comment", "list", issue_id, "--output", "json"])
    except RuntimeError:
        return post_issue_comment(issue_id, content, deepexi_bin)
    for comment in reversed(payload_items(listing, "comments")):
        existing = _json_object_from_text(str(comment.get("content") or ""))
        if isinstance(existing, dict) and str(existing.get("member_result_key") or "") == result_key:
            return {"ok": True, "reused": True, "issue_id": issue_id, "result_key": result_key}
    result = post_issue_comment(issue_id, content, deepexi_bin)
    result.setdefault("result_key", result_key)
    return result


def record_squad_activity(
    issue_id: str,
    outcome: str,
    reason: str,
    deepexi_bin: str = "",
) -> Dict[str, Any]:
    if outcome not in {"action", "no_action", "failed"}:
        raise ValueError("squad activity outcome must be action/no_action/failed")
    if not issue_id:
        return {"ok": True, "skipped": "missing-root"}
    if os.environ.get("DRY_RUN") == "1":
        return {"ok": True, "dry_run": True, "outcome": outcome}
    return run_json(
        [
            resolve_deepexi_bin(deepexi_bin),
            "squad",
            "activity",
            issue_id,
            outcome,
            "--reason",
            reason[:500],
            "--output",
            "json",
        ]
    )


def dispatch_child(
    parent_issue_id: str,
    role: str,
    title: str,
    input_payload: Dict[str, Any],
    assignee_id: str,
    acceptance: str = "",
    deepexi_bin: str = "",
) -> Dict[str, Any]:
    if not parent_issue_id or not assignee_id:
        raise ValueError("parent_issue_id and assignee_id are required")
    key = idempotency_key(parent_issue_id, role, input_payload)
    description = (
        "角色：%s\n\n%s\n\n"
        "<!-- team_root_issue_id:%s -->\n"
        "<!-- team_role:%s -->\n"
        "<!-- role_dispatch_key:%s -->\n\n"
        "验收标准：%s"
    ) % (
        role,
        json.dumps(input_payload, ensure_ascii=False, sort_keys=True),
        parent_issue_id,
        role,
        key,
        acceptance or "返回结构化结果和必要 artifact",
    )
    if os.environ.get("DRY_RUN") == "1":
        return {"ok": True, "dry_run": True, "id": "dry-run-" + key, "role": role, "key": key}
    existing = find_child_by_key(parent_issue_id, key, deepexi_bin)
    if existing is not None:
        return {
            "ok": True,
            "reused": True,
            "id": str(existing.get("id") or ""),
            "role": role,
            "key": key,
        }
    command = [
        resolve_deepexi_bin(deepexi_bin),
        "issue",
        "create",
        "--title",
        title,
        "--description",
        description,
        "--parent",
        parent_issue_id,
        "--assignee-type",
        "agent",
        "--assignee-id",
        assignee_id,
        "--output",
        "json",
    ]
    payload = run_json(command)
    if isinstance(payload.get("issue"), dict):
        nested = payload["issue"]
        payload.setdefault("id", nested.get("id"))
    payload.setdefault("key", key)
    payload.setdefault("role", role)
    if not payload.get("id"):
        raise RuntimeError("issue create returned no child issue id: %s" % str(payload)[:500])
    return payload


def turn_payload(
    reply_intent: Optional[Dict[str, Any]],
    started: float,
    *,
    continue_loop: bool = False,
    ok: bool = True,
    error: Optional[Dict[str, Any]] = None,
    observable: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ok": ok,
        "continue_loop": continue_loop,
        "reply_required": bool(reply_intent) and not continue_loop,
        "timings": {"total_ms": int((time.monotonic() - started) * 1000)},
    }
    if reply_intent:
        payload["reply_intent"] = reply_intent
    if error:
        payload["error"] = error
    # Dispatch identity must survive every continuation result. Losing it is what
    # turned a healthy "wait for member" into a bogus "dispatch failed" report.
    for key, value in (observable or {}).items():
        if value not in (None, "", [], {}):
            payload[key] = value
    return payload


def reply_intent(
    kind: str,
    root_id: str,
    text: str,
    facts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "frozen": {"marker": marker(root_id) if root_id else ""},
        "facts": facts or {},
        "tone_context": {"draft": text},
    }


def write_json(path: str, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
