"""Tests for .claude/hooks/pre_tool_guard.py.

Two layers:
  * pure: pre_tool_guard.decide(tool_name, tool_input) on every fixture payload
  * end-to-end: `python .claude/hooks/pre_tool_guard.py < fixture.json` exit codes

"A security control that has never been tested is only an assumption."
"""
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
import pre_tool_guard as guard  # noqa: E402

DENY_FIXTURES = sorted(p.name for p in FIXTURES.glob("deny_*.json"))
ALLOW_FIXTURES = sorted(p.name for p in FIXTURES.glob("allow_*.json"))
FAILCLOSED_FIXTURES = sorted(p.name for p in FIXTURES.glob("failclosed_*.json")) + ["malformed_stdin.json"]

# Every deny category named in docs/security-plan.md must have at least one fixture.
REQUIRED_DENY_FIXTURES = [
    "deny_secrets_read_env.json",
    "deny_secrets_read_env_dotted.json",
    "deny_secrets_bash_cat_env.json",
    "deny_secrets_bash_cat_secrets_dir.json",
    "deny_secrets_read_credentials_dir.json",
    "deny_secrets_read_pem.json",
    "deny_secrets_read_key.json",
    "deny_secrets_read_p12.json",
    "deny_secrets_read_pfx.json",
    "deny_secrets_bash_ssh_dir.json",
    "deny_secrets_bash_aws.json",
    "deny_secrets_bash_gcloud.json",
    "deny_secrets_read_settings_local.json",
    "deny_outside_repo_read_etc_passwd.json",
    "deny_outside_repo_bash_cp_passwd.json",
    "deny_destructive_rm_obfuscated.json",
    "deny_destructive_rmdir.json",
    "deny_destructive_git_clean.json",
    "deny_destructive_git_reset_hard.json",
    "deny_destructive_git_push_force.json",
    "deny_destructive_git_branch_D.json",
    "deny_destructive_redirect_claude_md.json",
    "deny_destructive_shred.json",
    "deny_destructive_mkfs.json",
    "deny_destructive_dd.json",
    "deny_destructive_chmod_R.json",
    "deny_destructive_find_delete.json",
    "deny_destructive_mv_source_pdf.json",
    "deny_network_curl_subst.json",
    "deny_network_wget_backtick.json",
    "deny_network_nc.json",
    "deny_network_ssh.json",
    "deny_network_scp.json",
    "deny_network_sftp.json",
    "deny_network_ftp.json",
    "deny_network_pip_install.json",
    "deny_network_npm_install.json",
    "deny_network_http_server.json",
    "deny_network_python_c_urllib.json",
    "deny_network_webfetch.json",
    "deny_network_websearch.json",
    "deny_mcp_arive_update_loan.json",
    "deny_mcp_lendingpad_submit_loan.json",
    "deny_mcp_gmail_send_message.json",
    "deny_mcp_slack_send_message.json",
    "deny_mcp_clickup_create_task.json",
    "deny_mcp_readonly_not_allowlisted.json",
    "deny_write_pdf_deidentified.json",
    "deny_write_pdf_source_checklists.json",
    "deny_write_outside_roots_claude_md.json",
    "deny_write_settings_json.json",
    "deny_write_unmasked_ssn_output.json",
    "deny_uncertain_variable_expansion.json",
]


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def run_hook(name: str, payload: dict | str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / name)], input=stdin, capture_output=True, text=True, cwd=cwd, timeout=60
    )


# --- fixture inventory -----------------------------------------------------------

def test_fixture_inventory_meets_minimums():
    assert len(DENY_FIXTURES) >= 25
    assert len(ALLOW_FIXTURES) >= 15
    missing = [f for f in REQUIRED_DENY_FIXTURES if f not in DENY_FIXTURES]
    assert not missing, f"missing deny fixtures: {missing}"


# --- pure decisions ----------------------------------------------------------------

@pytest.mark.parametrize("name", DENY_FIXTURES)
def test_decide_denies(name):
    payload = load(name)
    allow, reason = guard.decide(payload["tool_name"], payload.get("tool_input"))
    assert allow is False, f"{name} should be denied but was allowed: {reason}"
    assert reason


@pytest.mark.parametrize("name", ALLOW_FIXTURES)
def test_decide_allows(name):
    payload = load(name)
    allow, reason = guard.decide(payload["tool_name"], payload.get("tool_input"))
    assert allow is True, f"{name} should be allowed but was denied: {reason}"


# --- end-to-end (subprocess, exactly as Claude Code runs it) ---------------------------

