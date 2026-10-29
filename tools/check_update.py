#!/usr/bin/env python3
"""Check and optionally update the bundled industrial-agent skills."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO = "https://github.com/yanlixia/industrial-agent-skill"
BRANCH = "main"
INSTALL_SCHEMA = "industrial-agent-skill/install/1"
METADATA_NAME = "installed.json"
SKILL_NAMES = ("industrial-agent-builder", "session-storyboard")


class UpdateError(RuntimeError):
    pass


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def metadata_path() -> Path:
    return Path(__file__).resolve().parent.parent / METADATA_NAME


def read_metadata(path: Path | None = None) -> dict[str, Any]:
    target = path or metadata_path()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateError(f"cannot read installation metadata {target}: {exc}") from exc
    if not isinstance(value, dict):
        raise UpdateError(f"installation metadata must be an object: {target}")
    if value.get("schema") != INSTALL_SCHEMA:
        raise UpdateError(f"unsupported installation metadata schema in {target}")
    if value.get("repository") != REPO:
        raise UpdateError(f"installation metadata points to another repository: {target}")
    commit = str(value.get("commit") or "")
    if commit and len(commit) != 40:
        raise UpdateError(f"installation metadata has an invalid commit in {target}")
    return value


def _run(command: list[str], *, timeout: int = 30, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(f"command timed out after {timeout}s: {command[0]}") from exc
    except OSError as exc:
        raise UpdateError(f"cannot run {command[0]}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise UpdateError(f"command failed ({completed.returncode}): {command[0]} {detail}")
    return completed.stdout.strip()


def remote_commit() -> str:
    output = _run(["git", "ls-remote", REPO, f"refs/heads/{BRANCH}"])
    first_line = output.splitlines()[0] if output else ""
    commit = first_line.split("\t", 1)[0].strip()
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise UpdateError(f"unexpected git ls-remote response: {first_line!r}")
    return commit


def check(local_metadata: dict[str, Any]) -> dict[str, Any]:
    remote = remote_commit()
    local = str(local_metadata.get("commit") or "")
    status = "current" if local == remote else "update_available"
    return {
        "schema": "industrial-agent-skill/update-check/1",
        "status": status,
        "local_commit": local or None,
        "remote_commit": remote,
        "repository": REPO,
        "branch": BRANCH,
        "apply_command": f"{sys.executable} {Path(__file__).resolve()} --apply --yes",
    }


def _confirm(assumed_yes: bool) -> bool:
    if assumed_yes:
        return True
    try:
        answer = input("Update both installed skills from GitHub main? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def apply_update(*, assumed_yes: bool) -> dict[str, Any]:
    remote = remote_commit()
    if not _confirm(assumed_yes):
        return {
            "schema": "industrial-agent-skill/update-result/1",
            "status": "declined",
            "remote_commit": remote,
        }

    with tempfile.TemporaryDirectory(prefix="industrial-agent-skill-update-") as tmp:
        repo_dir = Path(tmp) / "repo"
        _run(
            ["git", "clone", "--depth", "1", "--branch", BRANCH, REPO, str(repo_dir)],
            timeout=120,
        )
        cloned = _run(["git", "rev-parse", "HEAD"], cwd=repo_dir)
        if cloned != remote:
            raise UpdateError(f"repository moved during update: expected {remote}, cloned {cloned}")
        _run(
            ["bash", "install.sh"],
            cwd=repo_dir,
            timeout=180,
            env={**os.environ, "INDUSTRIAL_AGENT_SKILL_COMMIT": remote},
        )

    home = Path.home()
    installed_commits = []
    for skill in SKILL_NAMES:
        path = home / ".agents" / "skills" / skill / METADATA_NAME
        installed = read_metadata(path)
        installed_commits.append(str(installed.get("commit") or ""))
        if installed_commits[-1] != remote:
            raise UpdateError(f"{skill} was not updated to {remote}")

    return {
        "schema": "industrial-agent-skill/update-result/1",
        "status": "updated",
        "remote_commit": remote,
        "installed_commits": {
            skill: commit for skill, commit in zip(SKILL_NAMES, installed_commits)
        },
        "checked_at": _utc_now(),
    }


def self_check() -> dict[str, Any]:
    value = read_metadata()
    return {
        "schema": "industrial-agent-skill/update-check/1",
        "status": "metadata_valid",
        "metadata": value,
    }


def print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check for industrial-agent-skill bundle updates.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true", help="update after explicit confirmation")
    modes.add_argument("--self-check", action="store_true", help="validate local metadata only")
    parser.add_argument("--yes", action="store_true", help="assume consent; use only after user confirmation")
    args = parser.parse_args(argv)

    try:
        if args.self_check:
            result = self_check()
        elif args.apply:
            result = apply_update(assumed_yes=args.yes)
        else:
            result = check(read_metadata())
        print_json(result)
        return 0
    except UpdateError as exc:
        print_json({
            "schema": "industrial-agent-skill/update-check/1",
            "status": "error",
            "error": str(exc),
        })
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
