"""Tests for .claude/hooks/post_write_validate.py (PostToolUse schema validation)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "hook_inputs"
sys.path.insert(0, str(HOOKS_DIR))

import hook_common as hc  # noqa: E402
import post_write_validate as pwv  # noqa: E402

VALID_DOC = FIXTURES / "doc_document_inventory_valid.json"
INVALID_DOC = FIXTURES / "doc_document_inventory_invalid.json"


def run_hook(payload: dict | str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / "post_write_validate.py")], input=stdin, capture_output=True, text=True, cwd=cwd, timeout=120
    )


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Isolated repo copy with the real validator and schemas."""
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copytree(REPO_ROOT / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(REPO_ROOT / "schemas", root / "schemas")
    (root / "output" / "audits").mkdir(parents=True)
    return root


@pytest.fixture
def run_dir_in_real_repo():
    rel = f"output/audits/HOOKTEST-{uuid.uuid4().hex[:12]}/RUN-1"
    path = REPO_ROOT / rel
    path.mkdir(parents=True)
    try:
        yield rel, path
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


# --- pure function ---------------------------------------------------------------

def test_valid_document_passes(tmp_repo):
    target = tmp_repo / "output/audits/LN/RUN/document_inventory.json"
    target.parent.mkdir(parents=True)
    shutil.copy(VALID_DOC, target)
    ok, msg = pwv.validate_written_file(str(target), tmp_repo)
    assert ok and "VALID" in msg


def test_invalid_document_fails_with_errors(tmp_repo):
    target = tmp_repo / "output/audits/LN/RUN/document_inventory.json"
    target.parent.mkdir(parents=True)
    shutil.copy(INVALID_DOC, target)
    ok, msg = pwv.validate_written_file(str(target), tmp_repo)
    assert ok is False
    assert "INVALID" in msg and "run_id" in msg


@pytest.mark.parametrize("basename, schema", sorted(hc.SCHEMA_BY_FILENAME.items()))
def test_every_mapped_filename_is_validated(tmp_repo, basename, schema):
    target = tmp_repo / "output/audits/LN/RUN" / basename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}")  # empty object is invalid for every schema (required fields)
    ok, msg = pwv.validate_written_file(str(target), tmp_repo)
    assert ok is False and schema in msg


def test_unmapped_json_must_parse_as_object(tmp_repo):
    target = tmp_repo / "output/audits/LN/RUN/run_manifest.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"run_id": "RUN-1"}')
    assert pwv.validate_written_file(str(target), tmp_repo)[0] is True
    target.write_text("[1, 2]")
    assert pwv.validate_written_file(str(target), tmp_repo)[0] is False
    target.write_text("{broken")
    ok, msg = pwv.validate_written_file(str(target), tmp_repo)
    assert ok is False and "not valid JSON" in msg


def test_paths_outside_output_audits_are_ignored(tmp_repo):
    assert pwv.validate_written_file("docs/notes.md", tmp_repo)[0] is True
    assert pwv.validate_written_file("output/drafts/x.json", tmp_repo)[0] is True
    assert pwv.validate_written_file("output/audits/LN/RUN/report.md", tmp_repo)[0] is True


def test_missing_file_after_write_is_an_error(tmp_repo):
    with pytest.raises(hc.HookError):
        pwv.validate_written_file("output/audits/LN/RUN/loan_file.json", tmp_repo)


def test_validator_error_is_raised_not_swallowed(tmp_repo):
    """A broken validator must block, never silently pass."""
    target = tmp_repo / "output/audits/LN/RUN/document_inventory.json"
    target.parent.mkdir(parents=True)
    shutil.copy(VALID_DOC, target)
    (tmp_repo / "scripts" / "validate_schema.py").write_text("import sys\nsys.exit(2)\n")
    with pytest.raises(hc.HookError):
        pwv.validate_written_file(str(target), tmp_repo)
    (tmp_repo / "scripts" / "validate_schema.py").unlink()
    with pytest.raises(hc.HookError):
        pwv.validate_written_file(str(target), tmp_repo)


# --- end-to-end ---------------------------------------------------------------------

def test_hook_exit_0_on_valid_write(run_dir_in_real_repo):
    rel, path = run_dir_in_real_repo
    shutil.copy(VALID_DOC, path / "document_inventory.json")
    proc = run_hook({"hook_event_name": "PostToolUse", "tool_name": "Write",
                     "tool_input": {"file_path": f"{rel}/document_inventory.json", "content": "..."},
                     "tool_response": {"success": True}})
    assert proc.returncode == 0, proc.stderr
    assert "VALID" in proc.stdout


def test_hook_exit_2_on_invalid_write(run_dir_in_real_repo):
    rel, path = run_dir_in_real_repo
    shutil.copy(INVALID_DOC, path / "document_inventory.json")
    proc = run_hook({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                     "tool_input": {"file_path": f"{rel}/document_inventory.json", "old_string": "a", "new_string": "b"},
                     "tool_response": {"success": True}})
    assert proc.returncode == 2
    assert "INVALID" in proc.stderr and "Fix the file" in proc.stderr


def test_hook_ignores_non_audit_paths_and_non_write_tools():
    for name in ("post_write_ignored_docs_path.json", "post_write_not_a_write_tool.json"):
        proc = run_hook((FIXTURES / name).read_text(encoding="utf-8"))
        assert proc.returncode == 0, f"{name}: {proc.stderr}"


def test_hook_fails_closed():
    assert run_hook((FIXTURES / "malformed_stdin.json").read_text()).returncode == 2
    assert run_hook({"tool_name": "Write", "tool_input": {}}).returncode == 2  # no file_path
    assert run_hook({"tool_name": "Write", "tool_input": {"file_path": "output/audits/LN/RUN/missing.json"}}).returncode == 2
