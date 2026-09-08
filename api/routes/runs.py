from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from api.deps import UserRepo
from services.models import AuditType, Document, Finding, ResultValue, Run, RunDetail

router = APIRouter(tags=["runs"])


class ReportSummary(BaseModel):
    name: str
    sha256: str


@router.get("/runs", response_model=list[Run], summary="Recent runs across all loans, newest first")
def list_runs(repo: UserRepo, limit: int = Query(50, ge=1, le=500)) -> list[Run]:
    return repo.list_runs(limit=limit)


@router.get("/runs/latest", response_model=list[Run], summary="One run per loan (the latest)")
def latest_runs(repo: UserRepo) -> list[Run]:
    return repo.latest_runs()


@router.get("/runs/{loan_id}/{run_id}", response_model=RunDetail, summary="Full bundle plus dashboard decisions")
def run_detail(loan_id: str, run_id: str, repo: UserRepo) -> RunDetail:
    return repo.get_run_detail(loan_id, run_id)


@router.get("/runs/{loan_id}/{run_id}/findings", response_model=list[Finding], summary="Findings, blocking first")
def run_findings(
    loan_id: str,
    run_id: str,
    repo: UserRepo,
    audit_type: Optional[AuditType] = None,
    result: Optional[ResultValue] = None,
    blocking: Optional[bool] = None,
    rule_id: Optional[str] = None,
) -> list[Finding]:
    return repo.list_findings(loan_id, run_id, audit_type=audit_type, result=result, blocking=blocking, rule_id=rule_id)


@router.get("/runs/{loan_id}/{run_id}/documents", response_model=list[Document])
def run_documents(loan_id: str, run_id: str, repo: UserRepo) -> list[Document]:
    return repo.list_documents(loan_id, run_id)


@router.get("/runs/{loan_id}/{run_id}/reports", response_model=list[ReportSummary], summary="Report names and hashes (no content)")
def run_reports(loan_id: str, run_id: str, repo: UserRepo) -> list[ReportSummary]:
    return [ReportSummary(name=r.name, sha256=r.sha256) for r in repo.list_reports(loan_id, run_id)]


@router.get("/runs/{loan_id}/{run_id}/reports/{name}", response_class=PlainTextResponse,
            responses={200: {"content": {"text/markdown": {}}}}, summary="One masked Markdown report")
def run_report(loan_id: str, run_id: str, name: str, repo: UserRepo) -> PlainTextResponse:
    report = repo.get_report(loan_id, run_id, name)
    return PlainTextResponse(report.content_md, media_type="text/markdown; charset=utf-8",
                             headers={"X-Report-SHA256": report.sha256})
