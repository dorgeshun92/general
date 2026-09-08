#!/usr/bin/env python3
"""Build borrower-communication DRAFTS from approved findings in an audit_result.json.

    python scripts/drafts/build_drafts.py <audit_result.json> --approved F-001,F-004 [--approver "Name"] [--approved-on YYYY-MM-DD] [--out output/drafts]
    python scripts/drafts/build_drafts.py <audit_result.json> --approved-file approved_findings.txt [--out output/drafts]
    python scripts/drafts/build_drafts.py --check <draft.md> [--check <draft.md> ...]

--approved-file format (one finding id per line, '#' comments allowed, headers required):
    approver: Jane Example (Processor)
    date: 2026-09-01
    F-001
    F-004

Outputs (all DRAFTS, written to output/drafts/<loan_id>/<run_id>/):
    document_request_email.md, borrower_call_agenda.md, post_call_los_note.md, agent_title_follow_up.md
agent_title_follow_up.md holds a one-line "not applicable" notice unless an approved finding's
proposed_action mentions the agent, title, contract, or escrow.

Refusals / exit codes:
    0  drafts written
    1  refused: unknown finding id, finding not FAIL/MISSING/REVIEW (nothing to request), empty
       approval list, or approved-file without approver/date headers; --check found a violation
    2  fail closed: audit_result invalid, banned phrase produced in external text, or unmasked PII
Nothing is written unless every draft passes every check. Nothing is ever sent.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.common.masking import contains_unmasked_pii, mask_text  # noqa: E402
from scripts.common.schema_registry import SchemaRegistryError, validate_document  # noqa: E402
from scripts.validate_schema import integrity_errors  # noqa: E402

DRAFT_LABEL = "DRAFT — HUMAN APPROVAL REQUIRED"
REQUESTABLE_RESULTS = ("FAIL", "MISSING", "REVIEW")
BANNED_PHRASES = ("approved", "guaranteed", "will close", "pre-approved", "preapproved")
BANNED_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(p) for p in BANNED_PHRASES) + r")(?![\w-])", re.IGNORECASE)
AGENT_TITLE_RE = re.compile(r"\b(agent|realtor|title|contract|escrow|closing attorney|settlement)\b", re.IGNORECASE)
EXTERNAL_FILES = ("document_request_email.md", "borrower_call_agenda.md", "agent_title_follow_up.md")
ALL_FILES = EXTERNAL_FILES[:2] + ("post_call_los_note.md", "agent_title_follow_up.md")
DEFAULT_OUT_ROOT = REPO_ROOT / "output" / "drafts"

CATEGORY_LABELS = {
    "CREDIT": "Credit",
    "LIABILITIES": "Debts and monthly payments",
    "REO": "Other real estate you own",
    "EMPLOYMENT": "Employment",
    "INCOME_REGULAR": "Income (salary or hourly)",
    "INCOME_VARIABLE": "Income (variable: bonus, overtime, commission)",
    "INCOME_SELF_EMPLOYED": "Self-employment income",
    "INCOME_OTHER": "Other income",
    "ASSETS": "Bank and asset statements",
    "GIFT_FUNDS": "Gift funds",
    "RETIREMENT_FUNDS": "Retirement accounts",
    "TITLE": "Title",
    "PROPERTY": "Property",
    "CONTRACT": "Purchase contract",
    "IDENTITY": "Identification",
    "APPLICATION_1003": "Loan application",
    "DISCLOSURES": "Disclosures",
    "LOS": "Loan file records",
    "AUS": "Automated underwriting",
    "OTHER": "Other items",
}


class DraftRefused(ValueError):
    """Input refused (exit 1)."""


class DraftSafetyError(RuntimeError):
    """Output failed a safety check; nothing written (exit 2)."""


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

def banned_phrase_hits(text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in BANNED_RE.finditer(text)})


def check_draft_text(text: str, *, external: bool) -> list[str]:
    """Return problems with a draft's text. Empty list = acceptable."""
    problems = []
    if not text.startswith(DRAFT_LABEL):
        problems.append(f"first line must be '{DRAFT_LABEL}'")
    if external:
        hits = banned_phrase_hits(text)
        if hits:
            problems.append("banned phrase(s) in borrower/agent-facing text: " + ", ".join(hits))
    pii = contains_unmasked_pii(text)
    if pii:
        problems.append("unmasked PII: " + ", ".join(pii))
    return problems


