"""Load and query config/checklist_catalog.yaml.

    from scripts.audit.catalog import load_catalog
    cat = load_catalog()                      # fails closed (CatalogError) if invalid
    cat.is_empty                              # True while items: []
    cat.is_reviewed                           # False while reviewed_by is null
    cat.items_in_phase("PREAPPROVAL")         # list[dict]
    cat.get("PRE-CREDIT-001")                 # dict or KeyError
    cat.review_status_label()                 # "catalog not yet reviewed by a licensed reviewer"
    cat.meta_for_phase("SUBMISSION")          # dict consumed by readiness_gate.finalize_audit_result

The catalog is validated against schemas/checklist_catalog.schema.json AND the
referential-integrity checks in scripts/validate_schema.py before any item is
exposed. An invalid or unreadable catalog raises CatalogError; callers must
treat that as a stop condition, never as "no rules".

CLI:  python scripts/audit/catalog.py [--catalog config/checklist_catalog.yaml] [--phase PREAPPROVAL|SUBMISSION]
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.common.hashing import sha256_file  # noqa: E402
from scripts.common.schema_registry import SchemaRegistryError, validate_document  # noqa: E402
from scripts.validate_schema import integrity_errors  # noqa: E402

DEFAULT_CATALOG_PATH = REPO_ROOT / "config" / "checklist_catalog.yaml"
PHASES = ("PREAPPROVAL", "SUBMISSION")
PHASE_PREFIX = {"PREAPPROVAL": "PRE", "SUBMISSION": "SUB"}
UNREVIEWED_LABEL = "catalog not yet reviewed by a licensed reviewer"
EMPTY_LABEL = "catalog is EMPTY (items: []) — no checklist rules exist; run docs/checklist-normalization.md"


class CatalogError(RuntimeError):
    """The catalog could not be loaded or did not validate. Treat as a stop condition."""


@dataclass(frozen=True)
class Catalog:
    path: Path
    sha256: str
    version: str
    reviewed_by: str | None
    reviewed_at: str | None
    normalized_at: str | None
    items: tuple[dict[str, Any], ...]
    sources: tuple[dict[str, Any], ...]
    conflicts: tuple[dict[str, Any], ...]
    consolidation_decisions: tuple[dict[str, Any], ...]
    _by_id: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False, compare=False)

    # ---- status -------------------------------------------------------
    @property
    def is_empty(self) -> bool:
        return len(self.items) == 0

    @property
    def is_reviewed(self) -> bool:
        return self.reviewed_by is not None and str(self.reviewed_by).strip() != ""

    def review_status_label(self) -> str:
        if self.is_reviewed:
            return f"reviewed by {self.reviewed_by}" + (f" on {self.reviewed_at}" if self.reviewed_at else "")
        return UNREVIEWED_LABEL

    def describe(self) -> str:
        """One-line, human-readable status suitable for report headers and completion reports."""
        size = EMPTY_LABEL if self.is_empty else f"{len(self.items)} item(s)"
        return f"Checklist catalog v{self.version} ({self.path.name}): {size}; {self.review_status_label()}."

    # ---- lookup -------------------------------------------------------
    def items_in_phase(self, phase: str) -> list[dict[str, Any]]:
        if phase not in PHASES:
            raise CatalogError(f"unknown phase '{phase}'; expected one of {PHASES}")
        return [dict(i) for i in self.items if i.get("phase") == phase]

    def rule_ids_in_phase(self, phase: str) -> list[str]:
        return [i["id"] for i in self.items_in_phase(phase)]

    def get(self, rule_id: str) -> dict[str, Any]:
        try:
            return dict(self._by_id[rule_id])
        except KeyError:
            raise KeyError(f"rule id '{rule_id}' is not in the catalog ({self.path})") from None

    def __contains__(self, rule_id: object) -> bool:
        return rule_id in self._by_id

    def meta_for_phase(self, phase: str) -> dict[str, Any]:
        """Metadata bundle consumed by scripts.audit.readiness_gate.finalize_audit_result."""
        return {
            "path": _display_path(self.path),
            "sha256": self.sha256,
            "version": self.version,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "is_empty": self.is_empty,
            "items_in_phase": self.items_in_phase(phase),
        }


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def load_catalog(path: str | Path = DEFAULT_CATALOG_PATH) -> Catalog:
    """Load, validate, and index the catalog. Raises CatalogError on any problem (fail closed)."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"cannot read catalog {p}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError(f"catalog {p} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise CatalogError(f"catalog {p} must be a mapping at the top level")
    try:
        errors = validate_document(data, "checklist_catalog")
    except SchemaRegistryError as exc:
        raise CatalogError(f"cannot validate catalog {p}: {exc}") from exc
    if not errors:
        errors = integrity_errors(data, "checklist_catalog")
    if errors:
        raise CatalogError(f"catalog {p} failed validation ({len(errors)} error(s)): " + "; ".join(errors))

    items = tuple(dict(i) for i in data.get("items") or [])
    by_id = {i["id"]: i for i in items}
    for item in items:
        expected_prefix = PHASE_PREFIX.get(item.get("phase"))
        if expected_prefix and not item["id"].startswith(expected_prefix + "-"):
            raise CatalogError(f"catalog item {item['id']} has phase {item.get('phase')} but its id prefix does not match")
    return Catalog(
        path=p,
        sha256=sha256_file(p),
        version=str(data["catalog_version"]),
        reviewed_by=data.get("reviewed_by"),
        reviewed_at=data.get("reviewed_at"),
        normalized_at=data.get("normalized_at"),
        items=items,
        sources=tuple(data.get("sources") or []),
        conflicts=tuple(data.get("conflicts") or []),
        consolidation_decisions=tuple(data.get("consolidation_decisions") or []),
        _by_id=by_id,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Inspect the checklist catalog (read-only).")
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    ap.add_argument("--phase", choices=PHASES)
    ap.add_argument("--id", dest="rule_id", help="print one item by id")
    args = ap.parse_args(argv)
    try:
        cat = load_catalog(args.catalog)
    except CatalogError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(cat.describe())
    if args.rule_id:
        try:
            item = cat.get(args.rule_id)
        except KeyError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(yaml.safe_dump(item, sort_keys=False))
        return 0
    phases = [args.phase] if args.phase else list(PHASES)
    for phase in phases:
        items = cat.items_in_phase(phase)
        print(f"{phase}: {len(items)} item(s)")
        for item in items:
            print(f"  {item['id']}  [{item['category']}] blocking={item['blocking_if_failed']} method={item['evaluation_method']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
