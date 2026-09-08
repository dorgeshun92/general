"""Security headers, request ids, and a structured access log (pure ASGI, no header/query logging)."""
from __future__ import annotations

import json
import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

access_log = logging.getLogger("mpire.api.access")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Dashboard: scripts only from self and jsdelivr (supabase-js), XHR/websocket to self and Supabase.
CSP_DEFAULT = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self' https://*.supabase.co wss://*.supabase.co; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "img-src 'self' data:; "
    "font-src 'self' https://fonts.gstatic.com; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
# Swagger UI (/api/docs) is an inline bootstrap script plus assets from jsdelivr; scoped to that path only.
CSP_DOCS = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
)
DOCS_PATHS = ("/api/docs", "/api/redoc")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def csp_for(path: str) -> str:
    return CSP_DOCS if any(path == p or path.startswith(p + "/") for p in DOCS_PATHS) else CSP_DEFAULT


class SecurityAndAccessLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = ""
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                incoming = value.decode("latin-1", "replace")
                break
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        path = scope.get("path", "")
        started = time.perf_counter()
        status = {"code": 0}

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers = MutableHeaders(scope=message)
                for key, value in SECURITY_HEADERS.items():
                    headers.setdefault(key, value)
                headers["Content-Security-Policy"] = csp_for(path)
                headers["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            # Path only: query strings can carry tokens, headers always can. Neither is logged.
            access_log.info(json.dumps({
                "event": "http_request",
                "request_id": request_id,
                "method": scope.get("method"),
                "path": path,
                "status": status["code"] or 500,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "caller": scope["state"].get("caller_id"),
            }, sort_keys=True))
