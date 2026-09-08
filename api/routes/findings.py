from __future__ import annotations

from fastapi import APIRouter, Query

from api.deps import UserRepo
from services.models import Finding

router = APIRouter(tags=["findings"])


@router.get("/findings/search", response_model=list[Finding], summary="Search findings by rule id or text")
def search_findings(repo: UserRepo, q: str = Query(..., min_length=1, max_length=200),
                    limit: int = Query(50, ge=1, le=500)) -> list[Finding]:
    return repo.search_findings(q, limit=limit)
