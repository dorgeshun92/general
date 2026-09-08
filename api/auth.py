"""Caller identification.

Backend `memory`   : no auth. Every request is caller `local-dev`.
Backend `supabase` : `Authorization: Bearer <Supabase user JWT>` is required on every /api route.
    - If SUPABASE_JWT_SECRET is set the token is verified here (HS256, audience `authenticated`)
      and its `sub` (the Supabase user uuid) becomes the caller id. A bad or expired token is 401.
    - If it is not set the token is NOT inspected: the caller id is the constant "jwt" and the
      token is simply forwarded to PostgREST, where Supabase verifies it and RLS applies. In that
      mode the API cannot know who the user is, so it does not stamp `decided_by` /
      `requested_by` (Caller.stamp is None) and the database default `auth.uid()` fills them.
Service routes     : `X-Service-Key: <SUPABASE_SERVICE_ROLE_KEY>`, compared in constant time.
    The literal key "local-dev" is additionally accepted when the backend is `memory` (there is
    no real key to compare against) or when MPIRE_ALLOW_LOCAL_SERVICE=1 is set explicitly.
    Service callers are never derived from a browser session.

Tokens and keys are never echoed in responses, exceptions, or logs.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import HTTPException, Request

LOCAL_DEV_ID = "local-dev"
LOCAL_SERVICE_KEY = "local-dev"
UNVERIFIED_ID = "jwt"
SERVICE_ID = "service"


@dataclass(frozen=True)
class Caller:
    id: str
    bearer: Optional[str]
    is_service: bool = False

    @property
    def stamp(self) -> Optional[str]:
        """Value written into decided_by / requested_by. None means 'let the datastore default it'."""
        return None if self.id == UNVERIFIED_ID else self.id


def _bearer_from(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _verify_jwt(token: str, secret: str) -> str:
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid or expired bearer token") from None
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise HTTPException(status_code=401, detail="bearer token has no subject")
    return sub


def caller(request: Request) -> Caller:
    """FastAPI dependency: identify the user making this request (see module docstring)."""
    settings = request.app.state.settings
    if settings.backend == "memory":
        who = Caller(id=LOCAL_DEV_ID, bearer=None)
    else:
        token = _bearer_from(request)
        if token is None:
            raise HTTPException(status_code=401, detail="missing bearer token",
                                headers={"WWW-Authenticate": "Bearer"})
        if settings.supabase_jwt_secret:
            who = Caller(id=_verify_jwt(token, settings.supabase_jwt_secret), bearer=token)
        else:
            who = Caller(id=UNVERIFIED_ID, bearer=token)
    request.state.caller_id = who.id
    return who


def _accepted_service_keys(request: Request) -> list[str]:
    settings = request.app.state.settings
    keys: list[str] = []
    if settings.supabase_service_role_key:
        keys.append(settings.supabase_service_role_key)
    if settings.backend == "memory" or getattr(request.app.state, "allow_local_service", False):
        keys.append(LOCAL_SERVICE_KEY)
    return keys


def service_caller(request: Request) -> Caller:
    """FastAPI dependency for service-only routes (eval upload, local sync)."""
    presented = request.headers.get("x-service-key")
    if not presented:
        raise HTTPException(status_code=401, detail="missing X-Service-Key")
    ok = False
    for key in _accepted_service_keys(request):
        # Evaluate every candidate so timing does not reveal which one matched.
        ok = hmac.compare_digest(presented.encode("utf-8"), key.encode("utf-8")) or ok
    if not ok:
        raise HTTPException(status_code=403, detail="invalid service key")
    request.state.caller_id = SERVICE_ID
    return Caller(id=SERVICE_ID, bearer=None, is_service=True)
