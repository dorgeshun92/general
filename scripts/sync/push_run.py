#!/usr/bin/env python3
"""Push local run outputs to the configured repository (Supabase, or memory for dry runs and tests).

    python scripts/sync/push_run.py <loan_id> <run_id> [--dry-run] [--backend memory|supabase]
    python scripts/sync/push_run.py --all [--dry-run]
    python scripts/sync/push_run.py --run-dir <path> [--dry-run]

Runs live under MPIRE_OUTPUT_DIR (default output/audits) as <loan_id>/<run_id>/. Every run goes
through services.run_loader.load_run_dir, which validates each JSON file against its schema and
refuses anything that still contains an unmasked SSN or account-number pattern. Only then is the
bundle handed to repository.upsert_run_bundle. Source documents are never read or sent.

When tests/fixtures/deidentified/<loan_id>/MANIFEST.yaml exists, its pii_pattern_allowlist entries
are passed to the loader and its description becomes the loan description.

--all      every <loan_id>/<run_id> directory that holds run_manifest.json or an audit file
           (preapproval_audit.json / submission_readiness.json). Intake-only runs (loan_file.json
           without a manifest) are skipped by --all but can be pushed explicitly.
--dry-run  load and validate, print a summary (counts, reports, sha256s), write nothing.
--backend  overrides MPIRE_REPO_BACKEND. memory discards everything at exit; it is only useful with
           --dry-run or in tests.

Exit codes: 0 ok; 1 nothing to sync / not found; 2 validation or PII failure, or a run directory
outside MPIRE_OUTPUT_DIR (fail closed: the file and reason are printed, never the offending value);
3 Supabase / network / configuration error. Keys are never printed.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from scripts.common.schema_registry import SchemaRegistryError  # noqa: E402
from scripts.intake.manifest import IntakeStopCondition, load_manifest  # noqa: E402
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
from services.models import Run, RunBundle  # noqa: E402
from services.repository import Repository  # noqa: E402
from services.run_loader import AUDIT_FILES, MANIFEST_FILE, RunLoadError, load_run_dir  # noqa: E402
from services.supabase_client import SupabaseError  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "deidentified"
SYNCABLE_FILES = (MANIFEST_FILE, *AUDIT_FILES)


# --------------------------------------------------------------------------------------
# Discovery and location checks
# --------------------------------------------------------------------------------------

def discover_run_dirs(output_dir: Path) -> list[Path]:
    """Every <output_dir>/<loan_id>/<run_id>/ holding a run manifest or an audit file, sorted."""
    if not output_dir.is_dir():
        return []
    found: list[Path] = []
    for loan_dir in sorted(p for p in output_dir.iterdir() if p.is_dir()):
        for run_dir in sorted(p for p in loan_dir.iterdir() if p.is_dir()):
            if any((run_dir / name).is_file() for name in SYNCABLE_FILES):
                found.append(run_dir)
    return found


def resolve_run_dir(output_dir: Path, run_dir: Path) -> Path:
    """Real path of run_dir, refused unless it is exactly <output_dir>/<loan_id>/<run_id>.

    Real paths are compared on both sides so symlinks and `..` cannot point the sync at a directory
    outside MPIRE_OUTPUT_DIR (the only place derived data may live)."""
    root = output_dir.resolve()
    real = run_dir.resolve()
    if real.parent.parent != root or real == root or real.parent == root:
        raise SyncError(
            f"refusing {run_dir}: not a <loan_id>/<run_id> directory under MPIRE_OUTPUT_DIR ({output_dir})",
            EXIT_INVALID,
        )
    return real


def fixture_context(loan_id: str, fixture_root: Optional[Path] = None) -> tuple[tuple[str, ...], Optional[str]]:
    """(pii_pattern_allowlist, description) from the fixture MANIFEST.yaml, or ((), None) when absent."""
    loan_dir = (fixture_root or FIXTURE_ROOT) / loan_id
    if not (loan_dir / "MANIFEST.yaml").is_file():
        return (), None
    try:
        manifest = load_manifest(loan_dir)
    except IntakeStopCondition as exc:
        raise SyncError(f"fixture MANIFEST.yaml for {loan_id} is not acceptable: {exc.reason}", EXIT_INVALID) from exc
    if manifest.loan_id != loan_id:
        raise SyncError(f"fixture MANIFEST.yaml under {loan_id} declares loan_id {manifest.loan_id}", EXIT_INVALID)
    description = manifest.description.strip() if manifest.description else None
    return manifest.pii_pattern_allowlist, description


# --------------------------------------------------------------------------------------
# Load, summarize, push
# --------------------------------------------------------------------------------------

def load_bundle(run_dir: Path, fixture_root: Optional[Path] = None) -> RunBundle:
    """Validated bundle or SyncError(exit 2). Messages name the file and the reason, never a value."""
    loan_id = run_dir.parent.name
    allowlist, description = fixture_context(loan_id, fixture_root)
    try:
        return load_run_dir(run_dir, pii_allowlist=allowlist, loan_description=description)
    except (RunLoadError, SchemaRegistryError) as exc:
        raise SyncError(f"{run_dir.parent.name}/{run_dir.name}: {exc}", EXIT_INVALID) from exc


def summarize(bundle: RunBundle) -> list[str]:
    run = bundle.run
    counts = Counter(f.result for f in bundle.findings)
    count_text = ", ".join(f"{k} {counts.get(k, 0)}" for k in ("PASS", "FAIL", "MISSING", "REVIEW", "NOT_APPLICABLE"))
    lines = [
        f"  loan: {run.loan_id}  run: {run.run_id}  skill: {run.skill or '-'}  completed_at: {run.completed_at or '-'}",
        f"  overall_status: {run.overall_status or 'none (no submission gate)'}  "
        f"preapproval: {'yes' if run.preapproval_present else 'no'}  submission: {'yes' if run.submission_present else 'no'}",
        f"  documents: {len(bundle.documents)}  findings: {len(bundle.findings)} ({count_text})  "
        f"blocking_open: {run.blocking_open if run.blocking_open is not None else '-'}  "
        f"coverage_percent: {run.coverage_percent or '-'}",
        f"  review_items: {len(bundle.review_items)}  missing_documents: {len(bundle.missing_documents)}  "
        f"conflicts: {len(bundle.conflicts)}  proposed_actions: {len(bundle.proposed_actions)}  "
        f"approvals_required: {len(bundle.approvals_required)}",
        f"  loan description: {bundle.loan.description or '-'}",
    ]
    if bundle.reports:
        for r in bundle.reports:
            lines.append(f"  report: {r.name}  sha256 {r.sha256}  ({len(r.content_md)} chars)")
    else:
        lines.append("  reports: none")
    return lines


def push_bundle(repo: Repository, bundle: RunBundle) -> Run:
    try:
        return repo.upsert_run_bundle(bundle)
    except (SupabaseError, httpx.HTTPError) as exc:
        raise backend_failure(exc) from exc


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _targets(args, output_dir: Path) -> list[Path]:
    if args.all:
        found = discover_run_dirs(output_dir)
        if not found:
            raise SyncError(f"nothing to sync: no <loan_id>/<run_id> directory with a run manifest or audit file under {output_dir}",
                            EXIT_NOT_FOUND)
        return [resolve_run_dir(output_dir, p) for p in found]
    if args.run_dir is not None:
        candidate = Path(args.run_dir)
        if not candidate.is_dir():
            raise SyncError(f"run directory not found: {candidate}", EXIT_NOT_FOUND)
        return [resolve_run_dir(output_dir, candidate)]
    candidate = output_dir / args.loan_id / args.run_id
    if not candidate.is_dir():
        raise SyncError(f"run {args.loan_id}/{args.run_id} not found under {output_dir}", EXIT_NOT_FOUND)
    return [resolve_run_dir(output_dir, candidate)]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("loan_id", nargs="?", help="loan id (directory under MPIRE_OUTPUT_DIR)")
    ap.add_argument("run_id", nargs="?", help="run id (directory under the loan)")
    ap.add_argument("--all", action="store_true", help="sync every run directory under MPIRE_OUTPUT_DIR")
    ap.add_argument("--run-dir", default=None, help="explicit <loan_id>/<run_id> directory (must be under MPIRE_OUTPUT_DIR)")
    add_backend_args(ap)
    args = ap.parse_args(argv)

    modes = sum([args.all, args.run_dir is not None, bool(args.loan_id or args.run_id)])
    if modes != 1 or (args.loan_id and not args.run_id) or (args.run_id and not args.loan_id):
        ap.error("give exactly one of: <loan_id> <run_id>, --all, or --run-dir <path>")

    try:
        settings = resolve_settings(args.backend)
        print(describe(settings, args.dry_run))
        if settings.backend == "memory" and not args.dry_run:
            print(MEMORY_NOTE)
        targets = _targets(args, settings.output_dir)
        repo = None if args.dry_run else build_repository(settings)
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code

    worst = EXIT_OK
    synced = 0
    for run_dir in targets:
        label = f"{run_dir.parent.name}/{run_dir.name}"
        try:
            bundle = load_bundle(run_dir)
            print(f"{'DRY-RUN' if args.dry_run else 'LOADED'} {label}")
            print("\n".join(summarize(bundle)))
            if repo is not None:
                run = push_bundle(repo, bundle)
                print(f"SYNCED {label} -> {settings.backend} (synced_at {run.synced_at})")
            synced += 1
        except SyncError as exc:
            print(f"FAILED {label}: {exc}", file=sys.stderr)
            worst = max(worst, exc.exit_code)
            if exc.exit_code == EXIT_BACKEND:
                break  # the backend is unreachable or refusing writes; do not keep hammering it
    verb = "validated" if args.dry_run else "synced"
    print(f"{verb}: {synced} of {len(targets)} run(s)")
    return worst


if __name__ == "__main__":
    sys.exit(main())
