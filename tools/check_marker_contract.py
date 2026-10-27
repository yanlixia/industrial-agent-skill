#!/usr/bin/env python3
"""Emit/exempt consistency gate (CI check, zero external deps).

Lessons baked in (device-ops 2026-10-27, DW-OBS-143):

The orchestrator emits invisible continuation markers that the renderer must
copy verbatim; the reply validator bans raw uuids. Those two rules contradict
unless every marker the codebase emits INTO USER-VISIBLE REPLIES is exempted
in validate_reply.py. Humans fix the instance they can see and miss the
class — this script makes the class visible.

Scope rule (important, do not widen blindly): only scripts that assemble the
user-visible reply are checked, because only their markers pass through the
validator. Markers written into issue descriptions (idempotency keys,
payloads for other machines) never meet the uuid ban and must NOT be forced
into the exemption list. Reply-layer files are detected by name:
turn.py, render_reply.py, and any *relay*/reply* module. Override with
--reply-files a.py,b.py when the naming differs.

Usage:
    python3 scripts/check_marker_contract.py [root] [--reply-files a.py,b.py]

Exits 0 when consistent, 1 with a report of mismatches.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Marker emission sites: `f"<!-- {namespace}:{payload} -->"` or the literal
# string form `<!-- namespace:...`. The first capture group is the namespace.
EMIT_PATTERN = re.compile(r"<!\x2D\x2D\s*([a-z_][a-z0-9_]*):", re.IGNORECASE)

# Files whose output becomes (part of) the user-visible reply.
DEFAULT_REPLY_FILES = ("turn.py", "render_reply.py")


def _is_reply_layer(path: Path, extra: set[str]) -> bool:
    name = path.name
    if name in DEFAULT_REPLY_FILES or name in extra:
        return True
    lowered = name.lower()
    return "relay" in lowered or "reply" in lowered


def validator_namespaces(scripts_dir: Path) -> set[str]:
    """Parse the exemption regex out of validate_reply.py."""
    validator = scripts_dir / "validate_reply.py"
    if not validator.is_file():
        return set()
    text = validator.read_text(encoding="utf-8")
    # The pattern body contains "> " inside the string, so [^)] truncates at
    # the first alternation member; grab the whole raw-string literal instead.
    match = re.search(
        r"CONTINUATION_MARKER_PATTERN\s*=\s*re\.compile\(\s*r?(\".*?\"|'.*?')", text, re.S
    )
    if not match:
        return set()
    body = match.group(1)
    # Names inside (?:a|b|c) groups joined by |.
    namespaces: set[str] = set()
    for group in re.findall(r"\(\?:([^)]*)\)", body):
        namespaces.update(part.strip() for part in group.split("|") if part.strip())
    return namespaces


def emitted_namespaces(scripts_dir: Path, reply_files: set[str]) -> dict[str, str]:
    """Scan reply-layer scripts for emitted marker namespaces."""
    found: dict[str, str] = {}
    for path in sorted(scripts_dir.rglob("*.py")):
        if not _is_reply_layer(path, reply_files):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in EMIT_PATTERN.finditer(text):
            namespace = match.group(1).lower()
            found.setdefault(namespace, str(path))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=".", help="repo/skill root to scan")
    parser.add_argument(
        "--reply-files",
        default="",
        help="comma-separated extra reply-layer file names (beyond turn.py/render_reply.py/*relay*/*reply*)",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    reply_files = {part.strip() for part in args.reply_files.split(",") if part.strip()}

    validators = sorted(root.rglob("validate_reply.py"))
    if not validators:
        print("marker contract: no validate_reply.py found under root; nothing to check")
        return 0

    emitted: dict[str, str] = {}
    exempted: set[str] = set()
    for validator in validators:
        exempted |= validator_namespaces(validator.parent)
        emitted.update(emitted_namespaces(validator.parent.parent, reply_files))

    missing = {ns: path for ns, path in emitted.items() if ns not in exempted}
    if missing:
        print("marker contract violations: reply-layer marker emitted but not exempted in validate_reply.py")
        for namespace, path in sorted(missing.items()):
            print(f"  - {namespace}  (emitted in {path})")
        print("Fix: add the namespace to CONTINUATION_MARKER_PATTERN in validate_reply.py")
        print("     (or, if the marker truly never reaches user replies, move it out of the reply layer).")
        return 1
    print(f"marker contract ok: reply-layer markers {sorted(emitted)} all exempted")
    return 0


if __name__ == "__main__":
    sys.exit(main())

