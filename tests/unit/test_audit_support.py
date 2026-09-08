"""Shared synthetic fixtures for the scripts.audit / scripts.drafts tests.

Everything here is obviously synthetic: rule ids are PRE-EXAMPLE-### / SUB-EXAMPLE-###,
explanations describe a pretend "example rule", and no real mortgage guideline text appears.
This module deliberately contains no test functions.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

FAKE_SHA = hashlib.sha256(b"synthetic-input").hexdigest()
CATALOG_SHA = hashlib.sha256(b"synthetic-catalog").hexdigest()
GENERATED_AT = "2026-09-08T12:00:00Z"


def finding(
    fid: str = "F-001",
    rule_id: str = "PRE-EXAMPLE-001",
    result: str = "PASS",
    *,
    blocking: bool = True,
    evidence: list[str] | None = None,
    explanation: str | None = None,
    proposed_action: str | None = None,
    reviewer_role: str | None = None,
    review_reason: str | None = None,
    discrepancy: str | None = None,
    calculation: dict[str, Any] | None = None,
    confidence: str = "HIGH",
) -> dict[str, Any]:
    if evidence is None:
        evidence = ["EV-001"] if result in ("PASS", "NOT_APPLICABLE") else []
    if proposed_action is None and result in ("FAIL", "MISSING"):
        proposed_action = f"Request the example document for synthetic rule {rule_id}."
    if result == "REVIEW":
        reviewer_role = reviewer_role or "UNDERWRITER"
        review_reason = review_reason or "Synthetic example rule needs a human decision."
    return {
        "finding_id": fid,
        "rule_id": rule_id,
        "result": result,
        "evidence_ids": evidence,
        "explanation": explanation or f"Synthetic example rule {rule_id} evaluated as {result}.",
        "discrepancy": discrepancy,
        "proposed_action": proposed_action,
        "blocking": blocking,
        "reviewer_role": reviewer_role,
        "confidence": confidence,
        "review_reason": review_reason,
        "calculation": calculation,
    }


def example_calculation() -> dict[str, Any]:
    return {
        "method": "salaried_monthly_base",
        "method_version": "1.0.0",
        "inputs": {"annual_salary": "60000.00"},
        "formula": "annual_salary / 12",
        "intermediate_values": {"months": "12"},
        "output": "5000.00",
        "warnings": ["synthetic example only"],
        "rounding_policy": "ROUND_HALF_UP to cents",
    }


def catalog_items(phase: str = "PREAPPROVAL", n: int = 3) -> list[dict[str, Any]]:
    prefix = "PRE" if phase == "PREAPPROVAL" else "SUB"
    return [{"id": f"{prefix}-EXAMPLE-{i:03d}", "phase": phase, "category": "OTHER", "blocking_if_failed": True} for i in range(1, n + 1)]


def catalog_meta(phase: str = "PREAPPROVAL", n: int = 3, reviewed_by: str | None = "Example Reviewer") -> dict[str, Any]:
    return {
        "path": "tests/synthetic/checklist_catalog.yaml",
        "sha256": CATALOG_SHA,
        "version": "9.9.9",
        "reviewed_by": reviewed_by,
        "reviewed_at": "2026-09-01" if reviewed_by else None,
        "is_empty": n == 0,
        "items_in_phase": catalog_items(phase, n),
    }


def summary_for(findings: list[dict[str, Any]], n_catalog: int) -> dict[str, Any]:
    counts = {k: 0 for k in ("PASS", "FAIL", "MISSING", "REVIEW", "NOT_APPLICABLE")}
    for f in findings:
        counts[f["result"]] += 1
    rule_ids = {f["rule_id"] for f in findings}
    evaluated = min(len(rule_ids), n_catalog)
    pct = "0.00" if n_catalog == 0 else f"{(evaluated * 100) // n_catalog}.00"
    return {
        "counts": counts,
        "blocking_open": sum(1 for f in findings if f["blocking"] and f["result"] not in ("PASS", "NOT_APPLICABLE")),
        "coverage": {"catalog_items_in_phase": n_catalog, "items_evaluated": evaluated, "coverage_percent": pct, "unevaluated_rule_ids": []},
    }


def make_audit(
    findings: list[dict[str, Any]] | None = None,
    *,
    audit_type: str = "SUBMISSION_READINESS",
    overall_status: str | None = "NOT_READY",
    loan_id: str = "LN-SYNTH-0001",
    run_id: str = "RUN-SYNTH-01",
    los_export_present: bool | None = False,
    n_catalog: int = 3,
    **extra: Any,
) -> dict[str, Any]:
    if findings is None:
        findings = [
            finding("F-001", "PRE-EXAMPLE-001", "FAIL"),
            finding("F-002", "PRE-EXAMPLE-002", "PASS"),
            finding("F-003", "PRE-EXAMPLE-003", "REVIEW", blocking=False),
        ]
    findings = copy.deepcopy(findings)
    doc: dict[str, Any] = {
        "schema_version": "1.0",
        "loan_id": loan_id,
        "run_id": run_id,
        "audit_type": audit_type,
        "generated_at": GENERATED_AT,
        "generated_by": {"skill": "synthetic-test"},
        "inputs": [{"path": "output/audits/synthetic/loan_file.json", "sha256": FAKE_SHA, "role": "loan_file"}],
        "catalog": {"path": "tests/synthetic/checklist_catalog.yaml", "sha256": CATALOG_SHA, "version": "9.9.9"},
        "overall_status": None if audit_type == "PREAPPROVAL" else overall_status,
        "findings": findings,
        "summary": summary_for(findings, n_catalog),
        "missing_documents": [],
        "conflicts": [],
        "approvals_required": [],
        "known_limitations": ["Synthetic test document; evaluates nothing real."],
    }
    if los_export_present is not None:
        doc["los_export_present"] = los_export_present
    doc.update(extra)
    return doc


def make_loan_file_evidence_only() -> dict[str, Any]:
    """Minimal structure render_report needs: documents + evidence (not a full valid loan_file)."""
    return {
        "documents": [{"document_id": "DOC-001", "filename": "example-paystub.pdf"}],
        "evidence": [
            {"evidence_id": "EV-001", "document_id": "DOC-001", "document_type": "PAYSTUB", "filename": "example-paystub.pdf",
             "page": 2, "document_date": "2026-08-15", "field": "income.INC-001.gross_pay_period", "extracted_value": "2500.00",
             "extraction_confidence": "HIGH", "extraction_method": "TEXT_LAYER"},
        ],
    }


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