def category_from_rule_id(rule_id: str) -> str:
    parts = (rule_id or "").split("-")
    if len(parts) < 3:
        return "OTHER"
    return "_".join(parts[1:-1]).upper()


def category_label(rule_id: str) -> str:
    key = category_from_rule_id(rule_id)
    return CATEGORY_LABELS.get(key, key.replace("_", " ").title() or "Other items")


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------

def parse_approved_file(path: Path) -> tuple[list[str], str, str]:
    approver = date = None
    ids: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith("approver:"):
            approver = line.split(":", 1)[1].strip()
        elif low.startswith("date:"):
            date = line.split(":", 1)[1].strip()
        else:
            ids.extend(p.strip() for p in line.split(",") if p.strip())
    if not approver or not date:
        raise DraftRefused(f"{path}: approved-file must start with 'approver: <name>' and 'date: <YYYY-MM-DD>' headers")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise DraftRefused(f"{path}: date header must be ISO 8601 (YYYY-MM-DD), got {date!r}")
    return ids, approver, date


def select_findings(audit: dict[str, Any], approved_ids: list[str]) -> list[dict[str, Any]]:
    if not approved_ids:
        raise DraftRefused("no approved finding ids were supplied; drafts are built only from approved findings")
    by_id = {f["finding_id"]: f for f in audit.get("findings", [])}
    seen: list[str] = []
    for fid in approved_ids:
        if fid not in seen:
            seen.append(fid)
    unknown = [fid for fid in seen if fid not in by_id]
    if unknown:
        raise DraftRefused("finding id(s) not present in the audit (not approved): " + ", ".join(unknown))
    wrong = [f"{fid} ({by_id[fid]['result']})" for fid in seen if by_id[fid]["result"] not in REQUESTABLE_RESULTS]
    if wrong:
        raise DraftRefused("only FAIL, MISSING, or REVIEW findings can generate a request; refused: " + ", ".join(wrong))
    return [by_id[fid] for fid in seen]


# ---------------------------------------------------------------------------
# drafting
# ---------------------------------------------------------------------------

def _need_text(f: dict[str, Any]) -> str:
    action = (f.get("proposed_action") or "").strip()
    if action:
        return action
    reason = (f.get("review_reason") or "").strip()
    return reason or "Additional documentation or clarification is needed for this item."


