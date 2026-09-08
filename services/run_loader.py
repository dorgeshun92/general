"""Map one output/audits/<loan_id>/<run_id>/ directory into a RunBundle.

Everything is validated against the JSON Schemas (with integrity checks) and scanned for
unmasked PII before it becomes a bundle; a directory that fails either check raises
RunLoadError and nothing is returned. The loader never reads source documents, only the
derived JSON and Markdown the skills wrote.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from scripts.common.hashing import sha256_bytes
from scripts.common.masking import contains_unmasked_pii
from scripts.common.schema_registry import validate_document
from services.models import (
    ApprovalRequired,
    Conflict,
    Document,
    Finding,
    Loan,
    MissingDocument,
    ProposedAction,
    Report,
    ReviewItem,
    Run,
    RunBundle,
)

AUDIT_FILES = {"preapproval_audit.json": "PREAPPROVAL", "submission_readiness.json": "SUBMISSION_READINESS"}
MANIFEST_FILE = "run_manifest.json"
LOAN_FILE = "loan_file.json"
INVENTORY_FILE = "document_inventory.json"


class RunLoadError(ValueError):
    """The directory is incomplete, invalid, or contains unmasked PII."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunLoadError(f"cannot read {path}: {exc}") from exc


def _validated(path: Path, schema: str) -> dict[str, Any]:
    data = _read_json(path)
    errors = validate_document(data, schema)
    if errors:
        raise RunLoadError(f"{path.name} fails {schema} schema: " + "; ".join(errors[:5]))
    return data


def _pii_check(label: str, text: str, allowlist: tuple[str, ...] = ()) -> None:
    scrubbed = text
    for token in allowlist:
        scrubbed = scrubbed.replace(token, "")
    hits = contains_unmasked_pii(scrubbed)
    if hits:
        raise RunLoadError(f"{label} contains unmasked PII patterns: {', '.join(hits)}")


def bundle_from_documents(
    *,
    loan_id: str,
    run_id: str,
    loan_file: Optional[dict[str, Any]],
    inventory: Optional[dict[str, Any]],
    audits: list[dict[str, Any]],
    manifest: Optional[dict[str, Any]],
    reports: dict[str, str],
    loan_description: Optional[str] = None,
    source_root: Optional[str] = None,
) -> RunBundle:
    """Pure mapping from already-validated dicts to a RunBundle. Used by load_run_dir and the demo seed."""
    docs_src = (inventory or loan_file or {}).get("documents", [])
    documents = [
        Document(
            loan_id=loan_id, run_id=run_id, document_id=d["document_id"], filename=d["filename"],
            relative_path=d.get("relative_path"), sha256=d["sha256"], size_bytes=d.get("size_bytes"),
            document_type=d["document_type"], classification_confidence=d["classification_confidence"],
            page_count=d.get("page_count"), status=d["status"], duplicate_of=d.get("duplicate_of"),
            document_date=d.get("document_date"),
        )
        for d in docs_src
    ]
    review_items = [
        ReviewItem(loan_id=loan_id, run_id=run_id, review_id=r["review_id"], category=r["category"],
                   description=r["description"], document_ids=r.get("document_ids", []),
                   evidence_ids=r.get("evidence_ids", []), reviewer_role=r["reviewer_role"])
        for r in (loan_file or {}).get("review_items", [])
    ]

    findings: list[Finding] = []
    missing: list[MissingDocument] = []
    conflicts: list[Conflict] = []
    actions: list[ProposedAction] = []
    approvals: list[ApprovalRequired] = []
    gate_audit: Optional[dict[str, Any]] = None
    preapproval_present = submission_present = False
    for audit in audits:
        at = audit["audit_type"]
        preapproval_present |= at == "PREAPPROVAL"
        submission_present |= at == "SUBMISSION_READINESS"
        # The submission gate defines the run's status; fall back to preapproval counts when it is absent.
        if gate_audit is None or at == "SUBMISSION_READINESS":
            gate_audit = audit
        for f in audit.get("findings", []):
            findings.append(Finding(loan_id=loan_id, run_id=run_id, audit_type=at, **{k: f[k] for k in (
                "finding_id", "rule_id", "result", "blocking", "evidence_ids", "explanation", "discrepancy",
                "proposed_action", "reviewer_role", "confidence", "review_reason")}, calculation=f.get("calculation"),
                guideline_source=f.get("guideline_source")))
        for i, m in enumerate(audit.get("missing_documents", []), start=1):
            missing.append(MissingDocument(loan_id=loan_id, run_id=run_id, audit_type=at, seq=i, document_type=m["document_type"],
                                           borrower_id=m.get("borrower_id"), description=m["description"], rule_ids=m.get("rule_ids", [])))
        for c in audit.get("conflicts", []):
            conflicts.append(Conflict(loan_id=loan_id, run_id=run_id, audit_type=at, conflict_id=c["conflict_id"], field=c["field"],
                                      values=c["values"], explanation=c["explanation"], rule_ids=c.get("rule_ids", [])))
        for group in ("proposed_los_corrections", "proposed_client_needs", "proposed_los_notes"):
            for a in audit.get(group, []):
                actions.append(ProposedAction(loan_id=loan_id, run_id=run_id, audit_type=at, action_id=a["action_id"],
                                              action_type=a["action_type"], target=a["target"], description=a["description"],
                                              before_value=None if a.get("before_value") is None else str(a["before_value"]),
                                              after_value=None if a.get("after_value") is None else str(a["after_value"]),
                                              rule_ids=a.get("rule_ids", []), evidence_ids=a.get("evidence_ids", []),
                                              approver_role=a["approver_role"]))
        for i, ap in enumerate(audit.get("approvals_required", []), start=1):
            approvals.append(ApprovalRequired(loan_id=loan_id, run_id=run_id, audit_type=at, seq=i, description=ap["description"],
                                              approver_role=ap["approver_role"], rule_ids=ap.get("rule_ids", [])))

    summary = (gate_audit or {}).get("summary", {})
    known_limitations = list((gate_audit or {}).get("known_limitations", []))
    run = Run(
        loan_id=loan_id, run_id=run_id,
        skill=(manifest or {}).get("skill"),
        started_at=(manifest or {}).get("started_at"),
        completed_at=(manifest or {}).get("completed_at") or (gate_audit or {}).get("generated_at"),
        completed_normally=(manifest or {}).get("completed_normally"),
        stop_condition=(manifest or {}).get("stop_condition"),
        overall_status=(gate_audit or {}).get("overall_status") if submission_present else None,
        preapproval_present=preapproval_present, submission_present=submission_present,
        los_export_present=(gate_audit or {}).get("los_export_present"),
        catalog_version=(gate_audit or {}).get("catalog", {}).get("version"),
        catalog_reviewed=None,
        counts=summary.get("counts"),
        blocking_open=summary.get("blocking_open"),
        coverage_percent=summary.get("coverage", {}).get("coverage_percent"),
        known_limitations=known_limitations,
        tool_versions=(manifest or {}).get("tool_versions"),
        manifest=manifest,
        totals=(inventory or {}).get("totals"),
    )
    report_models = [Report(loan_id=loan_id, run_id=run_id, name=name, content_md=text, sha256=sha256_bytes(text.encode("utf-8")))
                     for name, text in sorted(reports.items())]
    loan = Loan(loan_id=loan_id, description=loan_description, deidentified=True, source_root=source_root)
    return RunBundle(loan=loan, run=run, documents=documents, findings=findings, review_items=review_items,
                     missing_documents=missing, conflicts=conflicts, proposed_actions=actions,
                     approvals_required=approvals, reports=report_models)


