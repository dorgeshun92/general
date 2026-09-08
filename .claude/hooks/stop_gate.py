#!/usr/bin/env python3
"""Stop hook: refuse to let a session finish with an incomplete or invalid audit run.

    python .claude/hooks/stop_gate.py < hook_input.json

Scope: only run directories touched during THIS session, i.e. the union of
  * run dirs recorded in output/.hook_state/<session_id>.json by the
    PreToolUse guard (Write/Edit/Bash calls that referenced output/audits/...),
  * run dirs under output/audits whose files were modified after the
    session's recorded start time (covers runs produced by scripts).
Sessions that never ran an audit have no state file and are never blocked.

For each run dir:
  * run_manifest.json must exist and be a JSON object,
  * every *.json whose basename maps to a schema must validate
    (via scripts/validate_schema.py in a subprocess),
  * every other *.json must parse as a JSON object.

Blocking output: {"decision": "block", "reason": "..."} on stdout, exit 0.
Any hook exception => exit 2 (also blocks; fail closed).

Loop guard: after MAX_CONSECUTIVE_BLOCKS blocks in one session the gate lets
the session stop but emits a systemMessage stating the run is NOT complete.
Set MAX_CONSECUTIVE_BLOCKS = None for strict mode (block forever).

Pure functions for tests: session_run_dirs(), check_run_dir(), evaluate().
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
from hook_common import HookError  # noqa: E402
from post_write_validate import run_validator  # noqa: E402

MAX_CONSECUTIVE_BLOCKS: int | None = 3


def _dir_mtime_max(path: Path) -> float:
    latest = 0.0
    try:
        latest = path.stat().st_mtime
    except OSError:
        return latest
    for p in path.rglob("*"):
        try:
            latest = max(latest, p.stat().st_mtime)
        except OSError:
            continue
    return latest


def session_run_dirs(state: dict[str, Any], repo_root: Path) -> list[str]:
    """Run dirs touched during the session (recorded + modified-since-start)."""
    found: set[str] = set(rd for rd in state.get("run_dirs", []) if isinstance(rd, str))
    started = state.get("started_at")
    audits = repo_root / hc.AUDIT_OUTPUT_ROOT
    if isinstance(started, (int, float)) and audits.is_dir():
        for loan_dir in audits.iterdir():
            if not loan_dir.is_dir() or loan_dir.name.startswith("."):
                continue
            run_candidates = [d for d in loan_dir.iterdir() if d.is_dir()]
            if not run_candidates:
                if _dir_mtime_max(loan_dir) >= started:
                    found.add(f"{hc.AUDIT_OUTPUT_ROOT}/{loan_dir.name}")
                continue
            for run_dir in run_candidates:
                if _dir_mtime_max(run_dir) >= started:
                    found.add(f"{hc.AUDIT_OUTPUT_ROOT}/{loan_dir.name}/{run_dir.name}")
    return sorted(found)


def check_run_dir(rel: str, repo_root: Path) -> list[str]:
    """Return a list of problems for one run directory (empty = complete and valid)."""
    problems: list[str] = []
    run_dir = repo_root / rel
    if not run_dir.is_dir():
        return [f"{rel}: run directory no longer exists (was touched this session)"]
    manifest = run_dir / hc.RUN_MANIFEST_NAME
    if not manifest.is_file():
        problems.append(f"{rel}: missing {hc.RUN_MANIFEST_NAME} (every run needs an audit manifest)")
    for json_path in sorted(run_dir.rglob("*.json")):
        sub = json_path.relative_to(repo_root).as_posix()
        schema = hc.SCHEMA_BY_FILENAME.get(json_path.name)
        if schema is None:
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                problems.append(f"{sub}: not valid JSON: {exc}")
                continue
            if not isinstance(data, dict):
                problems.append(f"{sub}: top-level JSON value must be an object")
            continue
        code, output = run_validator(json_path, schema, repo_root)
        if code == 1:
            problems.append(f"{sub}: INVALID against {schema}\n{output}")
        elif code != 0:
            raise HookError(f"validator errored (exit {code}) on {sub}: {output}")
    return problems


def evaluate(session_id: str | None, repo_root: Path | None = None) -> tuple[bool, list[str], list[str]]:
    """Return (should_block, problems, run_dirs_checked)."""
    repo_root = Path(repo_root) if repo_root else hc.REPO_ROOT
    if not session_id:
        return False, [], []
    state = hc.load_state(session_id, repo_root)
    if not state:
        return False, [], []
    run_dirs = session_run_dirs(state, repo_root)
    problems: list[str] = []
    for rd in run_dirs:
        problems.extend(check_run_dir(rd, repo_root))
    return bool(problems), problems, run_dirs


def main() -> int:
    payload = hc.read_stdin_json()
    sid = hc.session_id_of(payload)
    should_block, problems, run_dirs = evaluate(sid)
    if not should_block:
        if sid and run_dirs:
            state = hc.load_state(sid)
            if state.get("stop_blocks"):
                state["stop_blocks"] = 0
                hc.save_state(sid, state)
            print(f"stop_gate: {len(run_dirs)} run dir(s) complete and valid: {', '.join(run_dirs)}")
        return 0
    state = hc.load_state(sid) if sid else {}
    blocks = int(state.get("stop_blocks", 0)) + 1
    state["stop_blocks"] = blocks
    if sid:
        hc.save_state(sid, state)
    reason = (
        "stop_gate: the session touched audit run(s) that are incomplete or invalid. "
        "Fix these before finishing (write run_manifest.json; make every JSON validate):\n- "
        + "\n- ".join(problems)
    )
    if MAX_CONSECUTIVE_BLOCKS is not None and blocks > MAX_CONSECUTIVE_BLOCKS:
        hc.emit_json({
            "systemMessage": (
                f"STOP GATE: allowed the session to stop after {blocks - 1} blocked attempts, "
                f"but the audit run is NOT complete/valid. Do not treat it as finished.\n{reason}"
            )
        })
        return 0
    hc.emit_json({"decision": "block", "reason": reason})
    return 0


if __name__ == "__main__":
    hc.run_fail_closed(main, "stop_gate")
