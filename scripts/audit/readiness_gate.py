"""Readiness gate: pure, deterministic status computation plus audit_result assembly.

    from scripts.audit.readiness_gate import compute_gate, finalize_audit_result

compute_gate(findings, catalog_items_in_phase, los_export_present, *, catalog_reviewed=False)
    -> dict with overall_status, summary, coverage, id lists, and the reasons behind the status.

Status precedence (documented so a licensed reviewer can confirm it):
  1. NOT_READY   when any blocking finding is FAIL or MISSING, or los_export_present is False.
                 A hard blocking failure is never softened to HUMAN_REVIEW by an unrelated
                 review item; the review items are still listed in `reasons`.
  2. HUMAN_REVIEW when (in the absence of 1) any blocking finding is REVIEW, catalog coverage
                 is below 100% (including an empty catalog), a catalog item has more than one
                 finding, a finding cites a rule id that is not in the catalog, the catalog is
                 unreviewed, or a PASS / NOT_APPLICABLE finding cites no evidence.
  3. READY       only when none of the above holds.

`catalog_reviewed` defaults to False (fail closed): a caller that does not state the catalog
was reviewed by a licensed reviewer never receives READY.

finalize_audit_result(partial, findings, catalog_meta, inputs) assembles a complete
audit_result document, adds default known_limitations, validates it against
schemas/audit_result.schema.json plus the referential checks in scripts/validate_schema.py,
and raises AuditResultInvalid on failure. It never writes files.

Decimal is used for the coverage percentage. No floats anywhere in this module.
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any, Iterable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.common.hashing import sha256_file  # noqa: E402
from scripts.common.schema_registry import SchemaRegistryError, validate_document  # noqa: E402
from scripts.validate_schema import integrity_errors  # noqa: E402

RESULT_VALUES = ("PASS", "FAIL", "MISSING", "REVIEW", "NOT_APPLICABLE")
CLOSED_RESULTS = ("PASS", "NOT_APPLICABLE")
HARD_OPEN_RESULTS = ("FAIL", "MISSING")
DEFAULT_APPROVED_SOURCES_PATH = REPO_ROOT / "config" / "approved_sources.yaml"

LIMIT_SELF_EMPLOYED = "Self-employed income is classified only, not calculated (v1)."
LIMIT_VARIABLE_INCOME = (
    "Commission, bonus, overtime, rental, asset depletion, and guideline-dependent income are not "
    "calculated (v1); such items are classified and routed to REVIEW."
)
LIMIT_NO_SOURCES = "No approved guideline sources are registered; guideline_lookup items are REVIEW."
LIMIT_CATALOG_EMPTY = "Checklist catalog is empty (items: []); no checklist rules were evaluated."
LIMIT_CATALOG_UNREVIEWED = "Checklist catalog has not been reviewed by a licensed reviewer."
LIMIT_NO_LOS_EXPORT = "No LOS export was provided; no claim is made about LOS (Arive/LendingPad) completeness."
LIMIT_DECISION_SUPPORT = "Decision support only; this is not a credit or compliance decision."


class GateError(ValueError):
    """Inputs to the gate were malformed (e.g. a finding without a result)."""


class AuditResultInvalid(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__(f"audit_result failed validation ({len(errors)} error(s)): " + "; ".join(errors))
        self.errors = errors


def _rule_ids(catalog_items_in_phase: Iterable[Any]) -> list[str]:
    ids: list[str] = []
    for item in catalog_items_in_phase or []:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.append(item["id"])
        else:
            raise GateError(f"catalog item must be a rule id string or a dict with 'id': {item!r}")
    if len(set(ids)) != len(ids):
        raise GateError("catalog_items_in_phase contains duplicate rule ids")
    return ids


def coverage_percent(items_evaluated: int, catalog_items_in_phase: int) -> str:
    """Decimal string with two places, rounded DOWN so 99.995 never displays as 100.00."""
    if catalog_items_in_phase <= 0:
        return "0.00"
    pct = (Decimal(items_evaluated) / Decimal(catalog_items_in_phase)) * Decimal(100)
    return str(pct.quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def compute_gate(
    findings: list[dict[str, Any]],
    catalog_items_in_phase: Iterable[Any],
    los_export_present: bool | None,
    *,
    catalog_reviewed: bool = False,
) -> dict[str, Any]:
    """Pure function. See module docstring for the precedence rules."""
    catalog_ids = _rule_ids(catalog_items_in_phase)
    catalog_set = set(catalog_ids)

    counts = {k: 0 for k in RESULT_VALUES}
    blocking_ids: list[str] = []
    nonblocking_ids: list[str] = []
    blocking_open_ids: list[str] = []
    blocking_hard_open_ids: list[str] = []
    blocking_review_ids: list[str] = []
    unevidenced_closed_ids: list[str] = []
    per_rule: Counter[str] = Counter()
    off_catalog: list[str] = []

    for f in findings or []:
        fid = f.get("finding_id")
        result = f.get("result")
        rule_id = f.get("rule_id")
        if result not in RESULT_VALUES:
            raise GateError(f"finding {fid!r} has unknown result {result!r}")
        if not isinstance(rule_id, str) or not rule_id:
            raise GateError(f"finding {fid!r} has no rule_id")
        counts[result] += 1
        per_rule[rule_id] += 1
        if rule_id not in catalog_set and rule_id not in off_catalog:
            off_catalog.append(rule_id)
        blocking = bool(f.get("blocking"))
        (blocking_ids if blocking else nonblocking_ids).append(fid)
        if result in CLOSED_RESULTS and not f.get("evidence_ids"):
            unevidenced_closed_ids.append(fid)
        if blocking and result not in CLOSED_RESULTS:
            blocking_open_ids.append(fid)
            if result in HARD_OPEN_RESULTS:
                blocking_hard_open_ids.append(fid)
            elif result == "REVIEW":
                blocking_review_ids.append(fid)

    evaluated = [rid for rid in catalog_ids if per_rule.get(rid, 0) >= 1]
    unevaluated = [rid for rid in catalog_ids if per_rule.get(rid, 0) == 0]
    duplicated = [rid for rid in catalog_ids if per_rule.get(rid, 0) > 1]
    n_catalog = len(catalog_ids)
    n_evaluated = len(evaluated)
    full_coverage = n_catalog > 0 and n_evaluated == n_catalog and not duplicated and not off_catalog

    reasons: list[str] = []
    if blocking_hard_open_ids:
        reasons.append(f"blocking FAIL/MISSING findings: {', '.join(blocking_hard_open_ids)}")
    if los_export_present is not True:
        reasons.append("LOS export not present (los_export_present is not true)")
    if blocking_review_ids:
        reasons.append(f"blocking REVIEW findings: {', '.join(blocking_review_ids)}")
    if n_catalog == 0:
        reasons.append("catalog has no items in this phase (coverage undefined, treated as 0.00%)")
    elif unevaluated:
        reasons.append(f"catalog coverage below 100%: unevaluated {', '.join(unevaluated)}")
    if duplicated:
        reasons.append(f"catalog items with more than one finding: {', '.join(duplicated)}")
    if off_catalog:
        reasons.append(f"findings cite rule ids not in the catalog: {', '.join(off_catalog)}")
    if not catalog_reviewed:
        reasons.append("catalog not yet reviewed by a licensed reviewer")
    if unevidenced_closed_ids:
        reasons.append(f"PASS/NOT_APPLICABLE findings without evidence: {', '.join(unevidenced_closed_ids)}")

    if blocking_hard_open_ids or los_export_present is not True:
        status = "NOT_READY"
    elif (
        blocking_review_ids
        or not full_coverage
        or not catalog_reviewed
        or unevidenced_closed_ids
    ):
        status = "HUMAN_REVIEW"
    else:
        status = "READY"

    return {
        "overall_status": status,
        "summary": {
            "counts": counts,
            "blocking_open": len(blocking_open_ids),
            "coverage": {
                "catalog_items_in_phase": n_catalog,
                "items_evaluated": n_evaluated,
                "coverage_percent": coverage_percent(n_evaluated, n_catalog),
                "unevaluated_rule_ids": unevaluated,
            },
        },
        "blocking_findings": blocking_ids,
        "nonblocking_findings": nonblocking_ids,
        "blocking_open_ids": blocking_open_ids,
        "blocking_review_ids": blocking_review_ids,
        "unevidenced_pass_ids": unevidenced_closed_ids,
        "duplicate_rule_ids": duplicated,
        "off_catalog_rule_ids": off_catalog,
        "catalog_reviewed": bool(catalog_reviewed),
        "los_export_present": los_export_present is True,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# audit_result assembly
# ---------------------------------------------------------------------------

def approved_sources_registered(path: str | Path = DEFAULT_APPROVED_SOURCES_PATH) -> bool:
    """True only when config/approved_sources.yaml lists at least one source. Unreadable -> False."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    return isinstance(data, dict) and bool(data.get("sources"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_inputs(inputs: Iterable[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in inputs or []:
        if isinstance(item, dict):
            rec = {"path": str(item["path"]), "sha256": item["sha256"]}
            if item.get("role"):
                rec["role"] = str(item["role"])
            out.append(rec)
        else:
            p = Path(item)
            out.append({"path": str(item), "sha256": sha256_file(p)})
    return out


