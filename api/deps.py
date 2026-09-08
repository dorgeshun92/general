"""Repository selection per request.

memory   : one seeded InMemoryRepository shared by every request (app.state.repository).
supabase : user routes get `SupabaseRepository.for_bearer(<user JWT>)` so PostgREST applies RLS as
           that user; service routes get the service-role repository (bypasses RLS, server-side only).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from api.auth import Caller, caller, service_caller
from services.repository import Repository


def get_repo(request: Request, who: Annotated[Caller, Depends(caller)]) -> Repository:
    state = request.app.state
    if state.settings.backend == "memory":
        return state.repository
    if who.is_service:
        return state.service_repository
    return state.user_repository.for_bearer(who.bearer)


def get_service_repo(request: Request, who: Annotated[Caller, Depends(service_caller)]) -> Repository:
    state = request.app.state
    if state.settings.backend == "memory":
        return state.repository
    return state.service_repository


CurrentCaller = Annotated[Caller, Depends(caller)]
ServiceCaller = Annotated[Caller, Depends(service_caller)]
UserRepo = Annotated[Repository, Depends(get_repo)]
ServiceRepo = Annotated[Repository, Depends(get_service_repo)]