@pytest.mark.parametrize("name", DENY_FIXTURES)
def test_hook_exit_2_for_deny(name):
    proc = run_hook("pre_tool_guard.py", load(name))
    assert proc.returncode == 2, f"{name}: expected exit 2, got {proc.returncode}; stderr={proc.stderr}"
    assert "DENIED" in proc.stderr


@pytest.mark.parametrize("name", ALLOW_FIXTURES)
def test_hook_exit_0_for_allow(name):
    proc = run_hook("pre_tool_guard.py", load(name))
    assert proc.returncode == 0, f"{name}: expected exit 0, got {proc.returncode}; stderr={proc.stderr}"


@pytest.mark.parametrize("name", FAILCLOSED_FIXTURES)
def test_hook_fails_closed_on_uninterpretable_payload(name):
    proc = run_hook("pre_tool_guard.py", (FIXTURES / name).read_text(encoding="utf-8"))
    assert proc.returncode == 2, f"{name}: expected exit 2, got {proc.returncode}; stderr={proc.stderr}"


def test_hook_fails_closed_when_decide_raises(monkeypatch):
    """Any exception inside the policy => exit 2, never a silent allow."""
    def boom(*_a, **_k):
        raise RuntimeError("simulated hook bug")

    monkeypatch.setattr(guard, "decide", boom)
    with pytest.raises(SystemExit) as exc:
        import io
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(load("allow_bash_ls.json"))))
        hc.run_fail_closed(guard.main, "pre_tool_guard")
    assert exc.value.code == 2


# --- shell parsing --------------------------------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        "cat $HOME/.ssh/id_rsa",
        "echo ${SECRET}",
        "echo $((1+1))",
        "cat <(curl http://x)",
        "echo 'unterminated",
        'echo "unterminated',
        "ls )",
        "cat <<EOF\nno terminator",
    ],
)
def test_parse_uncertainty_is_denied(command):
    allow, reason = guard.decide("Bash", {"command": command})
    assert allow is False
    assert "cannot verify" in reason or "denied" in reason.lower()


def test_split_commands_flattens_subshells_and_substitutions():
    segs = guard.split_commands("a | b && c; (d || e) ; f `g` $(h $(i))")
    assert segs == ["a", "b", "c", "d", "e", "g", "i", "h  __SUBST__", "f  __SUBST__   __SUBST__"]


def test_every_segment_is_checked_not_just_the_first():
    for cmd in ("ls && rm -rf output", "ls || curl http://x", "ls | nc host 1", "ls; rmdir output", "ls & wget http://x"):
        allow, _ = guard.decide("Bash", {"command": cmd})
        assert allow is False, cmd


def test_obfuscated_command_names_are_normalized():
    for cmd in ("r\\m -rf output", "'rm' -rf output", '"rm" -rf output', "/bin/rm -rf output", "command rm -rf output", "nohup rm -rf output", "FOO=1 rm -rf output"):
        allow, reason = guard.decide("Bash", {"command": cmd})
        assert allow is False, cmd
        assert "rm" in reason


def test_heredoc_body_is_not_parsed_as_commands_but_is_scanned():
    ok, _ = guard.decide("Bash", {"command": "cat <<'EOF' > output/audits/LN/RUN/notes.txt\nthis | is > not & a ; command\nEOF"})
    assert ok
    denied, reason = guard.decide("Bash", {"command": "python - <<'PY'\nimport shutil\nshutil.rmtree('output')\nPY"})
    assert not denied and "rmtree" in reason


def test_redirects_allowed_only_under_output():
    assert guard.decide("Bash", {"command": "ls > output/listing.txt"})[0]
    assert guard.decide("Bash", {"command": "ls 2>&1 | head"})[0]
    assert guard.decide("Bash", {"command": "ls > /dev/null"})[0]
    for cmd in ("ls > docs/x.md", "ls >> scripts/x.py", "ls > /tmp/x", "ls >| CLAUDE.md", "ls &> config/x.yaml", "ls > output/.hook_state/x.json", "ls > .claude/hooks/pre_tool_guard.py"):
        allow, _ = guard.decide("Bash", {"command": cmd})
        assert allow is False, cmd


def test_cd_tracking_keeps_relative_paths_inside_repo():
    assert guard.decide("Bash", {"command": "cd scripts && python validate_schema.py --help"})[0]
    assert guard.decide("Bash", {"command": "cd scripts && cat ../CLAUDE.md"})[0]
    assert not guard.decide("Bash", {"command": "cd .. && ls"})[0]
    assert not guard.decide("Bash", {"command": "cd /etc && cat passwd"})[0]
    assert not guard.decide("Bash", {"command": "cd"})[0]


