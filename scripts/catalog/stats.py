#!/usr/bin/env python3
"""Print checklist-catalog statistics after validating it.

    python scripts/catalog/stats.py [--catalog config/checklist_catalog.yaml] [--json]

Counts by phase, category, evaluation_method, blocking_if_failed, ambiguous;
unresolved conflicts; consolidation decisions awaiting approval; review status.
Exit codes: 0 valid, 1 schema/integrity errors (stats still printed), 2 could not read.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.common.schema_registry import SchemaRegistryError, validate_document  # noqa: E402
from scripts.validate_schema import integrity_errors  # noqa: E402


def compute_stats(catalog: dict[str, Any]) -> dict[str, Any]:
    items = [i for i in (catalog.get("items") or []) if isinstance(i, dict)]
    conflicts = [c for c in (catalog.get("conflicts") or []) if isinstance(c, dict)]
    decisions = [d for d in (catalog.get("consolidation_decisions") or []) if isinstance(d, dict)]

    def count(key: str) -> dict[str, int]:
        return dict(sorted(Counter(str(i.get(key)) for i in items).items()))

    return {
        "catalog_version": catalog.get("catalog_version"),
        "sources": len(catalog.get("sources") or []),
        "items": len(items),
        "by_phase": count("phase"),
        "by_category": count("category"),
        "by_evaluation_method": count("evaluation_method"),
        "by_blocking_if_failed": count("blocking_if_failed"),
        "by_ambiguous": dict(sorted(Counter(str(bool(i.get("ambiguous", False))) for i in items).items())),
        "withdrawn": sum(1 for i in items if str(i.get("notes", "")).startswith("WITHDRAWN")),
        "conflicts_total": len(conflicts),
        "conflicts_unresolved": [c.get("conflict_id") for c in conflicts if c.get("resolution") is None],
        "consolidation_decisions_total": len(decisions),
        "consolidation_decisions_unapproved": [d.get("decision_id") for d in decisions if d.get("approved_by") is None],
        "normalized_at": catalog.get("normalized_at"),
        "reviewed_by": catalog.get("reviewed_by"),
        "reviewed_at": catalog.get("reviewed_at"),
        "reviewed": bool(catalog.get("reviewed_by")) and bool(catalog.get("reviewed_at")),
    }


def render_text(stats: dict[str, Any], errors: list[str]) -> str:
    lines = [f"Catalog version: {stats['catalog_version']}",
             f"Validation: {'VALID' if not errors else f'INVALID ({len(errors)} error(s))'}"]
    lines += [f"  - {e}" for e in errors]
    lines += [f"Sources: {stats['sources']}", f"Items: {stats['items']}"]

    def block(title: str, counts: dict[str, int]) -> None:
        lines.append(f"{title}:")
        if not counts:
            lines.append("  (none)")
        for k, v in counts.items():
            lines.append(f"  {k}: {v}")

    block("By phase", stats["by_phase"])
    block("By category", stats["by_category"])
    block("By evaluation_method", stats["by_evaluation_method"])
    block("By blocking_if_failed", stats["by_blocking_if_failed"])
    block("By ambiguous", stats["by_ambiguous"])
    lines.append(f"Withdrawn items: {stats['withdrawn']}")
    lines.append(f"Conflicts: {stats['conflicts_total']} total, "
                 f"{len(stats['conflicts_unresolved'])} unresolved"
                 + (f" ({', '.join(stats['conflicts_unresolved'])})" if stats["conflicts_unresolved"] else ""))
    lines.append(f"Consolidation decisions: {stats['consolidation_decisions_total']} total, "
                 f"{len(stats['consolidation_decisions_unapproved'])} awaiting approval")
    lines.append(f"Normalized at: {stats['normalized_at']}")
    lines.append("Reviewed: " + (f"yes by {stats['reviewed_by']} at {stats['reviewed_at']}" if stats["reviewed"]
                                 else "NO — catalog not yet reviewed by a licensed reviewer"))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", type=Path, default=REPO_ROOT / "config/checklist_catalog.yaml")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)
    try:
        catalog = yaml.safe_load(args.catalog.read_text(encoding="utf-8")) or {}
        if not isinstance(catalog, dict):
            raise ValueError("catalog must be a mapping")
        errors = validate_document(catalog, "checklist_catalog")
        if not errors:
            errors = integrity_errors(catalog, "checklist_catalog")
    except (OSError, ValueError, yaml.YAMLError, SchemaRegistryError) as exc:
        print(f"ERROR: could not read or validate {args.catalog}: {exc}", file=sys.stderr)
        return 2
    stats = compute_stats(catalog)
    if args.json:
        print(json.dumps({"stats": stats, "validation_errors": errors}, indent=2))
    else:
        print(render_text(stats, errors), end="")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
