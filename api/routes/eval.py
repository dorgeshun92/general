"""Evaluation harness summaries. Upload is service-only; anyone signed in can read the latest."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException

from api.deps import ServiceRepo, UserRepo
from services.models import EvalReport

router = APIRouter(tags=["eval"])


def eval_report_from_body(body: dict[str, Any]) -> EvalReport:
    """Accept either an EvalReport-shaped object or a raw eval_report.json from scripts/eval/run_eval.py."""
    if "aggregate" in body and isinstance(body["aggregate"], dict):
        agg = body["aggregate"]
        if "all_targets_met" not in agg or "targets" not in agg:
            raise HTTPException(status_code=422, detail="eval_report.json aggregate needs all_targets_met and targets")
        return EvalReport(generated_at=body.get("generated_at"), all_targets_met=bool(agg["all_targets_met"]),
                          targets=agg["targets"], report=body)
    missing = [k for k in ("all_targets_met", "targets", "report") if k not in body]
    if missing:
        raise HTTPException(status_code=422, detail=f"eval report body missing: {', '.join(missing)}")
    return EvalReport(**{k: v for k, v in body.items() if k in EvalReport.model_fields})


@router.get("/eval/latest", response_model=Optional[EvalReport], summary="Most recent evaluation summary, or null")
def latest_eval(repo: UserRepo) -> Optional[EvalReport]:
    return repo.latest_eval_report()


@router.post("/eval", response_model=EvalReport, status_code=201, summary="Upload an eval_report.json (service key only)")
def upload_eval(repo: ServiceRepo, body: dict[str, Any] = Body(...)) -> EvalReport:
    return repo.add_eval_report(eval_report_from_body(body))