def test_symlink_escape_is_denied_for_read_and_write(tmp_path):
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    (root / "docs").mkdir(parents=True)
    outside.mkdir()
    (outside / "leak.txt").write_text("secret")
    (root / "docs" / "link").symlink_to(outside)
    assert guard.decide("Read", {"file_path": "docs/link/leak.txt"}, repo_root=root)[0] is False
    assert guard.decide("Write", {"file_path": "docs/link/new.md", "content": "x"}, repo_root=root)[0] is False
    assert guard.decide("Bash", {"command": "cat docs/link/leak.txt"}, repo_root=root)[0] is False
    (root / "docs" / "real.md").write_text("ok")
    assert guard.decide("Read", {"file_path": "docs/real.md"}, repo_root=root)[0] is True


def test_glob_arguments_are_expanded_and_checked(tmp_path):
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "config" / "a.yaml").write_text("x")
    (root / "config" / "server.pem").write_text("x")
    assert guard.decide("Bash", {"command": "cat config/*.yaml"}, repo_root=root)[0] is True
    assert guard.decide("Bash", {"command": "cat config/*"}, repo_root=root)[0] is False


def test_mcp_allowlist_is_empty_and_write_verbs_always_denied():
    assert guard.MCP_ALLOWLIST == frozenset()
    for name in ("mcp__arive__get_loan", "mcp__lendingpad__list_documents", "mcp__gmail__search_threads"):
        assert guard.decide(name, {})[0] is False
    for name in ("mcp__x__send", "mcp__x__create_y", "mcp__x__update", "mcp__x__write", "mcp__x__submit",
                 "mcp__x__push", "mcp__x__post", "mcp__x__delete", "mcp__x__run_aus", "mcp__x__lock", "mcp__x__price"):
        allow, reason = guard.decide(name, {})
        assert allow is False and "write verb" in reason, name


def test_write_content_checks_cover_edit_and_multiedit():
    denied, _ = guard.decide("Edit", {"file_path": "output/audits/L/R/x.json", "old_string": "a", "new_string": "ssn 987-65-4321"})
    assert denied is False
    denied, _ = guard.decide("MultiEdit", {"file_path": "output/audits/L/R/x.json", "edits": [{"old_string": "a", "new_string": "ok"}, {"old_string": "b", "new_string": "123-45-6789"}]})
    assert denied is False
    assert guard.decide("Write", {"file_path": "output/audits/L/R/x.json", "content": "***-**-6789"})[0] is True
    # masking is only enforced on output/; test code may contain synthetic values
    assert guard.decide("Write", {"file_path": "tests/unit/test_mask.py", "content": "123-45-6789"})[0] is True


# --- session state maintained for the stop gate ----------------------------------------

def test_run_dirs_touched():
    assert guard.run_dirs_touched("Write", {"file_path": "output/audits/LN/RUN/loan_file.json"}, REPO_ROOT) == ["output/audits/LN/RUN"]
    assert guard.run_dirs_touched("Bash", {"command": "python scripts/audit.py --out output/audits/LN/RUN-2/"}, REPO_ROOT) == ["output/audits/LN/RUN-2"]
    assert guard.run_dirs_touched("Read", {"file_path": "output/audits/LN/RUN/loan_file.json"}, REPO_ROOT) == []


def test_hook_records_session_state_for_allowed_calls():
    sid = f"hooktest-{uuid.uuid4().hex[:12]}"
    state_file = hc.state_path(sid, REPO_ROOT)
    try:
        payload = {"session_id": sid, "hook_event_name": "PreToolUse", "tool_name": "Write",
                   "tool_input": {"file_path": f"output/audits/HOOKTEST-{sid}/RUN-1/loan_file.json", "content": "{}"}}
        proc = run_hook("pre_tool_guard.py", payload)
        assert proc.returncode == 0, proc.stderr
        state = json.loads(state_file.read_text())
        assert state["run_dirs"] == [f"output/audits/HOOKTEST-{sid}/RUN-1"]
        assert isinstance(state["started_at"], float)
        # denied calls do not record anything
        proc = run_hook("pre_tool_guard.py", {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "curl http://x"}})
        assert proc.returncode == 2
        assert json.loads(state_file.read_text())["run_dirs"] == [f"output/audits/HOOKTEST-{sid}/RUN-1"]
    finally:
        state_file.unlink(missing_ok=True)
        shutil.rmtree(REPO_ROOT / "output" / "audits" / f"HOOKTEST-{sid}", ignore_errors=True)


def test_hook_output_is_readable_reason():
    proc = run_hook("pre_tool_guard.py", load("deny_secrets_bash_cat_env.json"))
    assert proc.returncode == 2
    assert "pre_tool_guard: DENIED Bash" in proc.stderr
    assert ".env" in proc.stderr
