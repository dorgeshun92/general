"""Unit tests for .claude/hooks/hook_common.py (path helpers, stdin parsing, fail-closed wrapper)."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_common as hc  # noqa: E402


# --- stdin parsing ------------------------------------------------------------

def test_read_stdin_json_parses_object():
    payload = hc.read_stdin_json(io.StringIO('{"tool_name": "Bash", "tool_input": {"command": "ls"}}'))
    assert payload["tool_name"] == "Bash"


@pytest.mark.parametrize("raw", ["", "   ", "{not json", "[1, 2]", "\"string\"", "null"])
def test_read_stdin_json_rejects_non_objects(raw):
    with pytest.raises(hc.HookError):
        hc.read_stdin_json(io.StringIO(raw))


def test_tool_input_must_be_object():
    with pytest.raises(hc.HookError):
        hc.tool_input_of({"tool_input": "rm -rf /"})
    assert hc.tool_input_of({"tool_input": None}) == {}


@pytest.mark.parametrize("sid", ["../etc", "a/b", "", "x" * 200, "with space"])
def test_session_id_rejects_unsafe_values(sid):
    with pytest.raises(hc.HookError):
        hc.session_id_of({"session_id": sid})


def test_session_id_accepts_normal_and_missing():
    assert hc.session_id_of({"session_id": "abc-123_x.y"}) == "abc-123_x.y"
    assert hc.session_id_of({}) is None


# --- fail closed --------------------------------------------------------------

def test_run_fail_closed_converts_exception_to_exit_2(capsys):
    def broken():
        raise ValueError("boom")

    with pytest.raises(SystemExit) as exc:
        hc.run_fail_closed(broken, "unit")
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "fail closed" in err
    assert "boom" in err


def test_run_fail_closed_passes_through_exit_0():
    with pytest.raises(SystemExit) as exc:
        hc.run_fail_closed(lambda: 0, "unit")
    assert exc.value.code == 0


def test_run_fail_closed_passes_through_none_as_0():
    with pytest.raises(SystemExit) as exc:
        hc.run_fail_closed(lambda: None, "unit")
    assert exc.value.code == 0


def test_every_hook_script_is_runnable_and_fails_closed_on_malformed_stdin():
    fixture = REPO_ROOT / "tests" / "fixtures" / "hook_inputs" / "malformed_stdin.json"
    for script in ("pre_tool_guard.py", "post_write_validate.py", "stop_gate.py"):
        with fixture.open("rb") as fh:
            proc = subprocess.run(
                [sys.executable, str(HOOKS_DIR / script)], stdin=fh, capture_output=True, text=True, cwd=REPO_ROOT
            )
        assert proc.returncode == 2, f"{script} must exit 2 on malformed stdin, got {proc.returncode}: {proc.stderr}"
        assert "fail closed" in proc.stderr


# --- paths --------------------------------------------------------------------

def test_normalize_path_resolves_dotdot_and_tilde(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    assert hc.normalize_path("output/../CLAUDE.md", root) == root / "CLAUDE.md"
    assert hc.normalize_path("../../etc/passwd", root) == Path(os.path.normpath(str(root / "../../etc/passwd")))
    home = Path(os.path.expanduser("~"))
    assert hc.normalize_path("~/.ssh/id_rsa", root) == home / ".ssh" / "id_rsa"
    assert hc.normalize_path("/abs/path", root) == Path("/abs/path")


def test_normalize_path_respects_cwd(tmp_path):
    root = tmp_path
    assert hc.normalize_path("x.py", root, cwd=root / "scripts") == root / "scripts" / "x.py"


@pytest.mark.parametrize("bad", ["", "   ", "a\x00b"])
def test_normalize_path_rejects_empty_or_nul(bad, tmp_path):
    with pytest.raises(hc.HookError):
        hc.normalize_path(bad, tmp_path)


@pytest.mark.parametrize(
    "rel, expected",
    [
        (".env", ".env"),
        (".env.local", ".env.*"),
        ("secrets/x.json", "secrets"),
        ("credentials/sa.json", "credentials"),
        ("config/a.pem", "*.pem"),
        ("config/a.key", "*.key"),
        ("config/a.p12", "*.p12"),
        ("config/a.pfx", "*.pfx"),
        (".claude/settings.local.json", "settings.local.json"),
        ("x/service.credentials.json", "*.credentials.json"),
    ],
)
def test_matches_secret_repo_relative(rel, expected, tmp_path):
    assert hc.matches_secret(tmp_path / rel, tmp_path) == expected


def test_matches_secret_home_stores(tmp_path):
    home = Path(os.path.expanduser("~"))
    assert hc.matches_secret(home / ".ssh" / "id_rsa", tmp_path) == ".ssh"
    assert hc.matches_secret(home / ".aws" / "credentials", tmp_path) == ".aws"
    assert hc.matches_secret(home / ".config" / "gcloud" / "x.db", tmp_path) == "~/.config/gcloud"
    assert hc.matches_secret(tmp_path / "docs" / "gcloud.md", tmp_path) is None  # gcloud only under .config


@pytest.mark.parametrize("rel", ["CLAUDE.md", "schemas/loan_file.schema.json", ".claude/settings.json", "docs/environment.md"])
def test_matches_secret_negative(rel, tmp_path):
    assert hc.matches_secret(tmp_path / rel, tmp_path) is None


def test_symlink_escape_detected(tmp_path):
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "passwd").write_text("x")
    link = root / "link"
    link.symlink_to(outside)
    assert hc.symlink_escapes(root / "link" / "passwd", root) is True
    (root / "real.txt").write_text("y")
    assert hc.symlink_escapes(root / "real.txt", root) is False
    inner = root / "inner"
    inner.symlink_to(root / "real.txt")
    assert hc.symlink_escapes(inner, root) is False


def test_read_only_tool_paths():
    assert hc.under_read_only_tool_path(Path("/usr/lib/python3.11/os.py"))
    assert hc.under_read_only_tool_path(Path("/usr/bin/python3"))
    assert hc.under_read_only_tool_path(Path("/dev/null"))
    assert not hc.under_read_only_tool_path(Path("/etc/passwd"))
    assert not hc.under_read_only_tool_path(Path("/usr/lib/python3-evil/x"))


@pytest.mark.parametrize(
    "rel, expected",
    [
        ("output/audits/LN-1/RUN-1/loan_file.json", "output/audits/LN-1/RUN-1"),
        ("output/audits/LN-1/RUN-1/reports/x.md", "output/audits/LN-1/RUN-1"),
        ("output/audits/x/y.json", "output/audits/x"),
        ("output/audits/README.md", None),
        ("output/drafts/x.json", None),
        ("docs/x.json", None),
    ],
)
def test_run_dir_for(rel, expected):
    assert hc.run_dir_for(rel) == expected


# --- session state --------------------------------------------------------------

def test_record_session_activity_creates_and_extends_state(tmp_path):
    changed = hc.record_session_activity("sess-1", ["output/audits/LN/RUN"], tmp_path)
    assert changed
    state = hc.load_state("sess-1", tmp_path)
    assert "started_at" in state and state["run_dirs"] == ["output/audits/LN/RUN"]
    assert hc.record_session_activity("sess-1", ["output/audits/LN/RUN"], tmp_path) is False
    assert hc.record_session_activity("sess-1", ["output/audits/LN/RUN-2"], tmp_path) is True
    assert hc.load_state("sess-1", tmp_path)["run_dirs"] == ["output/audits/LN/RUN", "output/audits/LN/RUN-2"]
    assert hc.state_path("sess-1", tmp_path) == tmp_path / "output" / ".hook_state" / "sess-1.json"


def test_load_state_rejects_corrupt_file(tmp_path):
    p = hc.state_path("sess-2", tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("[]")
    with pytest.raises(hc.HookError):
        hc.load_state("sess-2", tmp_path)
    p.write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        hc.load_state("sess-2", tmp_path)
