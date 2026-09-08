#!/usr/bin/env python3
"""Validate a JSON or YAML document against one of the repository schemas.

    python scripts/validate_schema.py <file> --schema {loan_file,audit_result,document_inventory,checklist_catalog}

Exit codes: 0 valid, 1 invalid (errors printed), 2 could not validate (fail closed).
Beyond JSON Schema, this also enforces referential integrity:
  - loan_file: every evidence_id referenced exists in evidence[]; every document_id referenced exists in documents[]
  - audit_result: finding_ids unique
  - checklist_catalog: item ids unique; conflicts/consolidations reference known ids
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.common.schema_registry import SCHEMA_NAMES, SchemaRegistryError, validate_document  # noqa: E402


def load_any(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_load(text)
    return json.loads(text)


def _walk(obj, key):
    """Yield every value stored under `key` anywhere in a nested structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            else:
                yield from _walk(v, key)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item, key)


def integrity_errors(data, name: str) -> list[str]:
    errs: list[str] = []
    if not isinstance(data, dict):
        return errs
    if name == "loan_file":
        ev_ids = {e.get("evidence_id") for e in data.get("evidence", [])}
        doc_ids = {d.get("document_id") for d in data.get("documents", [])}
        if len(ev_ids) != len(data.get("evidence", [])):
            errs.append("/evidence: duplicate evidence_id values")
        if len(doc_ids) != len(data.get("documents", [])):
            errs.append("/documents: duplicate document_id values")
        for ids in _walk({k: v for k, v in data.items() if k != "evidence"}, "evidence_ids"):
            for ev in ids or []:
                if ev not in ev_ids:
                    errs.append(f"evidence_ids: '{ev}' is referenced but not present in evidence[]")
        for ids in _walk({k: v for k, v in data.items() if k != "documents"}, "document_ids"):
            for d in ids or []:
                if d not in doc_ids:
                    errs.append(f"document_ids: '{d}' is referenced but not present in documents[]")
        for ev in data.get("evidence", []):
            if ev.get("document_id") not in doc_ids:
                errs.append(f"/evidence/{ev.get('evidence_id')}: document_id '{ev.get('document_id')}' not in documents[]")
        for d in data.get("documents", []):
            if d.get("duplicate_of") and d["duplicate_of"] not in doc_ids:
                errs.append(f"/documents/{d.get('document_id')}: duplicate_of '{d['duplicate_of']}' not in documents[]")
    elif name == "audit_result":
        fids = [f.get("finding_id") for f in data.get("findings", [])]
        if len(set(fids)) != len(fids):
            errs.append("/findings: duplicate finding_id values")
        counts = data.get("summary", {}).get("counts", {})
        if counts:
            actual = {k: 0 for k in ("PASS", "FAIL", "MISSING", "REVIEW", "NOT_APPLICABLE")}
            for f in data.get("findings", []):
                if f.get("result") in actual:
                    actual[f["result"]] += 1
            for k, v in actual.items():
                if counts.get(k) != v:
                    errs.append(f"/summary/counts/{k}: declared {counts.get(k)} but findings contain {v}")
            blocking_open = sum(1 for f in data.get("findings", []) if f.get("blocking") and f.get("result") not in ("PASS", "NOT_APPLICABLE"))
            if data.get("summary", {}).get("blocking_open") != blocking_open:
                errs.append(f"/summary/blocking_open: declared {data['summary'].get('blocking_open')} but findings contain {blocking_open}")
        if data.get("audit_type") == "SUBMISSION_READINESS":
            status = data.get("overall_status")
            if status is None:
                errs.append("/overall_status: SUBMISSION_READINESS requires READY, NOT_READY, or HUMAN_REVIEW")
            if status == "READY":
                for f in data.get("findings", []):
                    if f.get("blocking") and f.get("result") not in ("PASS", "NOT_APPLICABLE"):
                        errs.append(f"/overall_status: READY is not allowed; blocking finding {f.get('finding_id')} is {f.get('result')}")
                    if f.get("result") == "PASS" and not f.get("evidence_ids"):
                        errs.append(f"/overall_status: READY is not allowed; PASS finding {f.get('finding_id')} has no evidence")
                if data.get("los_export_present") is False:
                    errs.append("/overall_status: READY is not allowed without an LOS export (los_export_present is false)")
        elif data.get("audit_type") == "PREAPPROVAL" and data.get("overall_status") is not None:
            errs.append("/overall_status: must be null for PREAPPROVAL audits")
    elif name == "checklist_catalog":
        ids = [i.get("id") for i in data.get("items", [])]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            errs.append(f"/items: duplicate ids {sorted(dup)}")
        src_ids = {s.get("source_id") for s in data.get("sources", [])}
        for i in data.get("items", []):
            if i.get("source_document") not in src_ids:
                errs.append(f"/items/{i.get('id')}: source_document '{i.get('source_document')}' not in sources[]")
            if i.get("ambiguous") and "REVIEW" not in (i.get("result_values") or []):
                errs.append(f"/items/{i.get('id')}: ambiguous items must allow REVIEW")
            if "NOT_APPLICABLE" in (i.get("result_values") or []) and i.get("applies_when", "").strip().lower() == "always":
                errs.append(f"/items/{i.get('id')}: applies_when 'always' cannot produce NOT_APPLICABLE")
        known = set(ids)
        for group_key in ("conflicts", "consolidation_decisions"):
            for rec in data.get(group_key, []):
                for rid in rec.get("rule_ids", []):
                    if rid not in known:
                        errs.append(f"/{group_key}: rule_id '{rid}' not in items[]")
    return errs


def validate_path(path: Path, name: str) -> list[str]:
    data = load_any(path)
    errors = validate_document(data, name)
    if not errors:
        errors = integrity_errors(data, name)
    return errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", type=Path)
    ap.add_argument("--schema", required=True, choices=SCHEMA_NAMES)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    try:
        errors = validate_path(args.file, args.schema)
    except (SchemaRegistryError, OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: could not validate {args.file}: {exc}", file=sys.stderr)
        return 2
    if errors:
        if not args.quiet:
            print(f"INVALID: {args.file} against {args.schema} ({len(errors)} error(s))")
            for e in errors:
                print(f"  - {e}")
        return 1
    if not args.quiet:
        print(f"VALID: {args.file} against {args.schema}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
