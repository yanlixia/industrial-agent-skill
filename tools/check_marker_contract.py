#!/usr/bin/env python3
"""Check that every reply-layer marker is exempted by validate_reply.py."""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set

EMIT_PATTERN = re.compile(r"<!\x2D\x2D\s*([a-z_][a-z0-9_]*):", re.IGNORECASE)

DEFAULT_REPLY_FILES = {
    "turn.py",
    "run.py",
    "render_reply.py",
    "runtime.py",
}
DEFAULT_REPLY_PATTERNS = ("*reply*.py", "*relay*.py")


def _load_config(root: Path) -> Dict[str, object]:
    path = root / "contracts" / "markers.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def validator_namespaces(validator: Path) -> Set[str]:
    if not validator.is_file():
        return set()
    text = validator.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {target.id for target in targets if isinstance(target, ast.Name)}
            if "CONTINUATION_MARKER_NAMESPACES" not in names:
                continue
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            if isinstance(value, (tuple, list, set)):
                return {str(item).lower() for item in value}
    match = re.search(
        r"CONTINUATION_MARKER_PATTERN\s*=\s*re\.compile\(\s*r?(\".*?\"|'.*?')",
        text,
        re.S,
    )
    if not match:
        return set()
    body = match.group(1)
    namespaces: Set[str] = set()
    for group in re.findall(r"\(\?:([^)]*)\)", body):
        namespaces.update(part.strip() for part in group.split("|") if part.strip())
    return namespaces


def _matches_any(path: Path, globs: List[str]) -> bool:
    rel = path.as_posix()
    name = path.name
    for pattern in globs:
        if name in DEFAULT_REPLY_FILES:
            return True
        if any(fnmatch.fnmatchcase(name, candidate) for candidate in DEFAULT_REPLY_PATTERNS):
            return True
        if fnmatch.fnmatchcase(rel, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatchcase(rel, pattern[3:]):
            return True
        if "/" not in pattern and fnmatch.fnmatchcase(name, pattern):
            return True
    return False


def emitted_namespaces(root: Path, globs: List[str]) -> Dict[str, str]:
    found: Dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part in {"__pycache__", ".venv", "node_modules", "tests"} for part in relative.parts):
            continue
        if not _matches_any(relative, globs):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in EMIT_PATTERN.finditer(text):
            found.setdefault(match.group(1).lower(), str(relative))
    return found


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=".", help="generated repo/skill root")
    parser.add_argument("--reply-files", default="", help="comma-separated extra globs or file names")
    args = parser.parse_args(argv)
    root = Path(args.root)
    config = _load_config(root)
    globs = ["**/" + name for name in sorted(DEFAULT_REPLY_FILES)]
    globs.extend("**/" + pattern for pattern in DEFAULT_REPLY_PATTERNS)
    configured = config.get("reply_globs")
    if isinstance(configured, list):
        globs.extend(str(item) for item in configured)
    globs.extend(part.strip() for part in args.reply_files.split(",") if part.strip())

    validator = root / "scripts" / "validate_reply.py"
    configured_validator = config.get("validator")
    if isinstance(configured_validator, str):
        validator = root / configured_validator
    if not validator.is_file():
        found = sorted(root.rglob("validate_reply.py"))
        if not found:
            print("marker contract: no validate_reply.py found under %s" % root)
            return 0
        validator = found[0]

    emitted = emitted_namespaces(root, globs)
    exempted = validator_namespaces(validator)
    missing = {name: path for name, path in emitted.items() if name not in exempted}
    if missing:
        print("marker contract violations: reply-layer marker emitted but not exempted")
        for namespace, path in sorted(missing.items()):
            print("  - %s  (%s)" % (namespace, path))
        print("Fix: add the namespace to CONTINUATION_MARKER_NAMESPACES in %s" % validator)
        return 1
    print("marker contract ok: %s" % sorted(emitted))
    return 0


if __name__ == "__main__":
    sys.exit(main())
