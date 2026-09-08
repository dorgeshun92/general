"""Build the configured repository. api/ and mcp_server/ call get_repository(); tests build InMemoryRepository directly."""
from __future__ import annotations

from typing import Optional

from services.config import Settings, get_settings
from services.repository import InMemoryRepository, Repository
from services.supabase_client import SupabaseClient
from services.supabase_repo import SupabaseRepository


def build_repository(settings: Optional[Settings] = None, *, bearer: Optional[str] = None) -> Repository:
    settings = settings or get_settings()
    if settings.backend == "memory":
        return InMemoryRepository()
    key = settings.supabase_service_role_key or settings.supabase_anon_key
    client = SupabaseClient(settings.supabase_url, key, bearer=bearer)
    return SupabaseRepository(client)
