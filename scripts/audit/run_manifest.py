"""Run manifests: every input and output of a run, hashed, so the run is reproducible and tamper-evident.

    from scripts.audit.run_manifest import write_run_manifest, verify_run_manifest
    write_run_manifest(run_dir, inputs=[...], outputs=[...], skill="preapproval-audit",
                       run_id="RUN-1", loan_id="LN-TEST-0001", stop_condition=None)
    verify_run_manifest(run_dir) -> {"ok": bool, "checked": int, "mismatches": [...]}

CLI:
    python scripts/audit/run_manifest.py write --run-dir <dir> --skill <s> --run-id <r> --loan-id <l>
        [--input <path>]... [--output <path>]... [--stop-condition "<text>"]
    python scripts/audit/run_manifest.py verify <run_dir>
Exit codes: 0 ok, 1 mismatch / missing file, 2 could not read or write (fail closed).

Every listed file must exist when the manifest is written; a missing file raises instead of
producing a manifest with holes. Paths are stored relative to the repository root when they
lie inside it, otherwise as given.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.common.hashing import sha256_file  # noqa: E402

MANIFEST_NAME = "run_manifest.json"
MANIFEST_SCHEMA_VERSION = "1.0"


class ManifestError(RuntimeError):
    pass


def tool_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for dist in ("jsonschema", "pypdf", "PyYAML", "referencing"):
        try:
            versions[dist] = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else REPO_ROOT / p


def _file_record(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ManifestError(f"file listed in manifest does not exist: {p}")
    return {"path": _display_path(p), "sha256": sha256_file(p), "size_bytes": p.stat().st_size}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_run_manifest(
    run_dir: str | Path,
    inputs: Iterable[str | Path],
    outputs: Iterable[str | Path],
    skill: str,
    run_id: str,
    loan_id: str,
    stop_condition: str | None = None,
    *,
    started_at: str | None = None,
) -> Path:
    run_dir = Path(run_dir)
    manifest_path = run_dir / MANIFEST_NAME
    input_records = [_file_record(p) for p in inputs]
    output_records = [_file_record(p) for p in outputs if Path(p).resolve() != manifest_path.resolve()]
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "loan_id": loan_id,
        "run_id": run_id,
        "skill": skill,
        "started_at": started_at,
        "completed_at": _now_iso(),
        "stop_condition": stop_condition,
        "completed_normally": stop_condition is None,
        "tool_versions": tool_versions(),
        "inputs": input_records,
        "outputs": output_records,
        "totals": {
            "inputs": len(input_records),
            "outputs": len(output_records),
            "input_bytes": sum(r["size_bytes"] for r in input_records),
            "output_bytes": sum(r["size_bytes"] for r in output_records),
        },
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return manifest_path


def read_run_manifest(run_dir: str | Path) -> dict[str, Any]:
    path = Path(run_dir) / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read {path}: {exc}") from exc
    for key in ("inputs", "outputs", "run_id", "loan_id", "skill"):
        if key not in data:
            raise ManifestError(f"{path} is missing '{key}'")
    return data


def verify_run_manifest(run_dir: str | Path) -> dict[str, Any]:
    """Recompute every hash. Returns {"ok", "checked", "mismatches": [{path, kind, expected, actual, reason}]}."""
    manifest = read_run_manifest(run_dir)
    mismatches: list[dict[str, Any]] = []
    checked = 0
    for kind in ("inputs", "outputs"):
        for rec in manifest.get(kind, []):
            checked += 1
            p = _resolve(rec["path"])
            if not p.is_file():
                mismatches.append({"path": rec["path"], "kind": kind, "reason": "MISSING", "expected": rec.get("sha256"), "actual": None})
                continue
            actual = sha256_file(p)
            if actual != rec.get("sha256"):
                mismatches.append({"path": rec["path"], "kind": kind, "reason": "HASH_MISMATCH", "expected": rec.get("sha256"), "actual": actual})
            elif "size_bytes" in rec and p.stat().st_size != rec["size_bytes"]:
                mismatches.append({"path": rec["path"], "kind": kind, "reason": "SIZE_MISMATCH", "expected": rec["size_bytes"], "actual": p.stat().st_size})
    return {"ok": not mismatches, "checked": checked, "mismatches": mismatches, "run_id": manifest["run_id"], "loan_id": manifest["loan_id"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="write run_manifest.json")
    w.add_argument("--run-dir", required=True, type=Path)
    w.add_argument("--skill", required=True)
    w.add_argument("--run-id", required=True)
    w.add_argument("--loan-id", required=True)
    w.add_argument("--input", action="append", default=[], type=Path)
    w.add_argument("--output", action="append", default=[], type=Path)
    w.add_argument("--stop-condition", default=None)
    v = sub.add_parser("verify", help="recompute hashes in run_manifest.json")
    v.add_argument("run_dir", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "write":
            path = write_run_manifest(args.run_dir, args.input, args.output, args.skill, args.run_id, args.loan_id, args.stop_condition)
            print(f"WROTE: {path}")
            return 0
        report = verify_run_manifest(args.run_dir)
    except (ManifestError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if report["ok"]:
        print(f"VERIFIED: {report['checked']} file(s) match run_manifest.json in {args.run_dir}")
        return 0
    print(f"MISMATCH: {len(report['mismatches'])} of {report['checked']} file(s) differ from run_manifest.json")
    for m in report["mismatches"]:
        print(f"  - {m['kind']} {m['path']}: {m['reason']} (expected {m['expected']}, actual {m['actual']})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
