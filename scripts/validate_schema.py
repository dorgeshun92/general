#!/usr/bin/env python3
"""Validate a JSON or YAML document against one of the repository schemas.

    python scripts/validate_schema.py <file> --schema {loan_file,audit_result,document_inventory,checklist_catalog}

Exit codes: 0 valid, 1 invalid (errors printed), 2 could not validate (fail closed).
Beyond JSON Schema, validate_document also enforces referential integrity (scripts/common/integrity.py):
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
from scripts.common.integrity import integrity_errors  # noqa: E402,F401  (re-exported for tests)
from scripts.common.schema_registry import SCHEMA_NAMES, SchemaRegistryError, validate_document  # noqa: E402


def load_any(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_load(text)
    return json.loads(text)


def validate_path(path: Path, name: str) -> list[str]:
    data = load_any(path)
    return validate_document(data, name)  # schema errors, then integrity errors


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
