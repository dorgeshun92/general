"""Environment-driven settings. Reads a local .env if present (never committed) and never logs secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader: KEY=VALUE lines, # comments, no expansion. Existing env wins."""
    path = path or REPO_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    backend: str = "memory"                      # memory | supabase
    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_role_key: str | None = None
    supabase_jwt_secret: str | None = None
    output_dir: Path = field(default_factory=lambda: REPO_ROOT / "output" / "audits")
    api_host: str = "127.0.0.1"
    api_port: int = 8080

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and (self.supabase_service_role_key or self.supabase_anon_key))

    def redacted(self) -> dict:
        """Safe-to-log view."""
        return {
            "backend": self.backend,
            "supabase_url": self.supabase_url,
            "supabase_anon_key": "set" if self.supabase_anon_key else None,
            "supabase_service_role_key": "set" if self.supabase_service_role_key else None,
            "supabase_jwt_secret": "set" if self.supabase_jwt_secret else None,
            "output_dir": str(self.output_dir),
            "api_host": self.api_host,
            "api_port": self.api_port,
        }


def get_settings(dotenv: bool = True) -> Settings:
    if dotenv:
        load_dotenv()
    backend = os.environ.get("MPIRE_REPO_BACKEND", "memory").strip().lower()
    if backend not in ("memory", "supabase"):
        raise ValueError(f"MPIRE_REPO_BACKEND must be 'memory' or 'supabase', got {backend!r}")
    out = os.environ.get("MPIRE_OUTPUT_DIR", "output/audits")
    out_path = Path(out) if Path(out).is_absolute() else REPO_ROOT / out
    settings = Settings(
        backend=backend,
        supabase_url=(os.environ.get("SUPABASE_URL") or "").rstrip("/") or None,
        supabase_anon_key=os.environ.get("SUPABASE_ANON_KEY") or None,
        supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or None,
        supabase_jwt_secret=os.environ.get("SUPABASE_JWT_SECRET") or None,
        output_dir=out_path,
        api_host=os.environ.get("MPIRE_API_HOST", "127.0.0.1"),
        api_port=int(os.environ.get("MPIRE_API_PORT", "8080")),
    )
    if settings.backend == "supabase" and not settings.supabase_configured:
        raise ValueError("MPIRE_REPO_BACKEND=supabase requires SUPABASE_URL and a key (see .env.example)")
    return settings
