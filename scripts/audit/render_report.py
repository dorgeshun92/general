#!/usr/bin/env python3
"""Render a validated audit_result.json as a human-readable Markdown report.

    python scripts/audit/render_report.py <audit_result.json> --out <report.md> [--loan-file loan_file.json] [--catalog config/checklist_catalog.yaml]

Section order (fixed): header, KNOWN LIMITATIONS, blocking findings, non-blocking findings,
missing documents, conflicts, calculation summary, checklist coverage, approvals required,
items requiring licensed review, input files with sha256.

Fail-closed behaviour:
  - the audit_result is validated (schema + referential checks) BEFORE anything is rendered;
    an invalid document is never rendered (exit 1);
  - the rendered text is passed through mask_text and then checked with contains_unmasked_pii;
    if anything unmasked remains, nothing is written (exit 2);
  - unreadable inputs exit 2.
Nothing here decides anything: the report is labelled DECISION SUPPORT ONLY.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.audit.catalog import CatalogError, load_catalog  # noqa: E402
from scripts.common.masking import contains_unmasked_pii, mask_text  # noqa: E402
from scripts.common.schema_registry import SchemaRegistryError, validate_document  # noqa: E402
from scripts.validate_schema import integrity_errors  # noqa: E402

DECISION_LABEL = "DECISION SUPPORT ONLY — not a credit or compliance decision"
RESULT_ORDER = {"FAIL": 0, "MISSING": 1, "REVIEW": 2, "PASS": 3, "NOT_APPLICABLE": 4}
SECTION_TITLES = (
    "Known limitations",
    "Blocking findings",
    "Non-blocking findings",
    "Missing documents",
    "Conflicting facts",
    "Calculation summary",
    "Checklist coverage",
    "Approvals required",
    "Items requiring licensed review",
    "Input files",
)


class RenderError(RuntimeError):
    pass


class UnmaskedPIIError(RenderError):
    pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _cell(value: Any) -> str:
    if value is None:
        return "—"
    text = str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()
    return text or "—"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(_cell(c) for c in row) + " |")
    return out


def _evidence_index(loan_file: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not loan_file:
        return {}
    return {e.get("evidence_id"): e for e in loan_file.get("evidence", []) if e.get("evidence_id")}


def describe_evidence(evidence_ids: list[str], index: dict[str, dict[str, Any]]) -> str:
    if not evidence_ids:
        return "none cited"
    parts = []
    for ev_id in evidence_ids:
        ev = index.get(ev_id)
        if not ev:
            parts.append(ev_id if not index else f"{ev_id} (not found in loan_file)")
            continue
        page = f"p.{ev['page']}" if ev.get("page") is not None else "page unknown"
        value = ev.get("extracted_value")
        value_txt = f" = {value}" if value not in (None, "") else ""
        parts.append(f"{ev_id}: {ev.get('document_id')} {ev.get('filename')} {page} [{ev.get('field')}{value_txt}]")
    return "; ".join(parts)


def catalog_review_status(audit: dict[str, Any], catalog_path: Path | None) -> str:
    """Report the catalog's review status. Unknown is reported as unknown, never as reviewed."""
    cat_meta = audit.get("catalog", {})
    path = catalog_path or Path(cat_meta.get("path", ""))
    if not path.is_absolute():
        path = REPO_ROOT / path
    try:
        cat = load_catalog(path)
    except CatalogError:
        return "review status unknown (catalog file not loadable at the recorded path)"
    if cat.sha256 != cat_meta.get("sha256"):
        return "review status unknown (catalog on disk does not match the sha256 recorded in the audit)"
    label = cat.review_status_label()
    if cat.is_empty:
        label += "; catalog is EMPTY"
    return label


def _finding_rows(findings: list[dict[str, Any]], index: dict[str, dict[str, Any]]) -> list[list[Any]]:
    ordered = sorted(findings, key=lambda f: (RESULT_ORDER.get(f.get("result"), 9), f.get("finding_id", "")))
    rows = []
    for f in ordered:
        rows.append([
            f.get("finding_id"),
            f.get("rule_id"),
            f.get("result"),
            f.get("explanation"),
            describe_evidence(f.get("evidence_ids") or [], index),
            f.get("proposed_action"),
            f.get("reviewer_role"),
        ])
    return rows


