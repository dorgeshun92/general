from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api import __version__

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    backend: str
    version: str
    demo: bool


@router.get("/health", response_model=Health, summary="Liveness and backend mode (no auth)")
def health(request: Request) -> Health:
    state = request.app.state
    return Health(status="ok", backend=state.settings.backend, version=__version__, demo=state.demo)
