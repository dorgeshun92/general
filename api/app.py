"""Application factory. `create_app()` reads settings from the environment; tests inject both.

Error mapping (every body is {"detail": "..."}):
    services.repository.NotFound        -> 404
    services.repository.Conflicted      -> 409
    services.run_loader.RunLoadError    -> 400
    services.supabase_client.SupabaseError -> 401/403 when PostgREST rejected the credentials, else 502
    request validation                  -> 422 (one string, not the FastAPI list)
    anything else                       -> 500 "internal error" (details go to the log, never the client)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import ValidationError

from api import __version__
from api.middleware import SecurityAndAccessLogMiddleware
from api.routes import eval as eval_routes
from api.routes import findings, health, loans, review, run_requests, runs, static, sync
from api.routes.static import DASHBOARD_DIR
from services.config import Settings, get_settings
from services.demo import seed_demo
from services.factory import build_repository
from services.repository import Conflicted, InMemoryRepository, NotFound, Repository
from services.run_loader import RunLoadError
from services.supabase_client import SupabaseClient, SupabaseError
from services.supabase_repo import SupabaseRepository

log = logging.getLogger("mpire.api")


class LazyStaticFiles(StaticFiles):
    """StaticFiles that answers 404 (not 500) while the directory does not exist yet.

    The dashboard agent may create `dashboard/` after the API has started; Starlette's own
    check_dir=False still raises RuntimeError on the first request if the directory is missing.
    """

    async def check_config(self) -> None:
        if self.directory is not None and not os.path.isdir(self.directory):
            raise StarletteHTTPException(status_code=404)
        await super().check_config()

TITLE = "Mpire Mortgage Ops API"
DESCRIPTION = (
    "Read-mostly decision support over masked, de-identified audit outputs. "
    "It does not write to an LOS, send email/SMS, run AUS, trigger TRID, price, pick a lender, "
    "submit a loan, or serve source documents. Writes are append-only dashboard rows only."
)


def _detail(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound):
        return _detail(404, str(exc))

    @app.exception_handler(Conflicted)
    async def _conflict(request: Request, exc: Conflicted):
        return _detail(409, str(exc))

    @app.exception_handler(RunLoadError)
    async def _run_load(request: Request, exc: RunLoadError):
        return _detail(400, str(exc))

    @app.exception_handler(SupabaseError)
    async def _supabase(request: Request, exc: SupabaseError):
        # The PostgREST message can contain row values and is never returned; status + path are logged.
        log.warning("supabase error status=%s path=%s request_id=%s", exc.status, exc.path,
                    getattr(request.state, "request_id", None))
        if exc.status == 401:
            return _detail(401, "datastore rejected the bearer token")
        if exc.status == 403:
            return _detail(403, "datastore denied the request (row-level security)")
        return _detail(502, f"datastore error (HTTP {exc.status})")

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return _detail(422, _format_validation(exc.errors()))

    @app.exception_handler(ValidationError)
    async def _model_validation(request: Request, exc: ValidationError):
        return _detail(422, _format_validation(exc.errors()))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("unhandled error request_id=%s", getattr(request.state, "request_id", None))
        return _detail(500, "internal error")


def _format_validation(errors) -> str:
    parts = []
    for err in errors[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{loc or 'body'}: {err.get('msg', 'invalid')}")
    return "; ".join(parts) or "invalid request"


def _wire_repositories(app: FastAPI, settings: Settings, repository: Optional[Repository]) -> None:
    state = app.state
    if settings.backend == "memory":
        if repository is None:
            repository = InMemoryRepository()
            runs = seed_demo(repository)
            log.info("seeded demo data: %d run(s)", len(runs))
        state.repository = state.service_repository = state.user_repository = repository
        return
    if repository is not None:
        state.service_repository = state.user_repository = repository
    else:
        state.service_repository = build_repository(settings)             # service-role key (server-side)
        user_key = settings.supabase_anon_key or settings.supabase_service_role_key
        state.user_repository = SupabaseRepository(SupabaseClient(settings.supabase_url, user_key))
    state.repository = None  # user routes must go through for_bearer; see api.deps


def create_app(settings: Optional[Settings] = None, repository: Optional[Repository] = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title=TITLE, version=__version__, description=DESCRIPTION,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.settings = settings
    app.state.demo = settings.backend == "memory"
    app.state.allow_local_service = os.environ.get("MPIRE_ALLOW_LOCAL_SERVICE", "").strip() == "1"
    log.info("settings %s", settings.redacted())
    if app.state.allow_local_service and settings.backend != "memory":
        log.warning("MPIRE_ALLOW_LOCAL_SERVICE=1: the 'local-dev' service key is accepted on a %s backend", settings.backend)

    _wire_repositories(app, settings, repository)
    _install_error_handlers(app)
    app.add_middleware(SecurityAndAccessLogMiddleware)

    for router in (health.router, loans.router, runs.router, findings.router, review.router,
                   run_requests.router, eval_routes.router, sync.router):
        app.include_router(router, prefix="/api")
    app.include_router(static.router)
    # check_dir=False: the dashboard agent may create the directory after startup; until then /dashboard/* is 404.
    app.mount("/dashboard", LazyStaticFiles(directory=str(DASHBOARD_DIR), html=True, check_dir=False), name="dashboard")
    return app
