"""Run requests are a queue for humans. POST creates a QUEUED row and nothing else runs."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from api.deps import CurrentCaller, UserRepo
from services.models import RunRequest, RunRequestStatus

router = APIRouter(tags=["run-requests"])

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$"


class RunRequestIn(BaseModel):
    model_config = ConfigDict(extra="ignore")   # requested_by / status from the client are ignored
    loan_id: str = Field(pattern=ID_PATTERN)
    note: Optional[str] = Field(default=None, max_length=2000)


class RunRequestPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: RunRequestStatus
    run_id: Optional[str] = Field(default=None, pattern=ID_PATTERN)


@router.get("/run-requests", response_model=list[RunRequest])
def list_run_requests(repo: UserRepo, status: Optional[RunRequestStatus] = None,
                      limit: int = Query(100, ge=1, le=500)) -> list[RunRequest]:
    return repo.list_run_requests(status=status, limit=limit)


@router.post("/run-requests", response_model=RunRequest, status_code=201,
             summary="Queue an audit request (a human runs /mortgage-file-audit; nothing runs automatically)")
def create_run_request(body: RunRequestIn, repo: UserRepo, who: CurrentCaller) -> RunRequest:
    return repo.create_run_request(body.loan_id, body.note, who.stamp)


@router.patch("/run-requests/{request_id}", response_model=RunRequest, summary="Move a request through QUEUED → PICKED_UP → COMPLETED | REJECTED")
def update_run_request(request_id: str, body: RunRequestPatch, repo: UserRepo) -> RunRequest:
    return repo.update_run_request(request_id, body.status, run_id=body.run_id)