def _calc_lines(findings: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for f in findings:
        calc = f.get("calculation")
        if not calc:
            continue
        lines.append(f"### {f.get('finding_id')} — {calc.get('method')} (v{calc.get('method_version')})")
        lines.append("")
        lines.append(f"- Formula: `{calc.get('formula')}`")
        lines.append("- Inputs:")
        for k, v in (calc.get("inputs") or {}).items():
            lines.append(f"  - {k}: `{json.dumps(v, default=str)}`")
        if calc.get("intermediate_values"):
            lines.append("- Intermediate values:")
            for k, v in calc["intermediate_values"].items():
                lines.append(f"  - {k}: `{json.dumps(v, default=str)}`")
        lines.append(f"- Output: `{json.dumps(calc.get('output'), default=str)}`")
        if calc.get("rounding_policy"):
            lines.append(f"- Rounding policy: {calc['rounding_policy']}")
        warnings = calc.get("warnings") or []
        lines.append("- Warnings: " + ("; ".join(warnings) if warnings else "none"))
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render_markdown(audit: dict[str, Any], loan_file: dict[str, Any] | None = None, catalog_path: Path | None = None) -> str:
    index = _evidence_index(loan_file)
    findings = audit.get("findings", [])
    blocking = [f for f in findings if f.get("blocking")]
    nonblocking = [f for f in findings if not f.get("blocking")]
    summary = audit.get("summary", {})
    coverage = summary.get("coverage", {})
    status = audit.get("overall_status")
    status_txt = status if status else "n/a (PREAPPROVAL audits carry no overall status)"
    headers = ["Finding", "Rule", "Result", "Explanation", "Evidence (document / page)", "Proposed action", "Reviewer"]

    L: list[str] = []
    L.append(f"# Audit report — {audit.get('loan_id')} / {audit.get('run_id')}")
    L.append("")
    L.append(f"**{DECISION_LABEL}**")
    L.append("")
    L.append(f"- Loan id: {audit.get('loan_id')}")
    L.append(f"- Run id: {audit.get('run_id')}")
    L.append(f"- Audit type: {audit.get('audit_type')}")
    L.append(f"- Generated at: {audit.get('generated_at')} by {audit.get('generated_by', {}).get('skill')}")
    L.append(f"- Catalog: version {audit.get('catalog', {}).get('version')} ({audit.get('catalog', {}).get('path')}), {catalog_review_status(audit, catalog_path)}")
    L.append(f"- Overall status: **{status_txt}**")
    if "los_export_present" in audit:
        L.append(f"- LOS export present: {'yes' if audit['los_export_present'] else 'NO — no claim is made about LOS completeness'}")
    L.append(f"- Blocking findings open: {summary.get('blocking_open')}")
    L.append("")

    L.append("## Known limitations")
    L.append("")
    L.append("> **KNOWN LIMITATIONS**")
    for line in audit.get("known_limitations", []):
        L.append(f"> - {line}")
    L.append("")

    L.append("## Blocking findings")
    L.append("")
    L.extend(_table(headers, _finding_rows(blocking, index)) if blocking else ["_No blocking findings recorded._"])
    L.append("")

    L.append("## Non-blocking findings")
    L.append("")
    L.extend(_table(headers, _finding_rows(nonblocking, index)) if nonblocking else ["_No non-blocking findings recorded._"])
    L.append("")

    L.append("## Missing documents")
    L.append("")
    missing = audit.get("missing_documents", [])
    if missing:
        L.extend(_table(["Document type", "Borrower", "Description", "Rules"], [[m.get("document_type"), m.get("borrower_id"), m.get("description"), ", ".join(m.get("rule_ids", []))] for m in missing]))
    else:
        L.append("_No missing documents recorded._")
    L.append("")

    L.append("## Conflicting facts")
    L.append("")
    conflicts = audit.get("conflicts", [])
    if conflicts:
        for c in conflicts:
            L.append(f"- **{c.get('conflict_id')}** `{c.get('field')}` — {c.get('explanation')}")
            for v in c.get("values", []):
                L.append(f"  - {v.get('source')}: `{v.get('value')}` ({describe_evidence(v.get('evidence_ids') or [], index)})")
            if c.get("rule_ids"):
                L.append(f"  - Rules: {', '.join(c['rule_ids'])}")
    else:
        L.append("_No conflicts recorded._")
    L.append("")

    L.append("## Calculation summary")
    L.append("")
    calc_lines = _calc_lines(findings)
    L.extend(calc_lines if calc_lines else ["_No deterministic calculations were attached to findings._", ""])

    L.append("## Checklist coverage")
    L.append("")
    L.append(f"- Catalog items in phase: {coverage.get('catalog_items_in_phase')}")
    L.append(f"- Items evaluated: {coverage.get('items_evaluated')}")
    L.append(f"- Coverage: {coverage.get('coverage_percent')}%")
    unevaluated = coverage.get("unevaluated_rule_ids") or []
    L.append(f"- Unevaluated rule ids: {', '.join(unevaluated) if unevaluated else 'none'}")
    counts = summary.get("counts", {})
    L.append("- Result counts: " + ", ".join(f"{k} {counts.get(k, 0)}" for k in ("PASS", "FAIL", "MISSING", "REVIEW", "NOT_APPLICABLE")))
    L.append("")

    L.append("## Approvals required")
    L.append("")
    approvals = audit.get("approvals_required", [])
    if approvals:
        L.extend(_table(["Description", "Approver role", "Rules"], [[a.get("description"), a.get("approver_role"), ", ".join(a.get("rule_ids", []))] for a in approvals]))
    else:
        L.append("_No approvals recorded._")
    proposals = []
    for key, label in (("proposed_los_corrections", "LOS correction"), ("proposed_client_needs", "Client need"), ("proposed_los_notes", "LOS note")):
        for pa in audit.get(key, []) or []:
            proposals.append([pa.get("action_id"), label, pa.get("target"), pa.get("description"), pa.get("approver_role"), pa.get("status")])
    if proposals:
        L.append("")
        L.append("Proposed actions (all DRAFT — nothing has been executed):")
        L.append("")
        L.extend(_table(["Action", "Type", "Target", "Description", "Approver", "Status"], proposals))
    L.append("")

    L.append("## Items requiring licensed review")
    L.append("")
    review_items = [f for f in findings if f.get("result") == "REVIEW"]
    if review_items:
        L.extend(_table(["Finding", "Rule", "Reviewer", "Reason", "Blocking"], [[f.get("finding_id"), f.get("rule_id"), f.get("reviewer_role"), f.get("review_reason"), "yes" if f.get("blocking") else "no"] for f in review_items]))
    else:
        L.append("_No REVIEW findings._")
    L.append("")

    L.append("## Input files")
    L.append("")
    input_rows = [[i.get("path"), i.get("role"), i.get("sha256")] for i in audit.get("inputs", [])]
    input_rows.append([audit.get("catalog", {}).get("path"), "checklist catalog", audit.get("catalog", {}).get("sha256")])
    L.extend(_table(["Path", "Role", "SHA-256"], input_rows))
    L.append("")
    L.append(f"_{DECISION_LABEL}_")
    L.append("")
    return "\n".join(L)


def validate_audit(audit: Any) -> list[str]:
    errors = validate_document(audit, "audit_result")
    if not errors:
        errors = integrity_errors(audit, "audit_result")
    return errors


def render_to_file(audit_path: Path, out_path: Path, loan_file_path: Path | None = None, catalog_path: Path | None = None) -> str:
    """Validate, render, mask, verify, write. Raises on any failure; writes nothing on failure."""
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        loan_file = json.loads(loan_file_path.read_text(encoding="utf-8")) if loan_file_path else None
    except (OSError, json.JSONDecodeError) as exc:
        raise RenderError(f"cannot read input: {exc}") from exc
    try:
        errors = validate_audit(audit)
    except SchemaRegistryError as exc:
        raise RenderError(f"cannot validate audit_result: {exc}") from exc
    if errors:
        raise ValueError(f"audit_result {audit_path} is invalid ({len(errors)} error(s)): " + "; ".join(errors))
    text = mask_text(render_markdown(audit, loan_file, catalog_path))
    hits = contains_unmasked_pii(text)
    if hits:
        raise UnmaskedPIIError("rendered report still contains unmasked PII after masking: " + ", ".join(hits))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audit_result", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--loan-file", type=Path, default=None, help="loan_file.json used to resolve evidence ids to document/page")
    ap.add_argument("--catalog", type=Path, default=None, help="catalog path used to report reviewed status (default: path recorded in the audit)")
    args = ap.parse_args(argv)
    try:
        render_to_file(args.audit_result, args.out, args.loan_file, args.catalog)
    except ValueError as exc:  # invalid audit_result
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    except (RenderError, OSError) as exc:  # includes UnmaskedPIIError
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"WROTE: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
