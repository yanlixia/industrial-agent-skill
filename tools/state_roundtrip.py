#!/usr/bin/env python3
"""Cross-turn state roundtrip contract harness (no platform required).

The single most expensive class of cross-turn bug in experts and team leaders
(v3 2026-10-27, DW-OBS-143/146): every component test green, the chain still
100% broken, because no test walks the FULL pipeline —

    query turn emits state marker -> LLM drafts reply -> validate_reply
    accepts -> NEXT turn recovers the same state from the draft.

This harness makes that walk a reusable, domain-agnostic test. Wire it into
your test suite with three callables (see usage()):

    emit(ctx)        -> (turn_result, reply_intent)   # your turn.py query path
    render(intent)   -> draft: str                     # your draft step (mock LLM ok)
    recover(draft, issues) -> state                    # your state.rebuild

It then asserts, in order:

  R1 happy path   : draft passes validate_reply AND recovers the same root id
  R2 marker damage: payload corrupted/deleted -> recovery falls back to the
                    issue-tree path (authoritative source), NEVER escalates
  R3 uuid ban     : any uuid OUTSIDE the continuation markers still fails
                    validation (exemptions did not open a leak)

Real-world numbers baked into the defaults: LLM transcription of a 672-char
base64+zlib marker introduced 2 single-character errors (DW-OBS-146). R2
simulates exactly that (two substitutions + one deletion) on top of total
marker loss.

Standard library only. Exit code 0 = all contracts hold.
"""

from __future__ import annotations

import re
import sys
import uuid as uuid_lib
from dataclasses import dataclass
from typing import Any, Callable

# Tolerated import: validate_reply sits either beside this file (tools/) or in
# the scaffold skill scripts; fall back gracefully with an actionable error.
try:
    from validate_reply import CONTINUATION_MARKER_PATTERN, validate
except ImportError:  # pragma: no cover - environment wiring error
    print("state_roundtrip: cannot import validate_reply.py — run from tools/ or add its dir to PYTHONPATH")
    raise


MARKER_RE = re.compile(r"<!--\s*([a-z_][a-z0-9_]*):([^>]*)-->", re.IGNORECASE)


@dataclass
class RoundtripResult:
    checks: list[tuple[str, bool, str]]

    @property
    def ok(self) -> bool:
        return all(passed for _, passed, _ in self.checks)

    def report(self) -> str:
        lines = [f"{'PASS' if passed else 'FAIL'}  {name}: {detail}" for name, passed, detail in self.checks]
        lines.append("roundtrip contract: " + ("OK" if self.ok else "VIOLATED"))
        return "\n".join(lines)


def _damage(payload: str) -> str:
    """Simulate observed LLM transcription damage (DW-OBS-146): two single-char
    substitutions at ~60% depth plus one deletion. Enough to break zlib/base64
    integrity without looking obviously wrong."""
    if len(payload) < 8:
        return payload + "!"
    chars = list(payload)
    i, j = int(len(chars) * 0.6), int(len(chars) * 0.9)
    chars[i] = "X" if chars[i] != "X" else "Y"
    if j < len(chars):
        chars[j] = "4" if chars[j] != "4" else "7"
    return "".join(chars[:-1])  # one deletion at the tail


def run_roundtrip(
    emit: Callable[[], tuple[dict, dict]],
    render: Callable[[dict], str],
    recover: Callable[[str, Any], Any],
    issues: Any = None,
    get_root: Callable[[Any], str] = lambda state: getattr(state, "root_issue_id", ""),
) -> RoundtripResult:
    """Wire your pipeline pieces and get the three contracts checked."""
    checks: list[tuple[str, bool, str]] = []
    turn_result, intent = emit()
    root_id = str(turn_result.get("root_issue_id") or "")
    draft = render(intent)

    # R1 happy path: validation passes and state round-trips through the draft.
    verdict = validate(intent, draft)
    state = recover(draft, issues)
    recovered_root = str(get_root(state) or "")
    checks.append((
        "R1 happy-path roundtrip",
        verdict["ok"] and recovered_root == root_id and bool(root_id),
        f"validate={'ok' if verdict['ok'] else verdict['violations']} recovered={recovered_root!r} emitted={root_id!r}",
    ))

    # R2 damage tolerance: corrupted marker payload must fall back to the
    # authoritative issue-tree recovery without escalating.
    damaged = draft
    for match in MARKER_RE.finditer(draft):
        namespace, payload = match.group(1), match.group(2)
        if payload.strip():
            damaged = damaged.replace(
                match.group(0), f"<!-- {namespace}:{_damage(payload)} -->", 1
            )
    try:
        state2 = recover(damaged, issues)
        recovered2 = str(get_root(state2) or "")
        ok2 = recovered2 == root_id
        detail2 = f"damaged-marker recovery={recovered2!r}"
    except Exception as error:  # noqa: BLE001 - escalation IS the failure mode
        ok2, detail2 = False, f"recovery escalated on damaged marker: {error}"
    checks.append(("R2 damaged-marker fallback", ok2, detail2))

    # R2b total marker loss: reply text stripped of all markers entirely.
    stripped = MARKER_RE.sub("", draft)
    try:
        state3 = recover(stripped, issues)
        recovered3 = str(get_root(state3) or "")
        ok3, detail3 = recovered3 == root_id, f"marker-less recovery={recovered3!r}"
    except Exception as error:  # noqa: BLE001
        ok3, detail3 = False, f"recovery escalated without markers: {error}"
    checks.append(("R2b marker-less fallback", ok3, detail3))

    # R3 uuid ban still bites outside markers: exemptions must not open a leak.
    leak_draft = draft + f"\ntask {uuid_lib.uuid4()}"
    verdict3 = validate(intent, leak_draft)
    checks.append((
        "R3 uuid ban outside markers",
        not verdict3["ok"] and any("uuid" in v for v in verdict3["violations"]),
        "raw uuid outside markers must fail validation",
    ))

    return RoundtripResult(checks=checks)


# --- self-demo (python3 tools/state_roundtrip.py) -------------------------
def _demo() -> int:
    """Minimal in-memory demo proving the harness itself enforces the rules."""
    root = "2f000000-1111-4222-8333-444444444444"

    def emit():
        marker = f"<!-- chain_state:{root}:eJwLi8vMy0vM -->\n<!-- squad_root_issue_id:{root} -->"
        intent = {
            "frozen": {"marker": f"<!-- squad_root_issue_id:{root} -->"},
            "facts": {},
        }
        return {"root_issue_id": root, "reply": "工单表\n" + marker}, intent

    def render(intent):
        return "工单表如下：\n\n" + intent["frozen"]["marker"]

    class FakeIssues:
        description = f"authoritative <!-- chain_state:{root}:eJw --> copy"

    def recover(draft, issues):
        m = re.search(r"squad_root_issue_id:([0-9a-f-]{36})", draft)
        if m:
            return type("S", (), {"root_issue_id": m.group(1)})()
        # fallback: authoritative issue description
        m2 = re.search(r"chain_state:([0-9a-f-]{36})", str(getattr(issues, "description", "")))
        return type("S", (), {"root_issue_id": m2.group(1) if m2 else ""})()

    result = run_roundtrip(emit, render, recover, issues=FakeIssues())
    print(result.report())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(_demo())
