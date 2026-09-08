#!/usr/bin/env python3
"""Shared helpers for the Mpire Claude Code hooks.

Every hook script is `python .claude/hooks/<name>.py < hook_input.json`.

Contract (see docs/security-plan.md, "Hook contract"):
  * stdin  : one JSON object from Claude Code (session_id, hook_event_name,
             tool_name, tool_input, [tool_response], [stop_hook_active]).
  * exit 0 : allow. stdout may carry a JSON decision object.
  * exit 2 : BLOCK. stderr is fed back to Claude as the reason.
  * anything else: non-blocking error -- we never use it on purpose.

FAIL CLOSED: `run_fail_closed` converts *any* exception (malformed stdin,
bugs, missing files, unicode errors, ...) into exit 2 with a message.

Repository root is derived from this file's location, never from the
environment, so it cannot be redirected by a spoofed variable.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

REPO_ROOT: Path = Path(__file__).resolve().parents[2]

# --- Policy constants -------------------------------------------------------

# Path components / basenames that must never be read, written, copied, or
# listed. Matched with fnmatch against every component of a normalized path.
SECRET_COMPONENT_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.env",
    "secrets",
    "credentials",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.credentials.json",
    ".ssh",
    ".aws",
    "gcloud",              # only meaningful under .config; checked below too
    "settings.local.json",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_rsa*",
    "id_ed25519*",
)
# Absolute prefixes outside the repo that are read-only tool locations.
# The hook allows *reading* under these only (interpreter, stdlib, coreutils).
READ_ONLY_TOOL_PATHS: tuple[str, ...] = (
    "/usr/bin",
    "/usr/local/bin",
    "/bin",
    "/usr/lib/python3",
    "/usr/local/lib/python3",
    "/usr/lib64/python3",
    "/usr/share/doc",
    "/dev/null",
    "/dev/stdout",
    "/dev/stderr",
)
# Directories (relative to repo) where the Write/Edit tools may create files.
ALLOWED_WRITE_ROOTS: tuple[str, ...] = (
    "output",
    "tests",
    "scripts",
    "docs",
    "config",
    "schemas",
    ".claude",
)
# Source documents are never changed: no writes, no moves, no in-place edits.
SOURCE_DOCUMENT_ROOTS: tuple[str, ...] = (
    "tests/fixtures/deidentified",
    "docs/source-checklists",
)
# Files whose modification would change enforcement itself.
ENFORCEMENT_FILES: tuple[str, ...] = (
    ".claude/settings.json",
    ".claude/settings.local.json",
)
# Where derived audit runs live and where hook session state is kept.
AUDIT_OUTPUT_ROOT = "output/audits"
HOOK_STATE_DIR = "output/.hook_state"

# JSON basenames under output/audits that must validate against a schema.
SCHEMA_BY_FILENAME: dict[str, str] = {
    "loan_file.json": "loan_file",
    "document_inventory.json": "document_inventory",
    "preapproval_audit.json": "audit_result",
    "submission_readiness.json": "audit_result",
}
RUN_MANIFEST_NAME = "run_manifest.json"

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Unmasked US SSN (###-##-#### with separators). Masked forms (***-**-1234)
# do not match because they contain asterisks.
UNMASKED_SSN_RE = re.compile(r"(?<![0-9])[0-9]{3}-[0-9]{2}-[0-9]{4}(?![0-9])")


class HookError(RuntimeError):
    """Raised for conditions that must block (fail closed)."""


# --- stdin / stdout ---------------------------------------------------------

def read_stdin_json(stream=None) -> dict[str, Any]:
    """Parse the hook payload. Anything other than a JSON object is an error."""
    raw = (stream or sys.stdin).read()
    if not raw or not raw.strip():
        raise HookError("hook received empty stdin; expected a JSON object")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HookError(f"hook stdin is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HookError("hook stdin JSON must be an object")
    return data


def tool_input_of(payload: dict[str, Any]) -> dict[str, Any]:
    ti = payload.get("tool_input", {})
    if ti is None:
        ti = {}
    if not isinstance(ti, dict):
        raise HookError("tool_input must be a JSON object")
    return ti


def session_id_of(payload: dict[str, Any]) -> str | None:
    sid = payload.get("session_id")
    if sid is None:
        return None
    if not isinstance(sid, str) or not _SESSION_ID_RE.match(sid):
        raise HookError("session_id has an unexpected shape; refusing to use it as a filename")
    return sid


def block(reason: str, code: int = 2) -> "None":
    """Write the reason to stderr and exit with the blocking code."""
    sys.stderr.write(reason.rstrip() + "\n")
    sys.stderr.flush()
    sys.exit(code)


def emit_json(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def run_fail_closed(main: Callable[[], int | None], hook_name: str = "hook") -> None:
    """Run `main`; convert any exception into exit 2 (block)."""
    try:
        code = main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - fail closed on everything
        msg = f"{hook_name}: BLOCKED (fail closed) because the hook itself errored: {type(exc).__name__}: {exc}"
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
        sys.exit(2)
    sys.exit(0 if code is None else code)


# --- path helpers -----------------------------------------------------------

def _expand_user(raw: str) -> str:
    if raw == "~" or raw.startswith("~/") or raw.startswith("~\\"):
        return os.path.expanduser(raw)
    if raw.startswith("~"):  # ~otheruser/...
        return os.path.expanduser(raw)
    return raw


def normalize_path(raw: str, repo_root: Path | None = None, cwd: Path | None = None) -> Path:
    """Return an absolute, lexically-normalized path (``..`` and ``~`` resolved).

    Relative paths are anchored at `cwd` (default: repo root). Symlinks are
    NOT followed here; use `real_path` for that. Windows drive/UNC paths are
    returned as-is (they are outside the repo on a POSIX host anyway).
    """
    if not isinstance(raw, str):
        raise HookError(f"path must be a string, got {type(raw).__name__}")
    if "\x00" in raw:
        raise HookError("path contains a NUL byte")
    root = (repo_root or REPO_ROOT)
    base = cwd or root
    text = _expand_user(raw.strip())
    if text == "":
        raise HookError("empty path")
    p = Path(text)
    if not p.is_absolute():
        p = base / p
    return Path(os.path.normpath(str(p)))


def real_path(path: Path) -> Path:
    """Follow symlinks (for the existing prefix) without requiring existence."""
    return Path(os.path.realpath(str(path)))


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def relative_to_repo(path: Path, repo_root: Path | None = None) -> str | None:
    root = repo_root or REPO_ROOT
    if is_within(path, root):
        return PurePosixPath(path.relative_to(root)).as_posix()
    return None


def symlink_escapes(path: Path, root: Path) -> bool:
    """True when any existing component of `path` is a symlink leading outside `root`."""
    root_real = real_path(root)
    cur = path
    parts: list[Path] = []
    while True:
        parts.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    for candidate in reversed(parts):
        if not is_within(candidate, root):
            continue
        try:
            if candidate.is_symlink():
                target = real_path(candidate)
                if not is_within(target, root_real):
                    return True
        except OSError:
            return True  # cannot inspect => uncertain => escape
    # Final realpath check covers nested/relative symlink chains.
    try:
        return is_within(path, root) and not is_within(real_path(path), root_real)
    except OSError:
        return True


def matches_secret(path: Path, repo_root: Path | None = None) -> str | None:
    """Return the matching pattern if any component of `path` is a secret location."""
    parts = path.parts
    for idx, comp in enumerate(parts):
        for pat in SECRET_COMPONENT_PATTERNS:
            if pat == "gcloud":
                if comp == "gcloud" and idx > 0 and parts[idx - 1] == ".config":
                    return "~/.config/gcloud"
                continue
            if fnmatch.fnmatchcase(comp, pat):
                return pat
    return None


def under_read_only_tool_path(path: Path) -> bool:
    s = str(path)
    for prefix in READ_ONLY_TOOL_PATHS:
        if s == prefix or s.startswith(prefix + "/"):
            return True
        if prefix.endswith("python3") and s.startswith(prefix + "."):  # /usr/lib/python3.11/...
            return True
    return False


def is_source_document_path(rel: str) -> bool:
    for root in SOURCE_DOCUMENT_ROOTS:
        if rel == root or rel.startswith(root + "/"):
            return True
    return False


def is_pdf(rel_or_path: str) -> bool:
    return rel_or_path.lower().endswith(".pdf")


def run_dir_for(rel: str) -> str | None:
    """Map a repo-relative path under output/audits to its run directory.

    Layout is output/audits/<loan-id>/<run-id>/... ; a flat
    output/audits/<x>/file layout maps to output/audits/<x>.
    """
    prefix = AUDIT_OUTPUT_ROOT + "/"
    if not rel.startswith(prefix):
        return None
    tail = rel[len(prefix):].split("/")
    if len(tail) >= 3:
        return f"{AUDIT_OUTPUT_ROOT}/{tail[0]}/{tail[1]}"
    if len(tail) == 2:
        return f"{AUDIT_OUTPUT_ROOT}/{tail[0]}"
    return None


# --- session state (maintained by PreToolUse, read by Stop) -----------------

def state_path(session_id: str, repo_root: Path | None = None) -> Path:
    return (repo_root or REPO_ROOT) / HOOK_STATE_DIR / f"{session_id}.json"


def load_state(session_id: str, repo_root: Path | None = None) -> dict[str, Any]:
    p = state_path(session_id, repo_root)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise HookError(f"hook state file {p} is corrupt (not an object)")
    return data


def save_state(session_id: str, state: dict[str, Any], repo_root: Path | None = None) -> None:
    p = state_path(session_id, repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def record_session_activity(session_id: str, run_dirs: Iterable[str], repo_root: Path | None = None) -> bool:
    """Ensure the session has a start time and the given run dirs are recorded.

    Returns True when the state file was (re)written.
    """
    state = load_state(session_id, repo_root)
    changed = False
    if "started_at" not in state:
        state["started_at"] = time.time()
        changed = True
    existing = state.setdefault("run_dirs", [])
    for rd in run_dirs:
        if rd and rd not in existing:
            existing.append(rd)
            changed = True
    if changed:
        save_state(session_id, state, repo_root)
    return changed
