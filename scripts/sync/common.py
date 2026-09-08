"""Shared pieces of the sync CLIs: exit codes, the SyncError carrier, settings and repository
resolution. Nothing here reads source documents or prints a key."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from services import factory  # noqa: E402  (module import so tests can monkeypatch build_repository)
from services.config import Settings, get_settings  # noqa: E402
from services.repository import Repository  # noqa: E402
from services.supabase_client import SupabaseError  # noqa: E402

EXIT_OK = 0            # everything requested was synced (or, with --dry-run, validated)
EXIT_NOT_FOUND = 1     # nothing to sync, or the requested loan/run/file does not exist
EXIT_INVALID = 2       # schema, integrity, or PII gate failed, or a refused location: nothing was written
EXIT_BACKEND = 3       # Supabase / network / backend configuration error

BACKENDS = ("memory", "supabase")
MEMORY_NOTE = ("NOTE: backend=memory keeps rows only for the life of this process; "
               "it is useful with --dry-run and in tests, not for publishing.")


class SyncError(Exception):
    """A failure with a definite exit code. The message never contains a key or an offending value."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def add_backend_args(parser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="load and validate only; write nothing")
    parser.add_argument("--backend", choices=BACKENDS, default=None,
                        help="override MPIRE_REPO_BACKEND (memory is only useful with --dry-run or in tests)")


def resolve_settings(backend_override: Optional[str]) -> Settings:
    """Settings from the environment (and .env), with --backend applied first. Config errors are exit 3."""
    if backend_override:
        os.environ["MPIRE_REPO_BACKEND"] = backend_override
    try:
        return get_settings()
    except ValueError as exc:
        raise SyncError(f"backend configuration error: {exc}", EXIT_BACKEND) from exc


def build_repository(settings: Settings) -> Repository:
    try:
        return factory.build_repository(settings)
    except (ValueError, httpx.HTTPError) as exc:
        raise SyncError(f"cannot build the {settings.backend} repository: {exc}", EXIT_BACKEND) from exc


def backend_failure(exc: Exception) -> SyncError:
    """Map a Supabase/network exception to exit 3 without echoing headers or keys."""
    if isinstance(exc, SupabaseError):
        return SyncError(f"Supabase rejected the write (HTTP {exc.status} on {exc.path}): {exc.message}", EXIT_BACKEND)
    return SyncError(f"network error talking to the backend: {type(exc).__name__}: {exc}", EXIT_BACKEND)


def describe(settings: Settings, dry_run: bool) -> str:
    view = settings.redacted()  # never the key values
    return f"backend={view['backend']} output_dir={view['output_dir']} dry_run={'yes' if dry_run else 'no'}"
