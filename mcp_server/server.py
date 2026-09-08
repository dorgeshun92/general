"""mpire-audit MCP server (mcp>=2.0, `mcp.server.mcpserver.MCPServer`).

Exposes the derived, masked audit data behind ``services.repository.Repository`` to Claude Code
and other MCP clients. It is the MCP twin of the REST contract in ``docs/api-contract.md``:
tool names parallel the routes there (``mortgage_list_findings`` <-> ``GET .../findings``).

Capabilities, deliberately narrow:
- Read tools over loans, runs, findings, documents, reports, queues, and evaluation results.
- Three append-only write tools that only touch this system's own datastore: a QUEUED run
  request and two "record what a named human decided" tools. None of them has an external
  effect; nothing runs automatically.
- No tool sends email/SMS, writes to an LOS, runs AUS, triggers TRID, prices, picks a lender,
  submits a loan, reads a source document, or starts an audit run.

Errors reach the model as ``is_error`` results with a plain message (never a traceback,
never a credential). Money stays a decimal string and identifiers stay masked because the
repository models already enforce that; report text is re-checked for unmasked PII before it
is returned.
"""

import argparse
import inspect
import json
import logging
import os
import sys
from typing import Annotated, Any, Callable, Optional, TypeVar

from pydantic import Field

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, ToolAnnotations

from scripts.common.masking import contains_unmasked_pii
from services.config import Settings, get_settings
from services.models import (
    ActionDecision,
    ActionDecisionKind,
    AuditType,
    Finding,
    ProposedAction,
    ResultValue,
    ReviewDecision,
    ReviewDecisionKind,
    ReviewerRole,
    Run,
    RunRequestStatus,
)
from services.repository import Conflicted, InMemoryRepository, NotFound, Repository

SERVER_NAME = "mpire-audit"
SERVER_VERSION = "1.0.0"
DEFAULT_IDENTITY = "mcp-server"
IDENTITY_ENV = "MPIRE_MCP_IDENTITY"
BEARER_ENV = "MPIRE_MCP_BEARER"

REPORT_CHAR_CAP = 60_000          # mortgage_get_report truncates content_md beyond this
MAX_LIST_LIMIT = 500              # hard ceiling for every `limit` parameter
MAX_SEARCH_LIMIT = 200
MAX_RUNS_LIMIT = 200

REVIEWER_ROLES: tuple[str, ...] = ("LOAN_OFFICER", "PROCESSOR", "UNDERWRITER", "COMPLIANCE", "MANAGEMENT")

SERVER_INSTRUCTIONS = f"""\
{SERVER_NAME}: read access to the Mpire mortgage operations copilot's derived audit data
(loans, audit runs, findings, documents, review queue, proposed actions, Markdown reports,
evaluation results). Every value is decision support for licensed mortgage personnel, not a
credit or compliance decision. Money is a decimal string; dates are ISO 8601; SSNs and account
numbers are masked. Results use only PASS, FAIL, MISSING, REVIEW, NOT_APPLICABLE; the overall
gate uses only READY, NOT_READY, HUMAN_REVIEW.

Rules for using these tools:
- Never invent a borrower fact, document, guideline, or finding. Cite finding ids, rule ids,
  document ids and evidence ids exactly as returned. If a value is null or a list is empty,
  say so; do not fill the gap.
- Start with mortgage_summary or mortgage_list_loans, then mortgage_get_run, then the
  per-run list tools. Use `limit` to keep responses small; mortgage_get_finding returns the
  full calculation trail for one finding.
- The three write tools (mortgage_create_run_request, mortgage_record_review_decision,
  mortgage_record_action_decision) append rows to this system's own database and have no
  external effect. Call the two "record" tools only when a named human has explicitly stated
  the decision; never decide on their behalf. A run request only queues a row: a human still
  runs /mortgage-file-audit in Claude Code.

This server has NO tool that sends email or SMS, writes to an LOS (Arive, LendingPad, or any
other), runs AUS, triggers TRID disclosures, prices a loan, locks a rate, selects a lender,
submits a loan, reads a source document (PDF, statement, credit report), or starts an audit
run. If asked to do any of those, say that it is not possible from this server and that an
authorized human must act.
"""