def _grouped(findings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        groups.setdefault(category_label(f.get("rule_id", "")), []).append(f)
    return groups


def _internal_block(audit: dict[str, Any], findings: list[dict[str, Any]], approver: str, date: str) -> list[str]:
    ids = ", ".join(f["finding_id"] for f in findings)
    return [
        f"<!-- Internal, remove before sending: loan {audit['loan_id']} run {audit['run_id']}; "
        f"built from findings {ids}; sign-off recorded from {approver} on {date}. -->",
    ]


def build_document_request_email(audit: dict[str, Any], findings: list[dict[str, Any]], approver: str, date: str) -> str:
    L = [DRAFT_LABEL, ""]
    L += _internal_block(audit, findings, approver, date)
    L += ["", "Subject: A few items we still need for your loan file", "", "Hello,", "",
          "Thank you for the documents you have already sent. To keep your file moving, we still need the items below. "
          "Each one is listed with a short note on why it is needed.", ""]
    for label, items in _grouped(findings).items():
        L.append(f"**{label}**")
        for f in items:
            L.append(f"- {_need_text(f)}")
            L.append(f"  - Why we need it: {f.get('explanation')}")
        L.append("")
    L += ["If any item is unavailable or does not apply to you, please reply and let us know so we can discuss alternatives.", "",
          "You can send items by replying to this message or through the secure upload link your loan team provided. Please do not send full account or Social Security numbers in plain email.", "",
          "Thank you,", "", "[Loan team name]", "[Phone]", "",
          "_This message is a request for information only. It is not a commitment to lend, a loan decision, or a statement about the outcome of your application._", ""]
    return "\n".join(L)


def build_call_agenda(audit: dict[str, Any], findings: list[dict[str, Any]], approver: str, date: str) -> str:
    L = [DRAFT_LABEL, ""]
    L += _internal_block(audit, findings, approver, date)
    L += ["", f"# Borrower call agenda — loan {audit['loan_id']}", "",
          "Purpose: walk through the outstanding items, confirm what the borrower can provide, and agree on timing. "
          "Do not characterize the likelihood of a loan decision on this call.", "",
          "1. Open: thank the borrower; confirm this is a good time; confirm preferred contact method.",
          "2. Outstanding items (grouped):"]
    n = 0
    for label, items in _grouped(findings).items():
        L.append(f"   - {label}")
        for f in items:
            n += 1
            L.append(f"     {n}. Ask: {_need_text(f)}")
            L.append(f"        Why: {f.get('explanation')}")
            if f.get("discrepancy"):
                L.append(f"        Clarify: {f['discrepancy']}")
    L += ["3. For each item: can the borrower provide it? By when? Any reason it may not exist or apply?",
          "4. Confirm the secure way to send documents; remind the borrower not to email full account or Social Security numbers.",
          "5. Close: summarize what the borrower agreed to send and by when; set the next check-in.", "",
          "Do not say on this call: anything about the loan being certain, any promise about timing of a decision, or internal review notes.", ""]
    return "\n".join(L)


def build_los_note(audit: dict[str, Any], findings: list[dict[str, Any]], approver: str, date: str) -> str:
    L = [DRAFT_LABEL, "",
         f"# Post-call LOS note (proposed) — loan {audit['loan_id']}, run {audit['run_id']}", "",
         "Status: DRAFT_HUMAN_APPROVAL_REQUIRED. This note has not been entered in any LOS. "
         "Fill in the bracketed call details after the call, then a processor enters it manually.", "",
         f"Basis: findings {', '.join(f['finding_id'] for f in findings)} signed off by {approver} on {date}.", "",
         "Call date/time: [fill in]", "Spoke with: [borrower / co-borrower]", "", "Items requested:"]
    for f in findings:
        L.append(f"- {f['finding_id']} / {f.get('rule_id')} ({f.get('result')}): {_need_text(f)}")
        L.append("  - Borrower response: [fill in]   Expected by: [date]")
    L += ["", "Follow-up owner: [name]", "Next check-in: [date]", ""]
    return "\n".join(L)


def agent_title_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in findings if AGENT_TITLE_RE.search(f.get("proposed_action") or "")]


def build_agent_title_follow_up(audit: dict[str, Any], findings: list[dict[str, Any]], approver: str, date: str) -> str:
    relevant = agent_title_findings(findings)
    if not relevant:
        return DRAFT_LABEL + " — Not applicable: none of the selected findings has a proposed action that involves the agent, title, contract, or escrow.\n"
    L = [DRAFT_LABEL, ""]
    L += _internal_block(audit, relevant, approver, date)
    L += ["", "Subject: Follow-up items for the transaction", "", "Hello,", "",
          "We are working through the file for our mutual client and need your help with the following:", ""]
    for f in relevant:
        L.append(f"- {_need_text(f)}")
        L.append(f"  - Why: {f.get('explanation')}")
    L += ["", "Please send anything you have or let us know who the right contact is. Thank you for your help.", "",
          "[Loan team name]", "[Phone]", ""]
    return "\n".join(L)


