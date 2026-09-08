"""Dashboard bootstrap: `/` (index or placeholder) and `/config.js`. The static tree is mounted by api.app."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from services.config import REPO_ROOT

router = APIRouter(tags=["dashboard"])

DASHBOARD_DIR = REPO_ROOT / "dashboard"

PLACEHOLDER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Mpire Mortgage Ops</title>
<style>body{font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;color:#222}
code{background:#eee;padding:.1em .3em;border-radius:3px}</style></head>
<body><h1>Mpire Mortgage Ops</h1>
<p>The dashboard is being built and is not in this checkout yet (<code>dashboard/</code> is missing).</p>
<p>The API is up: see <a href="/api/health">/api/health</a> and the interactive docs at <a href="/api/docs">/api/docs</a>.</p>
<p><small>Decision support only. This system does not make credit or compliance decisions.</small></p>
</body></html>
"""


@router.get("/", response_class=HTMLResponse, summary="Dashboard entry point (or a placeholder while it is being built)")
def index() -> Response:
    index_html = DASHBOARD_DIR / "index.html"
    if index_html.is_file():
        return FileResponse(index_html, media_type="text/html")
    return HTMLResponse(PLACEHOLDER)


@router.get("/config.js", summary="window.MPIRE_CONFIG for the dashboard (never the service key)",
            responses={200: {"content": {"application/javascript": {}}}})
def config_js(request: Request) -> Response:
    state = request.app.state
    settings = state.settings
    config = {
        "backend": settings.backend,
        "demo": state.demo,
        "supabaseUrl": settings.supabase_url if settings.backend == "supabase" else None,
        "supabaseAnonKey": settings.supabase_anon_key if settings.backend == "supabase" else None,
        "apiBase": "/api",
    }
    body = "window.MPIRE_CONFIG = " + json.dumps(config, sort_keys=True) + ";\n"
    return Response(body, media_type="application/javascript; charset=utf-8", headers={"Cache-Control": "no-store"})
