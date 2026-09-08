#!/usr/bin/env python3
"""Push an eval_report.json (from scripts/eval/run_eval.py) to the configured repository.

    python scripts/sync/push_eval.py <path/to/eval_report.json> [--dry-run] [--backend memory|supabase]

The file becomes one EvalReport row: all_targets_met, the Section 10 target table, and the full
report. all_targets_met is taken from the report when present (top level, else under "aggregate")
and otherwise derived from the targets: met unless any target is MISSED. The whole document is
scanned for unmasked PII patterns first and refused if any is found.

Exit codes: 0 ok; 1 file not found; 2 unreadable, malformed, or PII failure (fail closed);
3 Supabase / network / configuration error. Keys are never printed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from scripts.common.masking import contains_unmasked_pii  # noqa: E402
from scripts.sync.common import (  # noqa: E402
    EXIT_BACKEND,
    EXIT_INVALID,
    EXIT_NOT_FOUND,
    EXIT_OK,
    MEMORY_NOTE,
    SyncError,
    add_backend_args,
    backend_failure,
    build_repository,
    describe,
    resolve_settings,
)
from services.models import EvalReport  # noqa: E402
from services.repository import Repository  # noqa: E402
from services.supabase_client import SupabaseError  # noqa: E402

# Not borrower data: compact timestamps (report directory stamps shaped <YYYYMMDD>T<HHMMSS>Z) and hex
# digests would otherwise trip the 8+ digit heuristic.
_TIMESTAMP_RE = re.compile(r"\b\d{8}T\d{4,6}Z?\b")
_SHA256_RE = re.compile(r"\b[0-9a-f]{64}\b")


def read_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SyncError(f"eval report not found: {path}", EXIT_NOT_FOUND)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"{path.name}: cannot read as JSON: {exc}", EXIT_INVALID) from exc
    if not isinstance(data, dict):
        raise SyncError(f"{path.name}: expected a JSON object at the top level", EXIT_INVALID)
    return data


def pii_gate(name: str, report: dict[str, Any]) -> None:
    text = json.dumps(report, ensure_ascii=False)
    text = _SHA256_RE.sub(" ", _TIMESTAMP_RE.sub(" ", text))
    hits = contains_unmasked_pii(text)
    if hits:
        raise SyncError(f"{name} contains unmasked PII patterns: {', '.join(hits)}; refusing to send it", EXIT_INVALID)


def build_eval_report(report: dict[str, Any], name: str = "eval_report.json") -> EvalReport:
    """Map an eval_report.json document to the EvalReport model. Raises SyncError(exit 2) when malformed."""
    aggregate = report.get("aggregate") if isinstance(report.get("aggregate"), dict) else {}
    targets = report.get("targets")
    if not isinstance(targets, dict):
        targets = aggregate.get("targets")
    if not isinstance(targets, dict) or not targets:
        raise SyncError(f"{name}: no 'targets' table found (top level or under 'aggregate')", EXIT_INVALID)
    if "all_targets_met" in report:
        all_met = report["all_targets_met"]
    elif "all_targets_met" in aggregate:
        all_met = aggregate["all_targets_met"]
    else:
        all_met = all(isinstance(t, dict) and t.get("status") != "MISSED" for t in targets.values())
    if not isinstance(all_met, bool):
        raise SyncError(f"{name}: all_targets_met must be a boolean", EXIT_INVALID)
    generated_at = report.get("generated_at")
    try:
        return EvalReport(generated_at=generated_at if isinstance(generated_at, str) else None,
                          all_targets_met=all_met, targets=targets, report=report)
    except ValidationError as exc:
        raise SyncError(f"{name}: does not fit the EvalReport model: {exc.error_count()} error(s)", EXIT_INVALID) from exc


def summarize(report: EvalReport) -> list[str]:
    lines = [f"  generated_at: {report.generated_at or '-'}  all_targets_met: {report.all_targets_met}"]
    for key, target in report.targets.items():
        status = target.get("status") if isinstance(target, dict) else target
        observed = target.get("observed") if isinstance(target, dict) else ""
        lines.append(f"  {str(status):<13} {key}: {observed or ''}".rstrip())
    return lines


def push_report(repo: Repository, report: EvalReport) -> EvalReport:
    try:
        return repo.add_eval_report(report)
    except (SupabaseError, httpx.HTTPError) as exc:
        raise backend_failure(exc) from exc


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", help="path to eval_report.json")
    add_backend_args(ap)
    args = ap.parse_args(argv)
    path = Path(args.report)
    try:
        settings = resolve_settings(args.backend)
        print(describe(settings, args.dry_run))
        if settings.backend == "memory" and not args.dry_run:
            print(MEMORY_NOTE)
        raw = read_report(path)
        pii_gate(path.name, raw)
        report = build_eval_report(raw, path.name)
        print(f"{'DRY-RUN' if args.dry_run else 'LOADED'} {path}")
        print("\n".join(summarize(report)))
        if args.dry_run:
            return EXIT_OK
        repo = build_repository(settings)
        stored = push_report(repo, report)
        print(f"SYNCED eval report -> {settings.backend} (eval_id {stored.eval_id or '-'}, generated_at {stored.generated_at or '-'})")
        return EXIT_OK
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