def build_all(audit: dict[str, Any], findings: list[dict[str, Any]], approver: str, date: str) -> dict[str, str]:
    texts = {
        "document_request_email.md": build_document_request_email(audit, findings, approver, date),
        "borrower_call_agenda.md": build_call_agenda(audit, findings, approver, date),
        "post_call_los_note.md": build_los_note(audit, findings, approver, date),
        "agent_title_follow_up.md": build_agent_title_follow_up(audit, findings, approver, date),
    }
    checked: dict[str, str] = {}
    problems: list[str] = []
    for name, text in texts.items():
        masked = mask_text(text)
        for p in check_draft_text(masked, external=name in EXTERNAL_FILES):
            problems.append(f"{name}: {p}")
        checked[name] = masked
    if problems:
        raise DraftSafetyError("drafts failed safety checks; nothing written: " + " | ".join(problems))
    return checked


def load_audit(path: Path) -> dict[str, Any]:
    try:
        audit = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DraftSafetyError(f"cannot read audit_result {path}: {exc}") from exc
    try:
        errors = validate_document(audit, "audit_result")
    except SchemaRegistryError as exc:
        raise DraftSafetyError(f"cannot validate audit_result: {exc}") from exc
    if not errors:
        errors = integrity_errors(audit, "audit_result")
    if errors:
        raise DraftSafetyError(f"audit_result {path} is invalid ({len(errors)} error(s)): " + "; ".join(errors))
    return audit


def write_drafts(audit_path: Path, approved_ids: list[str], approver: str, date: str, out_root: Path = DEFAULT_OUT_ROOT) -> list[Path]:
    audit = load_audit(audit_path)
    findings = select_findings(audit, approved_ids)
    texts = build_all(audit, findings, approver, date)
    out_dir = out_root / audit["loan_id"] / audit["run_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in ALL_FILES:
        p = out_dir / name
        p.write_text(texts[name], encoding="utf-8")
        written.append(p)
    return written


def check_files(paths: list[Path]) -> list[str]:
    problems = []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"{p}: cannot read ({exc})")
            continue
        for issue in check_draft_text(text, external=p.name in EXTERNAL_FILES or p.name not in ALL_FILES):
            problems.append(f"{p}: {issue}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audit_result", nargs="?", type=Path)
    ap.add_argument("--approved", help="comma-separated approved finding ids, e.g. F-001,F-004")
    ap.add_argument("--approved-file", type=Path, help="file with approver/date headers and one finding id per line")
    ap.add_argument("--approver", default=None, help="who approved the finding ids (used with --approved)")
    ap.add_argument("--approved-on", default=None, help="ISO date of the approval (used with --approved)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT_ROOT, help="drafts root (default output/drafts)")
    ap.add_argument("--check", action="append", type=Path, default=[], help="re-check an existing draft file instead of building")
    args = ap.parse_args(argv)

    if args.check:
        problems = check_files(args.check)
        if problems:
            print("CHECK FAILED:")
            for p in problems:
                print(f"  - {p}")
            return 1
        print(f"CHECK OK: {len(args.check)} draft file(s) carry the DRAFT label, no banned phrases, no unmasked PII")
        return 0

    if not args.audit_result:
        ap.error("audit_result is required unless --check is used")
    if bool(args.approved) == bool(args.approved_file):
        ap.error("supply exactly one of --approved or --approved-file")
    try:
        if args.approved_file:
            ids, approver, date = parse_approved_file(args.approved_file)
        else:
            ids = [p.strip() for p in args.approved.split(",") if p.strip()]
            approver = args.approver or "[approver not recorded on command line]"
            date = args.approved_on or "[date not recorded]"
        written = write_drafts(args.audit_result, ids, approver, date, args.out)
    except DraftRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    except (DraftSafetyError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for p in written:
        print(f"WROTE: {p}")
    print(f"{DRAFT_LABEL}: nothing has been sent. A human must review and approve each file before use.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