def default_known_limitations(
    *,
    catalog_meta: dict[str, Any],
    los_export_present: bool | None,
    approved_sources_path: str | Path = DEFAULT_APPROVED_SOURCES_PATH,
) -> list[str]:
    lines = [LIMIT_DECISION_SUPPORT, LIMIT_SELF_EMPLOYED, LIMIT_VARIABLE_INCOME]
    if not approved_sources_registered(approved_sources_path):
        lines.append(LIMIT_NO_SOURCES)
    if catalog_meta.get("is_empty") or not catalog_meta.get("items_in_phase"):
        lines.append(LIMIT_CATALOG_EMPTY)
    if not catalog_meta.get("reviewed_by"):
        lines.append(LIMIT_CATALOG_UNREVIEWED)
    if los_export_present is not True:
        lines.append(LIMIT_NO_LOS_EXPORT)
    return lines


def finalize_audit_result(
    partial: dict[str, Any],
    findings: list[dict[str, Any]],
    catalog_meta: dict[str, Any],
    inputs: Iterable[Any],
    *,
    approved_sources_path: str | Path = DEFAULT_APPROVED_SOURCES_PATH,
) -> dict[str, Any]:
    """Assemble and validate a full audit_result. Raises AuditResultInvalid / GateError. Never writes."""
    required_partial = ("loan_id", "run_id", "audit_type")
    for key in required_partial:
        if not partial.get(key):
            raise GateError(f"partial audit result is missing '{key}'")
    audit_type = partial["audit_type"]
    if audit_type not in ("PREAPPROVAL", "SUBMISSION_READINESS"):
        raise GateError(f"unknown audit_type {audit_type!r}")

    los_export_present = partial.get("los_export_present")
    gate = compute_gate(
        findings,
        catalog_meta.get("items_in_phase") or [],
        los_export_present,
        catalog_reviewed=bool(catalog_meta.get("reviewed_by")),
    )

    generated_by = dict(partial.get("generated_by") or {})
    generated_by.setdefault("skill", partial.get("skill") or "unknown")
    generated_by.pop("run_id", None)  # audit_result.generated_by has no run_id (loan_file does)

    limitations = list(partial.get("known_limitations") or [])
    for line in default_known_limitations(
        catalog_meta=catalog_meta, los_export_present=los_export_present, approved_sources_path=approved_sources_path
    ):
        if line not in limitations:
            limitations.append(line)

    doc: dict[str, Any] = {
        "schema_version": "1.0",
        "loan_id": partial["loan_id"],
        "run_id": partial["run_id"],
        "audit_type": audit_type,
        "generated_at": partial.get("generated_at") or _now_iso(),
        "generated_by": generated_by,
        "inputs": _normalize_inputs(inputs),
        "catalog": {
            "path": str(catalog_meta["path"]),
            "sha256": catalog_meta["sha256"],
            "version": str(catalog_meta["version"]),
        },
        "overall_status": gate["overall_status"] if audit_type == "SUBMISSION_READINESS" else None,
        "findings": list(findings or []),
        "summary": gate["summary"],
        "missing_documents": list(partial.get("missing_documents") or []),
        "conflicts": list(partial.get("conflicts") or []),
        "approvals_required": list(partial.get("approvals_required") or []),
        "known_limitations": limitations,
    }
    for optional in ("proposed_los_corrections", "proposed_client_needs", "proposed_los_notes"):
        if partial.get(optional) is not None:
            doc[optional] = list(partial[optional])
    if los_export_present is not None:
        doc["los_export_present"] = bool(los_export_present)
    elif audit_type == "SUBMISSION_READINESS":
        doc["los_export_present"] = False  # unknown is treated as absent (fail closed)

    try:
        errors = validate_document(doc, "audit_result")
    except SchemaRegistryError as exc:
        raise AuditResultInvalid([f"schema registry error: {exc}"]) from exc
    if not errors:
        errors = integrity_errors(doc, "audit_result")
    if errors:
        raise AuditResultInvalid(errors)
    return doc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    """python scripts/audit/readiness_gate.py finalize --partial p.json --findings f.json --phase PREAPPROVAL|SUBMISSION
           [--catalog config/checklist_catalog.yaml] [--input <path> ...] --out audit_result.json

    Assembles, validates, and writes an audit_result. Exit 0 written, 1 invalid (nothing written), 2 could not run.
    """
    import argparse
    import json

    from scripts.audit.catalog import CatalogError, load_catalog

    ap = argparse.ArgumentParser(description=main.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("finalize")
    f.add_argument("--partial", required=True, type=Path, help="JSON with loan_id, run_id, audit_type, generated_by, los_export_present, missing_documents, conflicts, approvals_required, proposed_*")
    f.add_argument("--findings", required=True, type=Path, help="JSON array of finding objects")
    f.add_argument("--phase", required=True, choices=("PREAPPROVAL", "SUBMISSION"))
    f.add_argument("--catalog", type=Path, default=REPO_ROOT / "config" / "checklist_catalog.yaml")
    f.add_argument("--input", action="append", default=[], type=Path, help="input file consumed by the audit (hashed)")
    f.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        partial = json.loads(args.partial.read_text(encoding="utf-8"))
        findings = json.loads(args.findings.read_text(encoding="utf-8"))
        catalog = load_catalog(args.catalog)
    except (OSError, ValueError, CatalogError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not isinstance(findings, list):
        print("ERROR: --findings must be a JSON array", file=sys.stderr)
        return 2
    try:
        doc = finalize_audit_result(partial, findings, catalog.meta_for_phase(args.phase), args.input)
    except AuditResultInvalid as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    except (GateError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"WROTE: {args.out} overall_status={doc['overall_status']} coverage={doc['summary']['coverage']['coverage_percent']}% blocking_open={doc['summary']['blocking_open']}")
    for reason in compute_gate(findings, catalog.meta_for_phase(args.phase)["items_in_phase"], partial.get("los_export_present"), catalog_reviewed=catalog.is_reviewed)["reasons"]:
        print(f"  gate: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
