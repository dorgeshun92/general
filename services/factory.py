"""Build the configured repository. api/ and mcp_server/ call build_repository() or build_user_repository(); tests build InMemoryRepository directly."""
from __future__ import annotations

from typing import Optional

from services.config import Settings, get_settings
from services.repository import InMemoryRepository, Repository
from services.supabase_client import SupabaseClient
from services.supabase_repo import SupabaseRepository


def build_repository(settings: Optional[Settings] = None, *, bearer: Optional[str] = None) -> Repository:
    """Service-side repository: service role key (bypasses RLS). For the sync script and the MCP server."""
    settings = settings or get_settings()
    if settings.backend == "memory":
        return InMemoryRepository()
    key = settings.supabase_service_role_key or settings.supabase_anon_key
    client = SupabaseClient(settings.supabase_url, key, bearer=bearer)
    return SupabaseRepository(client)


def build_user_repository(settings: Optional[Settings] = None, *, bearer: str) -> Repository:
    """Per-user repository: anon key as apikey, the user's JWT as Authorization, so RLS applies and
    the service role key never travels on a user request. The API uses this for every user route."""
    settings = settings or get_settings()
    if settings.backend == "memory":
        return InMemoryRepository()
    if not settings.supabase_anon_key:
        raise ValueError("SUPABASE_ANON_KEY is required for user-scoped access")
    client = SupabaseClient(settings.supabase_url, settings.supabase_anon_key, bearer=bearer)
    return SupabaseRepository(client)
