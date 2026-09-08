"""Service-only local sync: load MPIRE_OUTPUT_DIR/<loan_id>/<run_id> through services.run_loader and upsert it.

The path is confined to MPIRE_OUTPUT_DIR (ids are pattern-checked and the resolved path must stay
inside the output root, so symlinks and `..` cannot escape). If the fixture directory carries a
MANIFEST.yaml, its `pii_pattern_allowlist` and `description` are passed to the loader exactly as
the sync script does. Source documents are never read; only derived JSON and Markdown.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api.deps import ServiceRepo
from services.config import REPO_ROOT
from services.models import Run
from services.run_loader import load_run_dir

router = APIRouter(tags=["sync"])

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "deidentified"


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    loan_id: str = Field(pattern=ID_PATTERN)
    run_id: str = Field(pattern=ID_PATTERN)


def confined_run_dir(output_dir: Path, loan_id: str, run_id: str) -> Path:
    """Resolve <output_dir>/<loan_id>/<run_id> and refuse anything that lands outside output_dir."""
    base = output_dir.resolve()
    candidate = (base / loan_id / run_id)
    resolved = candidate.resolve()
    if resolved.parent.parent != base or resolved.name != run_id or resolved.parent.name != loan_id:
        raise HTTPException(status_code=400, detail="run directory is outside MPIRE_OUTPUT_DIR")
    return resolved


def fixture_manifest(loan_id: str) -> tuple[tuple[str, ...], Optional[str]]:
    path = FIXTURE_ROOT / loan_id / "MANIFEST.yaml"
    if not path.is_file():
        return (), None
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return (), None
    if not isinstance(data, dict):
        return (), None
    allow = data.get("pii_pattern_allowlist") or []
    allowlist = tuple(str(t) for t in allow if isinstance(t, (str, int)))
    description = data.get("description")
    return allowlist, (str(description) if description else None)


@router.post("/sync/runs", response_model=Run, status_code=201, summary="Load one local run directory and upsert it (service key only)")
def sync_run(body: SyncRequest, request: Request, repo: ServiceRepo) -> Run:
    settings = request.app.state.settings
    run_dir = confined_run_dir(settings.output_dir, body.loan_id, body.run_id)
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"run directory {body.loan_id}/{body.run_id} not found under MPIRE_OUTPUT_DIR")
    allowlist, description = fixture_manifest(body.loan_id)
    bundle = load_run_dir(run_dir, pii_allowlist=allowlist, loan_description=description)  # RunLoadError -> 400
    return repo.upsert_run_bundle(bundle)