_T = TypeVar("_T")

# ---- tool annotations -------------------------------------------------------------------------

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
APPEND_ONLY = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)

# ---- parameter types (shared descriptions so every tool's schema reads the same) --------------

LoanId = Annotated[str, Field(description="Loan identifier, e.g. 'LN-EXAMPLE-0002'.", min_length=1, max_length=128)]
RunId = Annotated[str, Field(description="Run identifier within the loan, e.g. 'RUN-DEMO-0002'.", min_length=1, max_length=128)]
OptLoanId = Annotated[Optional[str], Field(description="Optional loan identifier filter.", max_length=128)]
OptAuditType = Annotated[Optional[AuditType], Field(description="Filter by audit type: PREAPPROVAL or SUBMISSION_READINESS.")]
OptResult = Annotated[Optional[ResultValue], Field(description="Filter by result: PASS, FAIL, MISSING, REVIEW, NOT_APPLICABLE.")]
OptBlocking = Annotated[Optional[bool], Field(description="Filter by blocking flag (true = blocks submission).")]
OptRuleId = Annotated[Optional[str], Field(description="Filter by checklist rule id, e.g. 'PRE-CREDIT-001'.", max_length=64)]
OptReviewerRole = Annotated[Optional[ReviewerRole], Field(description="Filter by reviewer role.")]
OptNote = Annotated[Optional[str], Field(description="Free-text note (no SSNs or account numbers).", max_length=2000)]
ReportName = Annotated[str, Field(description="Report file name, e.g. 'report.md'.", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]


def _limit(default: int, maximum: int, what: str):
    return Annotated[int, Field(ge=1, le=maximum, description=f"Maximum {what} to return (1-{maximum}).")]


# ---- helpers ----------------------------------------------------------------------------------


def _clean_doc(doc: Optional[str]) -> str:
    """Docstring -> single-paragraph tool description (the SDK keeps raw indentation otherwise)."""
    return " ".join(inspect.cleandoc(doc or "").split())



def _dump(model: Any, *, full: bool = False) -> dict[str, Any]:
    """JSON-safe dict from a pydantic model. Compact rows drop nulls; `full` keeps every field."""
    return model.model_dump(mode="json", exclude_none=not full)


def _page(rows: list[Any], limit: int, key: str, *, full: bool = False, transform: Callable[[Any], dict[str, Any]] | None = None) -> dict[str, Any]:
    page = rows[:limit]
    items = [transform(r) if transform else _dump(r, full=full) for r in page]
    return {"count": len(items), "total": len(rows), "limit": limit, "truncated": len(rows) > len(items), key: items}


def _run_summary(run: Run) -> dict[str, Any]:
    """The small view of a run used in lists; mortgage_get_run returns the full row."""
    keys = ("loan_id", "run_id", "overall_status", "completed_at", "completed_normally", "stop_condition",
            "preapproval_present", "submission_present", "blocking_open", "coverage_percent", "counts", "synced_at")
    return {k: getattr(run, k) for k in keys}


def _finding_row(f: Finding) -> dict[str, Any]:
    """List view of a finding: everything except the calculation trail and guideline body."""
    row = _dump(f)
    row.pop("calculation", None)
    row.pop("guideline_source", None)
    row["has_calculation"] = f.calculation is not None
    row["has_guideline_source"] = f.guideline_source is not None
    return row


def _guard(fn: Callable[[], _T]) -> _T:
    """Run a repository call and convert failures into short, secret-free ToolErrors."""
    try:
        return fn()
    except ToolError:
        raise
    except NotFound as exc:
        raise ToolError(f"{exc}. Use mortgage_list_loans or mortgage_list_runs to find valid ids.") from None
    except Conflicted as exc:
        raise ToolError(f"{exc}.") from None
    except ValueError as exc:
        raise ToolError(f"invalid request: {exc}") from None
    except Exception as exc:  # backend / transport failures: name the class, never the payload or a traceback
        detail = getattr(exc, "status", None)
        suffix = f" (status {detail})" if detail is not None else ""
        raise ToolError(f"backend error {type(exc).__name__}{suffix}; the request was not completed") from None


def _resolve_identity(identity: Optional[str]) -> str:
    return (identity or os.environ.get(IDENTITY_ENV) or DEFAULT_IDENTITY).strip() or DEFAULT_IDENTITY


def _default_repository(settings: Optional[Settings]) -> Repository:
    """memory -> seeded InMemoryRepository; supabase -> services.factory (service key or MPIRE_MCP_BEARER)."""
    settings = settings or get_settings()
    if settings.backend == "memory":
        from services.demo import seed_demo

        repo = InMemoryRepository()
        seed_demo(repo)
        return repo
    from services.factory import build_repository

    bearer = os.environ.get(BEARER_ENV) or None
    return build_repository(settings, bearer=bearer)


# ---- server -----------------------------------------------------------------------------------


def build_server(repository: Optional[Repository] = None, settings: Optional[Settings] = None, *,
                 identity: Optional[str] = None) -> MCPServer:
    """Build the MCP server over `repository` (or the backend named by settings / MPIRE_REPO_BACKEND).

    `identity` (default: env MPIRE_MCP_IDENTITY, then "mcp-server") is stamped on every row the
    write tools create as `requested_by` / `decided_by`.
    """
    repo: Repository = repository if repository is not None else _default_repository(settings)
    who = _resolve_identity(identity)
    server = MCPServer(name=SERVER_NAME, title="Mpire mortgage audit data", version=SERVER_VERSION,
                       instructions=SERVER_INSTRUCTIONS, log_level="WARNING")

    def _tool(name: str, annotations: ToolAnnotations):
        """server.tool with the docstring normalized into `description`."""
        def deco(fn):
            return server.tool(name=name, description=_clean_doc(fn.__doc__), annotations=annotations)(fn)
        return deco

    # ---- read tools ---------------------------------------------------------------------------

    @_tool(name="mortgage_summary", annotations=READ_ONLY)
    def mortgage_summary() -> dict[str, Any]:
        """Dashboard totals: loans, runs, latest-run gate counts (READY / NOT_READY / HUMAN_REVIEW / no gate),
        open blocking findings, review-queue size, queued run requests, and proposed actions awaiting a decision.
        Start here. Read-only."""
        summary = _guard(repo.dashboard_summary)
        return {**_dump(summary, full=True), "server": SERVER_NAME}

    @_tool(name="mortgage_list_loans", annotations=READ_ONLY)
    def mortgage_list_loans() -> dict[str, Any]:
        """List every loan with its latest run's status (overall_status, blocking_open, coverage_percent, counts).
        `latest_run` is null when a loan has no synced run. Read-only."""
        def _go() -> dict[str, Any]:
            loans = repo.list_loans()
            latest = {r.loan_id: r for r in repo.latest_runs()}
            rows = [{**_dump(l), "latest_run": (_run_summary(latest[l.loan_id]) if l.loan_id in latest else None)} for l in loans]
            return {"count": len(rows), "loans": rows}
        return _guard(_go)

    @_tool(name="mortgage_list_runs", annotations=READ_ONLY)
    def mortgage_list_runs(loan_id: OptLoanId = None, limit: _limit(20, MAX_RUNS_LIMIT, "runs") = 20) -> dict[str, Any]:
        """List audit runs newest first, optionally for one loan. Each row is a run summary; use mortgage_get_run for
        the full row, counts and known limitations. Read-only."""
        def _go() -> dict[str, Any]:
            if loan_id is not None:
                repo.get_loan(loan_id)
            runs = repo.list_runs(loan_id=loan_id, limit=limit)
            return {"count": len(runs), "limit": limit, "loan_id": loan_id, "runs": [_run_summary(r) for r in runs]}
        return _guard(_go)

    @_tool(name="mortgage_get_run", annotations=READ_ONLY)
    def mortgage_get_run(loan_id: LoanId, run_id: RunId) -> dict[str, Any]:
        """One audit run: full run row (status, counts, coverage, known_limitations, catalog version, tool versions),
        child-row counts, decision counts, and the names of its Markdown reports (not their text; use
        mortgage_get_report). Read-only."""
        def _go() -> dict[str, Any]:
            detail = repo.get_run_detail(loan_id, run_id)
            b = detail.bundle
            return {
                **_dump(b.run, full=True),
                "loan_description": b.loan.description,
                "child_counts": {
                    "documents": len(b.documents), "findings": len(b.findings), "review_items": len(b.review_items),
                    "missing_documents": len(b.missing_documents), "conflicts": len(b.conflicts),
                    "proposed_actions": len(b.proposed_actions), "approvals_required": len(b.approvals_required),
                },
                "decision_counts": {"review": len(detail.review_decisions), "action": len(detail.action_decisions)},
                "report_names": [r.name for r in b.reports],
            }
        return _guard(_go)

    @_tool(name="mortgage_list_findings", annotations=READ_ONLY)
    def mortgage_list_findings(loan_id: LoanId, run_id: RunId, audit_type: OptAuditType = None, result: OptResult = None,
                               blocking: OptBlocking = None, rule_id: OptRuleId = None,
                               limit: _limit(100, MAX_LIST_LIMIT, "findings") = 100) -> dict[str, Any]:
        """Findings for one run, blocking first, with optional filters (audit_type, result, blocking, rule_id).
        Rows omit the calculation trail and guideline body; call mortgage_get_finding for those. Read-only."""
        def _go() -> dict[str, Any]:
            rows = repo.list_findings(loan_id, run_id, audit_type=audit_type, result=result, blocking=blocking, rule_id=rule_id)
            page = _page(rows, limit, "findings", transform=_finding_row)
            page["filters"] = {"audit_type": audit_type, "result": result, "blocking": blocking, "rule_id": rule_id}
            return page
        return _guard(_go)

    @_tool(name="mortgage_get_finding", annotations=READ_ONLY)
    def mortgage_get_finding(loan_id: LoanId, run_id: RunId, audit_type: Annotated[AuditType, Field(description="PREAPPROVAL or SUBMISSION_READINESS.")],
                             finding_id: Annotated[str, Field(description="Finding id within the audit, e.g. 'F-002'.", min_length=1, max_length=64)]) -> dict[str, Any]:
        """One finding in full: result, blocking flag, explanation, discrepancy, proposed action, evidence ids,
        the Decimal calculation trail, the cited guideline source, and any review decisions recorded on it.
        Read-only."""
        def _go() -> dict[str, Any]:
            rows = repo.list_findings(loan_id, run_id, audit_type=audit_type)
            match = next((f for f in rows if f.finding_id == finding_id), None)
            if match is None:
                raise NotFound(f"finding {finding_id} not found in {audit_type} audit of run {loan_id}/{run_id}")
            decisions = [d for d in repo.list_review_decisions(loan_id, run_id) if d.audit_type == audit_type and d.target_id == finding_id]
            return {**_dump(match, full=True), "review_decisions": [_dump(d) for d in decisions]}
        return _guard(_go)

    @_tool(name="mortgage_search_findings", annotations=READ_ONLY)
    def mortgage_search_findings(query: Annotated[str, Field(description="Case-insensitive text or rule id to match against rule_id, explanation, discrepancy and proposed_action.", min_length=1, max_length=200)],
                                 limit: _limit(25, MAX_SEARCH_LIMIT, "findings") = 25) -> dict[str, Any]:
        """Search findings across every loan and run by rule id or text. Returns compact finding rows with loan_id,
        run_id and audit_type so you can follow up with mortgage_get_finding. Read-only."""
        def _go() -> dict[str, Any]:
            if not query.strip():
                raise ValueError("query must not be blank")
            rows = repo.search_findings(query, limit=limit)
            return {"count": len(rows), "limit": limit, "query": query.strip(), "findings": [_finding_row(f) for f in rows]}
        return _guard(_go)

    @_tool(name="mortgage_list_documents", annotations=READ_ONLY)
    def mortgage_list_documents(loan_id: LoanId, run_id: RunId) -> dict[str, Any]:
        """Document inventory for one run: document id, filename, sha256, type, classification confidence, page count,
        status, duplicate_of. Metadata only; this server never returns a source document's contents. Read-only."""
        def _go() -> dict[str, Any]:
            rows = repo.list_documents(loan_id, run_id)
            return {"count": len(rows), "documents": [_dump(d) for d in rows]}
        return _guard(_go)

    @_tool(name="mortgage_review_queue", annotations=READ_ONLY)
    def mortgage_review_queue(reviewer_role: OptReviewerRole = None, loan_id: OptLoanId = None,
                              limit: _limit(100, MAX_LIST_LIMIT, "queue entries") = 100) -> dict[str, Any]:
        """Open human-review items (REVIEW findings and review_items with no recorded decision), blocking first,
        optionally filtered by reviewer role and loan. Each entry names the target_id and audit_type needed by
        mortgage_record_review_decision. Read-only."""
        def _go() -> dict[str, Any]:
            rows = repo.review_queue(reviewer_role=reviewer_role, loan_id=loan_id)
            page = _page(rows, limit, "entries", full=True)
            page["filters"] = {"reviewer_role": reviewer_role, "loan_id": loan_id}
            return page
        return _guard(_go)

    @_tool(name="mortgage_get_report", annotations=READ_ONLY)
    def mortgage_get_report(loan_id: LoanId, run_id: RunId, name: ReportName = "report.md") -> dict[str, Any]:
        """The masked Markdown text of one report for a run (default 'report.md'; see report_names from
        mortgage_get_run). Content longer than the cap is cut and `truncated` is true. Read-only."""
        def _go() -> dict[str, Any]:
            rep = repo.get_report(loan_id, run_id, name)
            if contains_unmasked_pii(rep.content_md):
                raise ToolError(f"report {name} for {loan_id}/{run_id} contains unmasked identifiers and was withheld; re-sync the run")
            text = rep.content_md
            truncated = len(text) > REPORT_CHAR_CAP
            return {"loan_id": loan_id, "run_id": run_id, "name": rep.name, "sha256": rep.sha256, "chars": len(text),
                    "truncated": truncated, "content_md": text[:REPORT_CHAR_CAP]}
        return _guard(_go)

    @_tool(name="mortgage_list_missing_documents", annotations=READ_ONLY)
    def mortgage_list_missing_documents(loan_id: LoanId, run_id: RunId) -> dict[str, Any]:
        """Documents the audits found missing for one run (type, borrower, description, related rule ids). Read-only."""
        def _go() -> dict[str, Any]:
            rows = repo.list_missing_documents(loan_id, run_id)
            return {"count": len(rows), "missing_documents": [_dump(m) for m in rows]}
        return _guard(_go)

    @_tool(name="mortgage_list_conflicts", annotations=READ_ONLY)
    def mortgage_list_conflicts(loan_id: LoanId, run_id: RunId) -> dict[str, Any]:
        """Conflicts between sources (1003, credit, documents, contract, AUS, LOS) recorded for one run, with the
        competing values and explanation. Read-only."""
        def _go() -> dict[str, Any]:
            rows = repo.list_conflicts(loan_id, run_id)
            return {"count": len(rows), "conflicts": [_dump(c) for c in rows]}
        return _guard(_go)

    @_tool(name="mortgage_list_proposed_actions", annotations=READ_ONLY)
    def mortgage_list_proposed_actions(loan_id: LoanId, run_id: RunId) -> dict[str, Any]:
        """DRAFT proposed actions for one run (LOS corrections, client needs, notes) with before/after values, the
        approver role, and every decision already recorded on each action. Nothing here is executed by this
        server. Read-only."""
        def _go() -> dict[str, Any]:
            actions: list[ProposedAction] = repo.list_proposed_actions(loan_id, run_id)
            decisions: list[ActionDecision] = repo.list_action_decisions(loan_id, run_id)
            by_action: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for d in decisions:
                by_action.setdefault((d.audit_type, d.action_id), []).append(_dump(d))
            rows = [{**_dump(a), "decisions": by_action.get((a.audit_type, a.action_id), [])} for a in actions]
            pending = sum(1 for r in rows if not r["decisions"])
            return {"count": len(rows), "pending": pending, "proposed_actions": rows}
        return _guard(_go)

    @_tool(name="mortgage_list_approvals_required", annotations=READ_ONLY)
    def mortgage_list_approvals_required(loan_id: LoanId, run_id: RunId) -> dict[str, Any]:
        """Approvals an authorized human must give before anything external happens for this run (description,
        approver role, rule ids). Read-only."""
        def _go() -> dict[str, Any]:
            rows = repo.list_approvals_required(loan_id, run_id)
            return {"count": len(rows), "approvals_required": [_dump(a) for a in rows]}
        return _guard(_go)

    @_tool(name="mortgage_eval_latest", annotations=READ_ONLY)
    def mortgage_eval_latest() -> dict[str, Any]:
        """Latest evaluation-harness result: the targets table and whether all targets were met. `available` is
        false when no evaluation has been uploaded. Read-only."""
        def _go() -> dict[str, Any]:
            ev = repo.latest_eval_report()
            if ev is None:
                return {"available": False, "all_targets_met": None, "targets": {}, "report_keys": []}
            return {"available": True, "eval_id": ev.eval_id, "generated_at": ev.generated_at,
                    "all_targets_met": ev.all_targets_met, "targets": ev.targets, "report_keys": sorted(ev.report.keys())}
        return _guard(_go)

    @_tool(name="mortgage_list_run_requests", annotations=READ_ONLY)
    def mortgage_list_run_requests(status: Annotated[Optional[RunRequestStatus], Field(description="Filter: QUEUED, PICKED_UP, COMPLETED, REJECTED.")] = None,
                                   limit: _limit(100, MAX_LIST_LIMIT, "requests") = 100) -> dict[str, Any]:
        """Audit run requests newest first, optionally by status. A request is only a queued row; it never starts
        anything by itself. Read-only."""
        def _go() -> dict[str, Any]:
            rows = repo.list_run_requests(status=status, limit=limit)
            return {"count": len(rows), "limit": limit, "status": status, "run_requests": [_dump(r) for r in rows]}
        return _guard(_go)

    # ---- append-only write tools (own database only, no external effect) ----------------------

    @_tool(name="mortgage_create_run_request", annotations=APPEND_ONLY)
    def mortgage_create_run_request(loan_id: LoanId, note: OptNote = None) -> dict[str, Any]:
        """Create a QUEUED audit run request row for a loan. WRITE (append-only, this system's own database).
        This does NOT run an audit: nothing happens until a human runs /mortgage-file-audit
        tests/fixtures/deidentified/<loan_id> in Claude Code and syncs the result. The row is stamped
        requested_by = the server's configured identity."""
        def _go() -> dict[str, Any]:
            repo.get_loan(loan_id)
            req = repo.create_run_request(loan_id, note, requested_by=who)
            return {"request": _dump(req, full=True), "executed": False,
                    "next_step": f"A human runs `/mortgage-file-audit tests/fixtures/deidentified/{loan_id}` in Claude Code; this server cannot start it."}
        return _guard(_go)

    @_tool(name="mortgage_record_review_decision", annotations=APPEND_ONLY)
    def mortgage_record_review_decision(loan_id: LoanId, run_id: RunId,
                                        target_id: Annotated[str, Field(description="finding_id (e.g. 'F-003') when audit_type is set, or review_id (e.g. 'RV-001') when audit_type is null.", min_length=1, max_length=64)],
                                        decision: Annotated[ReviewDecisionKind, Field(description="CONFIRMED, OVERRIDDEN, or NEEDS_INFO.")],
                                        audit_type: Annotated[Optional[AuditType], Field(description="PREAPPROVAL or SUBMISSION_READINESS for a finding; null for a review_item.")] = None,
                                        note: OptNote = None) -> dict[str, Any]:
        """Record a human reviewer's decision on a REVIEW finding or review item. WRITE (append-only, own database,
        no external effect). Call this ONLY when a named human has explicitly stated the decision you are
        relaying; never decide yourself. Put the human's name and date in `note`. The row is stamped
        decided_by = the server's configured identity, not the human. Recording removes the target from
        mortgage_review_queue."""
        def _go() -> dict[str, Any]:
            stored = repo.add_review_decision(ReviewDecision(loan_id=loan_id, run_id=run_id, audit_type=audit_type,
                                                             target_id=target_id, decision=decision, note=note, decided_by=who))
            return {"decision": _dump(stored, full=True), "executed_externally": False}
        try:
            return _guard(_go)
        except ToolError as exc:
            if "not found in run" in str(exc):
                raise ToolError(f"{exc} Use mortgage_review_queue to list valid targets; audit_type must be null for review items and set for findings.") from None
            raise

    @_tool(name="mortgage_record_action_decision", annotations=APPEND_ONLY)
    def mortgage_record_action_decision(loan_id: LoanId, run_id: RunId,
                                        audit_type: Annotated[AuditType, Field(description="PREAPPROVAL or SUBMISSION_READINESS.")],
                                        action_id: Annotated[str, Field(description="Proposed action id, e.g. 'PA-001'.", min_length=1, max_length=64)],
                                        decision: Annotated[ActionDecisionKind, Field(description="ACCEPTED, REJECTED, or DEFERRED.")],
                                        note: OptNote = None) -> dict[str, Any]:
        """Record a human approver's decision on a DRAFT proposed action. WRITE (append-only, own database, no
        external effect): accepting an action here does NOT perform it; a human still makes the LOS change or
        request. Call this ONLY when a named human has explicitly stated the decision; put their name and date in
        `note`. decided_by = the server's configured identity."""
        def _go() -> dict[str, Any]:
            stored = repo.add_action_decision(ActionDecision(loan_id=loan_id, run_id=run_id, audit_type=audit_type,
                                                             action_id=action_id, decision=decision, note=note, decided_by=who))
            return {"decision": _dump(stored, full=True), "executed_externally": False}
        try:
            return _guard(_go)
        except ToolError as exc:
            if "not found in run" in str(exc):
                raise ToolError(f"{exc} Use mortgage_list_proposed_actions to list valid action ids.") from None
            raise

    # ---- resources ----------------------------------------------------------------------------

    def _json(data: Any) -> str:
        return json.dumps(data, indent=2, sort_keys=False)

    def _resource(fn: Callable[[], Any]) -> str:
        """Tool-backed resource body; a missing id becomes ResourceNotFoundError with the tool's message."""
        try:
            return _json(fn())
        except (NotFound, ToolError) as exc:
            raise ResourceNotFoundError(str(exc)) from None

    @server.resource("mortgage://summary", name="summary", title="Dashboard summary", mime_type="application/json",
                     description="Dashboard totals; same data as mortgage_summary.")
    def resource_summary() -> str:
        return _resource(mortgage_summary)

    @server.resource("mortgage://loans", name="loans", title="Loans with latest run", mime_type="application/json",
                     description="Every loan with its latest run summary; same data as mortgage_list_loans.")
    def resource_loans() -> str:
        return _resource(mortgage_list_loans)

    @server.resource("mortgage://runs/{loan_id}/{run_id}", name="run", title="Run summary", mime_type="application/json",
                     description="Run summary with counts, known limitations and report names; same data as mortgage_get_run.")
    def resource_run(loan_id: str, run_id: str) -> str:
        return _resource(lambda: mortgage_get_run(loan_id, run_id))

    @server.resource("mortgage://reports/{loan_id}/{run_id}/{name}", name="report", title="Masked Markdown report", mime_type="text/markdown",
                     description="Masked Markdown text of one report, capped like mortgage_get_report.")
    def resource_report(loan_id: str, run_id: str, name: str) -> str:
        try:
            return mortgage_get_report(loan_id, run_id, name)["content_md"]
        except ToolError as exc:
            raise ResourceNotFoundError(str(exc)) from None

    # ---- prompts ------------------------------------------------------------------------------

    @server.prompt(name="mortgage_review_queue_briefing", title="Review queue briefing",
                   description="Instruction to summarize the open review queue for one reviewer role without inventing facts.")
    def mortgage_review_queue_briefing(reviewer_role: str = "PROCESSOR") -> str:
        role = (reviewer_role or "").strip().upper()
        if role not in REVIEWER_ROLES:  # MCPError passes through the SDK unchanged; a ValueError would become "Internal server error"
            raise MCPError(code=INVALID_PARAMS, message=f"reviewer_role must be one of {', '.join(REVIEWER_ROLES)}; got {reviewer_role!r}")
        return (
            f"Prepare a briefing of the open review queue for the {role} role of the Mpire mortgage copilot.\n\n"
            f"1. Call mortgage_review_queue(reviewer_role=\"{role}\") and, for context, mortgage_summary().\n"
            "2. For every entry, call mortgage_get_finding (findings) or mortgage_get_run (review items) only as far as\n"
            "   needed to state the open question; do not fetch report text unless an entry is unclear.\n"
            "3. Present blocking entries first. For each, give loan_id, run_id, target_id, rule_id or category, the result\n"
            "   word as returned (PASS, FAIL, MISSING, REVIEW, NOT_APPLICABLE), the stated reason, and the evidence ids\n"
            "   cited. Group by loan.\n"
            "4. Do not invent, estimate, or infer any borrower fact, document, guideline, or calculation. If a field is\n"
            "   null or a list is empty, say that it is missing. Model inference is never a verified fact.\n"
            "5. Do not decide any item and do not call the record tools: this briefing is decision support only. End\n"
            "   with the questions a human reviewer must answer, phrased neutrally.\n"
            "6. Keep SSNs and account numbers masked exactly as returned, and do not propose sending anything or\n"
            "   changing any system of record."
        )

    return server


# ---- CLI --------------------------------------------------------------------------------------


def selftest() -> int:
    """Build a memory-backed server, list tools, call mortgage_summary over the in-memory transport; 0 on success."""
    import anyio
    from mcp import Client

    async def _run() -> dict[str, Any]:
        server = build_server(repository=None, settings=Settings(backend="memory"), identity="selftest")
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            result = await client.call_tool("mortgage_summary", {})
            if result.is_error:
                raise RuntimeError(f"mortgage_summary failed: {result.content[0].text if result.content else 'no content'}")
            return {"ok": True, "server": SERVER_NAME, "version": SERVER_VERSION, "tool_count": len(tools),
                    "tools": sorted(t.name for t in tools), "mortgage_summary": result.structured_content}

    try:
        out = anyio.run(_run)
    except Exception as exc:  # report the class and message, no traceback
        print(f"selftest FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mcp_server", description=f"{SERVER_NAME} MCP server (read-mostly audit data).")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="streamable-http bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="streamable-http port (default 8765)")
    parser.add_argument("--selftest", action="store_true", help="build a memory server, list tools, call mortgage_summary, exit 0")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    log = logging.getLogger(SERVER_NAME)
    try:
        settings = get_settings()
    except ValueError as exc:
        print(f"{SERVER_NAME}: configuration error: {exc}", file=sys.stderr)
        return 2
    try:
        server = build_server(settings=settings)
    except Exception as exc:  # never a traceback with env values in it
        print(f"{SERVER_NAME}: could not build repository ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 2

    auth = "in-memory demo data" if settings.backend == "memory" else (
        f"user JWT from {BEARER_ENV} (RLS applies)" if os.environ.get(BEARER_ENV)
        else ("service role key (bypasses RLS; trusted machine only)" if settings.supabase_service_role_key else "anon key")
    )
    log.warning("starting %s v%s backend=%s auth=%s identity=%s transport=%s", SERVER_NAME, SERVER_VERSION,
                settings.backend, auth, _resolve_identity(None), args.transport)
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport="streamable-http", host=args.host, port=args.port)
    return 0
