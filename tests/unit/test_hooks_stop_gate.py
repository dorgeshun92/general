"""Tests for .claude/hooks/stop_gate.py (manifest + validation gate on session completion)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "hook_inputs"
sys.path.insert(0, str(HOOKS_DIR))

import hook_common as hc  # noqa: E402
import stop_gate  # noqa: E402

VALID_DOC = FIXTURES / "doc_document_inventory_valid.json"
INVALID_DOC = FIXTURES / "doc_document_inventory_invalid.json"
MANIFEST = FIXTURES / "doc_run_manifest.json"
FAR_FUTURE = time.time() + 10 * 365 * 86400  # disables the mtime scan in shared-repo tests


def run_hook(payload: dict | str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / "stop_gate.py")], input=stdin, capture_output=True, text=True, cwd=cwd, timeout=300
    )


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copytree(REPO_ROOT / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(REPO_ROOT / "schemas", root / "schemas")
    (root / "output" / "audits").mkdir(parents=True)
    return root


def make_run(root: Path, rel: str, manifest: bool = True, inventory: Path | None = VALID_DOC, extra: dict[str, str] | None = None) -> Path:
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    if manifest:
        shutil.copy(MANIFEST, d / hc.RUN_MANIFEST_NAME)
    if inventory is not None:
        shutil.copy(inventory, d / "document_inventory.json")
    for name, text in (extra or {}).items():
        (d / name).write_text(text)
    return d


# --- pure functions -------------------------------------------------------------------

def test_no_session_or_no_state_never_blocks(tmp_repo):
    assert stop_gate.evaluate(None, tmp_repo) == (False, [], [])
    assert stop_gate.evaluate("never-ran-an-audit", tmp_repo) == (False, [], [])


def test_complete_valid_run_allows(tmp_repo):
    make_run(tmp_repo, "output/audits/LN/RUN-1")
    hc.record_session_activity("s1", ["output/audits/LN/RUN-1"], tmp_repo)
    block, problems, dirs = stop_gate.evaluate("s1", tmp_repo)
    assert block is False and problems == [] and dirs == ["output/audits/LN/RUN-1"]


def test_missing_manifest_blocks(tmp_repo):
    make_run(tmp_repo, "output/audits/LN/RUN-1", manifest=False)
    hc.record_session_activity("s1", ["output/audits/LN/RUN-1"], tmp_repo)
    block, problems, _ = stop_gate.evaluate("s1", tmp_repo)
    assert block is True
    assert any("missing run_manifest.json" in p for p in problems)


def test_invalid_schema_document_blocks(tmp_repo):
    make_run(tmp_repo, "output/audits/LN/RUN-1", inventory=INVALID_DOC)
    hc.record_session_activity("s1", ["output/audits/LN/RUN-1"], tmp_repo)
    block, problems, _ = stop_gate.evaluate("s1", tmp_repo)
    assert block is True
    assert any("INVALID against document_inventory" in p for p in problems)


def test_unparseable_json_and_non_object_manifest_block(tmp_repo):
    make_run(tmp_repo, "output/audits/LN/RUN-1", extra={"notes.json": "{broken"})
    (tmp_repo / "output/audits/LN/RUN-1" / hc.RUN_MANIFEST_NAME).write_text("[]")
    problems = stop_gate.check_run_dir("output/audits/LN/RUN-1", tmp_repo)
    assert any("notes.json: not valid JSON" in p for p in problems)
    assert any("run_manifest.json: top-level JSON value must be an object" in p for p in problems)


def test_nested_json_inside_run_dir_is_checked(tmp_repo):
    d = make_run(tmp_repo, "output/audits/LN/RUN-1")
    (d / "sub").mkdir()
    shutil.copy(INVALID_DOC, d / "sub" / "document_inventory.json")
    assert stop_gate.check_run_dir("output/audits/LN/RUN-1", tmp_repo)


def test_scope_is_limited_to_runs_touched_this_session(tmp_repo):
    """An old invalid run from another session must not block this one."""
    make_run(tmp_repo, "output/audits/OLD/RUN-0", manifest=False, inventory=INVALID_DOC)
    started = time.time() + 5  # session starts strictly after the old run was written
    make_run(tmp_repo, "output/audits/LN/RUN-1")
    hc.save_state("s1", {"started_at": started, "run_dirs": ["output/audits/LN/RUN-1"]}, tmp_repo)
    block, problems, dirs = stop_gate.evaluate("s1", tmp_repo)
    assert dirs == ["output/audits/LN/RUN-1"]
    assert block is False, problems


def test_runs_written_by_scripts_are_found_by_mtime(tmp_repo):
    """Bash-produced runs (no Write tool call) are picked up via modification time."""
    hc.save_state("s1", {"started_at": time.time() - 60, "run_dirs": []}, tmp_repo)
    make_run(tmp_repo, "output/audits/LN/RUN-9", manifest=False)
    block, problems, dirs = stop_gate.evaluate("s1", tmp_repo)
    assert dirs == ["output/audits/LN/RUN-9"]
    assert block is True and "missing run_manifest.json" in problems[0]


def test_flat_layout_run_dir_is_supported(tmp_repo):
    (tmp_repo / "output/audits/x").mkdir(parents=True)
    (tmp_repo / "output/audits/x/y.json").write_text("{}")
    hc.record_session_activity("s1", ["output/audits/x"], tmp_repo)
    block, problems, _ = stop_gate.evaluate("s1", tmp_repo)
    assert block is True and "missing run_manifest.json" in problems[0]


def test_deleted_run_dir_is_reported(tmp_repo):
    hc.record_session_activity("s1", ["output/audits/GONE/RUN-1"], tmp_repo)
    block, problems, _ = stop_gate.evaluate("s1", tmp_repo)
    assert block is True and "no longer exists" in problems[0]


def test_validator_error_raises(tmp_repo):
    make_run(tmp_repo, "output/audits/LN/RUN-1")
    (tmp_repo / "scripts" / "validate_schema.py").write_text("raise SystemExit(2)\n")
    with pytest.raises(hc.HookError):
        stop_gate.check_run_dir("output/audits/LN/RUN-1", tmp_repo)


# --- end-to-end -----------------------------------------------------------------------

@pytest.fixture
def session_in_real_repo():
    sid = f"hooktest-{uuid.uuid4().hex[:12]}"
    rel = f"output/audits/HOOKTEST-{sid}/RUN-1"
    try:
        yield sid, rel
    finally:
        hc.state_path(sid, REPO_ROOT).unlink(missing_ok=True)
        shutil.rmtree(REPO_ROOT / "output" / "audits" / f"HOOKTEST-{sid}", ignore_errors=True)


def _payload(sid: str, active: bool = False) -> dict:
    return {"session_id": sid, "hook_event_name": "Stop", "stop_hook_active": active}


def test_hook_allows_session_that_never_ran_an_audit():
    proc = run_hook((FIXTURES / "stop_no_state.json").read_text(encoding="utf-8"))
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_hook_blocks_then_allows_after_fix(session_in_real_repo):
    sid, rel = session_in_real_repo
    make_run(REPO_ROOT, rel, manifest=False)
    hc.save_state(sid, {"started_at": FAR_FUTURE, "run_dirs": [rel]}, REPO_ROOT)
    proc = run_hook(_payload(sid))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["decision"] == "block" and "run_manifest.json" in out["reason"]
    assert hc.load_state(sid, REPO_ROOT)["stop_blocks"] == 1
    shutil.copy(MANIFEST, REPO_ROOT / rel / hc.RUN_MANIFEST_NAME)
    proc = run_hook(_payload(sid, active=True))
    assert proc.returncode == 0 and "decision" not in proc.stdout
    assert "complete and valid" in proc.stdout
    assert hc.load_state(sid, REPO_ROOT)["stop_blocks"] == 0


def test_hook_blocks_invalid_schema_document(session_in_real_repo):
    sid, rel = session_in_real_repo
    make_run(REPO_ROOT, rel, inventory=INVALID_DOC)
    hc.save_state(sid, {"started_at": FAR_FUTURE, "run_dirs": [rel]}, REPO_ROOT)
    proc = run_hook(_payload(sid))
    out = json.loads(proc.stdout)
    assert out["decision"] == "block" and "INVALID against document_inventory" in out["reason"]


def test_loop_guard_releases_with_system_message(session_in_real_repo):
    sid, rel = session_in_real_repo
    make_run(REPO_ROOT, rel, manifest=False)
    hc.save_state(sid, {"started_at": FAR_FUTURE, "run_dirs": [rel], "stop_blocks": stop_gate.MAX_CONSECUTIVE_BLOCKS}, REPO_ROOT)
    proc = run_hook(_payload(sid, active=True))
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert "decision" not in out
    assert "NOT complete" in out["systemMessage"]


def test_hook_fails_closed():
    assert run_hook((FIXTURES / "malformed_stdin.json").read_text()).returncode == 2
    assert run_hook({"session_id": "../../etc/passwd"}).returncode == 2