def load_run_dir(run_dir: str | Path, *, pii_allowlist: tuple[str, ...] = (), loan_description: Optional[str] = None) -> RunBundle:
    """Validate and load output/audits/<loan_id>/<run_id>/. Requires at least a manifest or one audit or loan_file."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise RunLoadError(f"{run_dir} is not a directory")
    loan_id, run_id = run_dir.parent.name, run_dir.name

    manifest = _read_json(run_dir / MANIFEST_FILE) if (run_dir / MANIFEST_FILE).is_file() else None
    if manifest and (manifest.get("loan_id") != loan_id or manifest.get("run_id") != run_id):
        raise RunLoadError(f"manifest ids {manifest.get('loan_id')}/{manifest.get('run_id')} do not match directory {loan_id}/{run_id}")
    loan_file = _validated(run_dir / LOAN_FILE, "loan_file") if (run_dir / LOAN_FILE).is_file() else None
    inventory = _validated(run_dir / INVENTORY_FILE, "document_inventory") if (run_dir / INVENTORY_FILE).is_file() else None
    audits = []
    for name in AUDIT_FILES:
        if (run_dir / name).is_file():
            audit = _validated(run_dir / name, "audit_result")
            if audit.get("loan_id") != loan_id or audit.get("run_id") != run_id:
                raise RunLoadError(f"{name} ids do not match directory {loan_id}/{run_id}")
            audits.append(audit)
    if manifest is None and loan_file is None and not audits:
        raise RunLoadError(f"{run_dir} has no run_manifest.json, loan_file.json, or audit files")

    reports: dict[str, str] = {}
    for md in sorted(run_dir.glob("*.md")):
        reports[md.name] = md.read_text(encoding="utf-8")

    # PII gate: every byte that would leave this machine is checked, hashes excluded.
    for label, payload in (("loan_file.json", loan_file), ("document_inventory.json", inventory), ("run_manifest.json", manifest)):
        if payload is not None:
            _pii_check(label, json.dumps(payload, ensure_ascii=False), pii_allowlist)
    for audit in audits:
        _pii_check(f"{audit['audit_type']} audit", json.dumps(audit, ensure_ascii=False), pii_allowlist)
    for name, text in reports.items():
        _pii_check(name, text, pii_allowlist)

    return bundle_from_documents(loan_id=loan_id, run_id=run_id, loan_file=loan_file, inventory=inventory, audits=audits,
                                 manifest=manifest, reports=reports, loan_description=loan_description,
                                 source_root=(loan_file or {}).get("source", {}).get("input_directory"))
