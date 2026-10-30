#!/usr/bin/env python3
"""Collect repeatable latency samples for one agent/team flow.

Usage:
  python3 scripts/perf_sampler.py --samples 20 --out-dir artifacts/perf \
    --round query:"查询维修工单" --round selection:"1" \
    --squad squad-id \
    --probe-cmd 'python3 scripts/remote_probe.py --squad-id {squad} --round {label} --message {message} {session}'

Why: a P95 claim needs >=20 fresh sessions walked through the same rounds, and
every number must be traceable to a raw payload. Picking two lucky runs is not
performance evidence.

Contract for `--probe-cmd`:

- it must print one JSON object per round on stdout;
- placeholders `{label}`, `{message}`, `{session}`, `{squad}` are substituted;
- `{session}` is `--session <id>` on later rounds and empty on the first round,
  so the probe starts a fresh session once per sample;
- the JSON must expose `session_id`, `terminal_status` and
  `timings.total_seconds`. Anything else is preserved verbatim in the artifacts.

Exit codes: 0 all samples complete and inside budget; 1 anything else.
"""

from __future__ import annotations

import argparse
import json
import shlex
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_ROUND_BUDGET_SECONDS = 120.0
DEFAULT_TOTAL_BUDGET_SECONDS = 180.0


def parse_round(spec: str) -> tuple[str, str]:
    label, sep, message = spec.partition(":")
    if not sep or not label.strip() or not message.strip():
        raise argparse.ArgumentTypeError("--round must look like label:message")
    return label.strip(), message.strip()


def percentile(values: list[float], quantile: float = 0.95) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    index = max(0, min(len(ordered) - 1, int(round(quantile * (len(ordered) - 1)))))
    return round(ordered[index], 3)


def render_command(template: str, *, label: str, message: str, session: str, squad: str) -> list[str]:
    quoted = shlex.quote
    rendered = (
        template.replace("{label}", quoted(label))
        .replace("{message}", quoted(message))
        .replace("{session}", "--session %s" % quoted(session) if session else "")
        .replace("{squad}", quoted(squad))
    )
    return shlex.split(rendered)


def run_probe(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            "probe exited %d: %s" % (completed.returncode, (completed.stderr or completed.stdout)[-600:])
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("probe did not print one JSON object: %s" % completed.stdout[:300]) from error


def summarize(samples: list[dict[str, Any]], rounds: list[str]) -> dict[str, Any]:
    per_round: dict[str, list[float]] = {label: [] for label in rounds}
    totals: list[float] = []
    failures: list[dict[str, Any]] = []
    for sample in samples:
        session_total = 0.0
        complete = True
        for label in rounds:
            payload = sample["rounds"].get(label)
            if not payload:
                complete = False
                continue
            if str(payload.get("terminal_status") or "") != "completed":
                failures.append(
                    {
                        "sample": sample["index"],
                        "round": label,
                        "terminal_status": payload.get("terminal_status"),
                        "failure_code": payload.get("failure_code"),
                    }
                )
                complete = False
            total = (payload.get("timings") or {}).get("total_seconds")
            if isinstance(total, (int, float)):
                per_round[label].append(float(total))
                session_total += float(total)
        if complete:
            totals.append(round(session_total, 3))
    return {
        "samples": len(samples),
        "complete_samples": len(totals),
        "per_round_p95_seconds": {label: percentile(v) for label, v in per_round.items()},
        "per_round_max_seconds": {label: round(max(v), 3) if v else None for label, v in per_round.items()},
        "session_p95_seconds": percentile(totals),
        "session_max_seconds": round(max(totals), 3) if totals else None,
        "session_mean_seconds": round(statistics.fmean(totals), 3) if totals else None,
        "failures": failures,
    }


def evaluate(summary: dict[str, Any], *, min_samples: int, round_budget: float, total_budget: float) -> list[str]:
    findings: list[str] = []
    if summary["complete_samples"] < min_samples:
        findings.append(
            "insufficient_samples: %d complete samples < %d required for a P95 claim"
            % (summary["complete_samples"], min_samples)
        )
    if summary["failures"]:
        findings.append("round_failures: %d round(s) did not complete" % len(summary["failures"]))
    for label, value in summary["per_round_p95_seconds"].items():
        if value is not None and value > round_budget:
            findings.append("p95_slow: %s p95=%.1fs > %.1fs budget" % (label, value, round_budget))
    total = summary["session_p95_seconds"]
    if total is not None and total > total_budget:
        findings.append("p95_session_slow: %.1fs > %.1fs budget" % (total, total_budget))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect repeatable latency samples")
    parser.add_argument("--round", dest="rounds", action="append", type=parse_round, required=True)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--squad", default="", help="substituted into {squad}")
    parser.add_argument("--probe-cmd", required=True, help="command template; see module docstring")
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument("--round-budget", type=float, default=DEFAULT_ROUND_BUDGET_SECONDS)
    parser.add_argument("--total-budget", type=float, default=DEFAULT_TOTAL_BUDGET_SECONDS)
    parser.add_argument("--keep-going", action="store_true", help="continue after a failed round")
    args = parser.parse_args(argv)
    if args.samples < 1:
        parser.error("--samples must be >= 1")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    labels = [label for label, _ in args.rounds]
    samples: list[dict[str, Any]] = []
    aborted = False
    for index in range(1, args.samples + 1):
        session = ""
        payloads: dict[str, Any] = {}
        for label, message in args.rounds:
            command = render_command(
                args.probe_cmd, label=label, message=message, session=session, squad=args.squad
            )
            try:
                payload = run_probe(command)
            except RuntimeError as error:
                print("perf_sampler: sample %d %s: %s" % (index, label, error), file=sys.stderr)
                payloads[label] = {"terminal_status": "probe_error", "failure_code": str(error)[:200]}
                aborted = not args.keep_going
                break
            payloads[label] = payload
            (args.out_dir / ("sample-%02d-%s.json" % (index, label))).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            session = session or str(payload.get("session_id") or "")
            if str(payload.get("terminal_status") or "") != "completed" and not args.keep_going:
                aborted = True
                break
        samples.append({"index": index, "session_id": session, "rounds": payloads})
        if aborted:
            break

    summary = summarize(samples, labels)
    findings = evaluate(
        summary,
        min_samples=args.min_samples,
        round_budget=args.round_budget,
        total_budget=args.total_budget,
    )
    report = {
        "schema": "industrial-agent-perf-sampler/1",
        "rounds": labels,
        "requested_samples": args.samples,
        "aborted": aborted,
        "summary": summary,
        "findings": findings,
    }
    (args.out_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "samples=%d complete=%d p95_session=%s findings=%s"
        % (summary["samples"], summary["complete_samples"], summary["session_p95_seconds"], findings or "none")
    )
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
