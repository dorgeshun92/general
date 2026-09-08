from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from api.deps import UserRepo
from services.models import DashboardSummary, Loan, Run

router = APIRouter(tags=["loans"])


class LoanWithLatestRun(Loan):
    latest_run: Optional[Run] = None


@router.get("/summary", response_model=DashboardSummary, summary="Dashboard counters")
def summary(repo: UserRepo) -> DashboardSummary:
    return repo.dashboard_summary()


@router.get("/loans", response_model=list[LoanWithLatestRun], summary="Loans joined with their latest run")
def list_loans(repo: UserRepo) -> list[LoanWithLatestRun]:
    latest = {r.loan_id: r for r in repo.latest_runs()}
    return [LoanWithLatestRun(**loan.model_dump(), latest_run=latest.get(loan.loan_id)) for loan in repo.list_loans()]


@router.get("/loans/{loan_id}", response_model=Loan)
def get_loan(loan_id: str, repo: UserRepo) -> Loan:
    return repo.get_loan(loan_id)


@router.get("/loans/{loan_id}/runs", response_model=list[Run], summary="Runs for one loan, newest first")
def loan_runs(loan_id: str, repo: UserRepo) -> list[Run]:
    repo.get_loan(loan_id)  # 404 for unknown loans instead of an empty list
    return repo.list_runs(loan_id=loan_id, limit=500)
