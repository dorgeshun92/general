#!/usr/bin/env python3
"""PostToolUse hook: validate JSON artifacts written under output/audits.

    python .claude/hooks/post_write_validate.py < hook_input.json

For Write/Edit/MultiEdit/NotebookEdit calls whose file_path lands under
output/audits/:
  * a basename listed in hook_common.SCHEMA_BY_FILENAME is validated by
    running `python scripts/validate_schema.py <file> --schema <name>` in a
    subprocess (never imported, so a broken validator cannot be silently
    skipped);
  * any other *.json (including run_manifest.json) must at least parse as a
    JSON object.

Exit 2 (block + feed stderr to Claude) when the file is invalid, when the
validator itself errors, or when the hook errors. Exit 0 otherwise. Files
outside output/audits are ignored.

Pure function for tests: validate_written_file(path, repo_root) -> (ok, message)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
from hook_common import HookError  # noqa: E402

VALIDATOR = "scripts/validate_schema.py"
VALIDATOR_TIMEOUT_S = 60


def run_validator(path: Path, schema: str, repo_root: Path) -> tuple[int, str]:
    """Run the repository validator in a subprocess. Returns (exit_code, combined_output)."""
    validator = repo_root / VALIDATOR
    if not validator.is_file():
        raise HookError(f"validator {VALIDATOR} not found under {repo_root}")
    proc = subprocess.run(
        [sys.executable, str(validator), str(path), "--schema", schema],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=VALIDATOR_TIMEOUT_S,
        check=False,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def validate_written_file(raw_path: str, repo_root: Path | None = None) -> tuple[bool, str]:
    """Return (ok, message). Raises HookError when validation cannot be performed."""
    repo_root = Path(repo_root) if repo_root else hc.REPO_ROOT
    path = hc.normalize_path(raw_path, repo_root)
    rel = hc.relative_to_repo(path, repo_root)
    if rel is None or not rel.startswith(hc.AUDIT_OUTPUT_ROOT + "/"):
        return True, f"{raw_path}: not under {hc.AUDIT_OUTPUT_ROOT}/; no validation required"
    if not rel.lower().endswith(".json"):
        return True, f"{rel}: not JSON; no validation required"
    if not path.is_file():
        raise HookError(f"{rel} does not exist after the write; cannot validate")
    schema = hc.SCHEMA_BY_FILENAME.get(path.name)
    if schema is None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return False, f"{rel}: not valid JSON: {exc}"
        if not isinstance(data, dict):
            return False, f"{rel}: top-level JSON value must be an object"
        return True, f"{rel}: parsed as a JSON object (no schema mapped to this filename)"
    code, output = run_validator(path, schema, repo_root)
    if code == 0:
        return True, f"{rel}: VALID against {schema}"
    if code == 1:
        return False, f"{rel}: INVALID against {schema}\n{output}"
    raise HookError(f"validator errored (exit {code}) on {rel}: {output}")


def main() -> int:
    payload = hc.read_stdin_json()
    tool_name = payload.get("tool_name")
    if tool_name not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return 0
    tool_input = hc.tool_input_of(payload)
    raw = tool_input.get("notebook_path" if tool_name == "NotebookEdit" else "file_path")
    if not isinstance(raw, str):
        raise HookError(f"{tool_name} payload has no file_path")
    ok, message = validate_written_file(raw)
    if not ok:
        hc.block(f"post_write_validate: {message}\nFix the file before reporting completion.")
        return 2
    print(f"post_write_validate: {message}")
    return 0


if __name__ == "__main__":
    hc.run_fail_closed(main, "post_write_validate")
