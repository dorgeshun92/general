"""services.config: .env precedence, backend validation, and a redacted view that never leaks keys."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import config
from services.config import REPO_ROOT, Settings, get_settings, load_dotenv

ENV_KEYS = ("MPIRE_REPO_BACKEND", "MPIRE_OUTPUT_DIR", "MPIRE_API_HOST", "MPIRE_API_PORT", "SUPABASE_URL",
            "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # get_settings() reads REPO_ROOT/.env; point it at an empty directory so a developer's .env cannot leak in.
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    return tmp_path


# ---- load_dotenv -----------------------------------------------------------------------

def test_load_dotenv_existing_env_wins(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("SUPABASE_URL=https://dotenv.example\nSUPABASE_ANON_KEY='quoted-anon'\n", encoding="utf-8")
    monkeypatch.setenv("SUPABASE_URL", "https://process.example")
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    load_dotenv(env)
    import os
    assert os.environ["SUPABASE_URL"] == "https://process.example"   # existing value kept
    assert os.environ["SUPABASE_ANON_KEY"] == "quoted-anon"          # quotes stripped
    monkeypatch.delenv("SUPABASE_ANON_KEY")


def test_load_dotenv_skips_comments_blank_and_malformed_lines(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text('# comment\n\nNOT_AN_ASSIGNMENT\n  MPIRE_API_HOST = "0.0.0.0"  \n', encoding="utf-8")
    monkeypatch.delenv("MPIRE_API_HOST", raising=False)
    monkeypatch.delenv("NOT_AN_ASSIGNMENT", raising=False)
    load_dotenv(env)
    import os
    assert os.environ["MPIRE_API_HOST"] == "0.0.0.0"
    assert "NOT_AN_ASSIGNMENT" not in os.environ
    monkeypatch.delenv("MPIRE_API_HOST")


def test_load_dotenv_missing_file_is_a_noop(tmp_path):
    load_dotenv(tmp_path / "absent.env")  # must not raise


def test_get_settings_reads_repo_dotenv_by_default(clean_env, monkeypatch):
    (clean_env / ".env").write_text("MPIRE_REPO_BACKEND=supabase\nSUPABASE_URL=https://x.supabase.co/\n"
                                    "SUPABASE_SERVICE_ROLE_KEY=svc-from-dotenv\n", encoding="utf-8")
    s = get_settings()
    assert s.backend == "supabase" and s.supabase_url == "https://x.supabase.co"
    assert s.supabase_service_role_key == "svc-from-dotenv"
    for key in ("MPIRE_REPO_BACKEND", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(key)


# ---- get_settings -----------------------------------------------------------------------

def test_defaults(clean_env):
    s = get_settings(dotenv=False)
    assert s.backend == "memory"
    assert s.output_dir == clean_env / "output" / "audits"   # REPO_ROOT is redirected by the fixture
    assert s.api_host == "127.0.0.1" and s.api_port == 8080
    assert s.supabase_url is None and not s.supabase_configured


def test_backend_must_be_memory_or_supabase(clean_env, monkeypatch):
    monkeypatch.setenv("MPIRE_REPO_BACKEND", "postgres")
    with pytest.raises(ValueError, match="MPIRE_REPO_BACKEND"):
        get_settings(dotenv=False)
    monkeypatch.setenv("MPIRE_REPO_BACKEND", " Memory ")
    assert get_settings(dotenv=False).backend == "memory"


def test_supabase_backend_requires_url_and_key(clean_env, monkeypatch):
    monkeypatch.setenv("MPIRE_REPO_BACKEND", "supabase")
    with pytest.raises(ValueError, match="SUPABASE_URL"):
        get_settings(dotenv=False)
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co/")
    with pytest.raises(ValueError):
        get_settings(dotenv=False)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    s = get_settings(dotenv=False)
    assert s.supabase_configured and s.supabase_url == "https://proj.supabase.co"  # trailing slash stripped
    assert s.supabase_service_role_key is None


def test_empty_key_values_become_none(clean_env, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "")
    s = get_settings(dotenv=False)
    assert s.supabase_url is None and s.supabase_anon_key is None


def test_output_dir_relative_and_absolute(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("MPIRE_OUTPUT_DIR", "custom/out")
    assert get_settings(dotenv=False).output_dir == clean_env / "custom" / "out"
    monkeypatch.setenv("MPIRE_OUTPUT_DIR", str(tmp_path / "abs"))
    assert get_settings(dotenv=False).output_dir == tmp_path / "abs"


def test_api_port_parsed(clean_env, monkeypatch):
    monkeypatch.setenv("MPIRE_API_PORT", "9999")
    assert get_settings(dotenv=False).api_port == 9999


# ---- redacted ---------------------------------------------------------------------------

def test_redacted_never_contains_key_values():
    secrets = {"anon": "anon-key-XYZ", "service": "service-role-SECRET", "jwt": "jwt-secret-ABC"}
    s = Settings(backend="supabase", supabase_url="https://proj.supabase.co", supabase_anon_key=secrets["anon"],
                 supabase_service_role_key=secrets["service"], supabase_jwt_secret=secrets["jwt"])
    view = s.redacted()
    text = json.dumps(view)
    for value in secrets.values():
        assert value not in text
    assert view["supabase_anon_key"] == "set"
    assert view["supabase_service_role_key"] == "set"
    assert view["supabase_jwt_secret"] == "set"
    assert view["supabase_url"] == "https://proj.supabase.co"
    assert Settings().redacted()["supabase_service_role_key"] is None


def test_settings_is_frozen():
    with pytest.raises(Exception):
        Settings().backend = "supabase"  # type: ignore[misc]
