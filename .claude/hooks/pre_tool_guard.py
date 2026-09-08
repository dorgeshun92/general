#!/usr/bin/env python3
"""PreToolUse guard for the Mpire read-only MVP.

    python .claude/hooks/pre_tool_guard.py < hook_input.json

All policy lives in pure functions so it can be unit-tested:

    decide(tool_name, tool_input, repo_root=None) -> (allow: bool, reason: str)

Denies (see docs/security-plan.md for the full rule table):
  * reading secrets / credential stores / anything outside the repo
  * deletion and destructive shell, truncation redirects outside output/
  * outbound network (commands, inline python, WebFetch, WebSearch, MCP)
  * every mcp__* tool (allowlist is empty during MVP 1)
  * writes outside the allowed roots, to source documents, or to the
    enforcement files (.claude/settings*.json)
  * unmasked SSNs written into output/

Bash commands are parsed conservatively: pipes, ;, &&, ||, &, newlines,
subshells, $(...) and backticks are split into separate simple commands and
every one is checked. Variable expansion, process substitution, arithmetic,
unterminated quotes, and anything else we cannot parse => DENY.

Fail closed: any exception => exit 2.
"""
from __future__ import annotations

import fnmatch
import glob
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402
from hook_common import HookError  # noqa: E402

# --- policy tables ----------------------------------------------------------

MCP_ALLOWLIST: frozenset[str] = frozenset()  # intentionally empty in MVP 1
MCP_WRITE_VERBS = ("send", "create", "update", "write", "submit", "push", "post",
                   "delete", "run_aus", "lock", "price", "upload", "trash", "forward",
                   "reply", "merge", "publish", "schedule", "share", "grant", "remove")

DESTRUCTIVE_COMMANDS = {
    "rm", "rmdir", "shred", "dd", "truncate", "unlink", "wipefs", "fdisk", "parted",
    "mkfs", "format", "diskpart", "del", "erase", "rd", "Remove-Item", "ri",
}
NETWORK_COMMANDS = {
    "curl", "wget", "nc", "ncat", "netcat", "ssh", "scp", "sftp", "ftp", "telnet",
    "socat", "nmap", "ping", "dig", "nslookup", "host", "whois", "traceroute",
    "gh", "aws", "gcloud", "az", "heroku", "docker", "podman", "kubectl",
    "npm", "npx", "yarn", "pnpm", "bun", "cargo", "go", "gem", "composer",
    "apt", "apt-get", "yum", "dnf", "brew", "snap", "pacman", "choco", "winget",
    "Invoke-WebRequest", "iwr", "Invoke-RestMethod", "irm", "Start-BitsTransfer",
    "New-Object", "openssl", "mail", "sendmail", "mutt", "smtp",
}
PIP_LIKE = {"pip", "pip3", "pipx", "uv", "poetry", "conda", "easy_install"}
PIP_READ_ONLY_SUBCOMMANDS = {"list", "show", "freeze", "check", "--version", "-V", "help", "--help"}
PRIVILEGE_COMMANDS = {"sudo", "doas", "su", "pkexec", "runas"}
UNCERTAIN_COMMANDS = {"eval", "xdg-open", "open", "start", "crontab", "at", "history"}
ENV_DUMP_COMMANDS = {"printenv", "env", "set", "export", "declare", "typeset"}
INLINE_CODE_COMMANDS = {"node", "perl", "ruby", "php", "lua", "osascript", "powershell", "pwsh"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
PYTHONS = {"python", "python3", "python3.11", "python3.12", "python3.13", "py", "pypy", "pypy3"}
WRAPPERS_STRIP = {"nohup", "time", "command", "builtin", "exec", "nice", "ionice", "stdbuf", "timeout"}
SHELL_KEYWORDS_STRIP = {"if", "then", "else", "elif", "while", "until", "do", "{", "}", "!", "fi", "done", "esac", "in"}

DENY_PY_MODULES = {
    "pip", "ensurepip", "venv", "http.server", "http", "urllib", "urllib.request",
    "smtplib", "ftplib", "telnetlib", "webbrowser", "twine", "uvicorn", "gunicorn",
    "flask", "SimpleHTTPServer", "socket", "socketserver", "xmlrpc.server",
}
INLINE_NETWORK_RE = re.compile(
    r"\b(urllib|urllib3|requests|socket|socketserver|http\.client|http\.server|httpx|aiohttp|"
    r"ftplib|smtplib|imaplib|poplib|telnetlib|boto3|botocore|paramiko|websocket|websockets|"
    r"pycurl|xmlrpc|nntplib|grpc|pika|kafka|redis|pymongo|psycopg2|sqlalchemy|"
    r"fetch\s*\(|XMLHttpRequest|Net\.WebClient|WebRequest|LWP::|Net::HTTP)\b",
    re.IGNORECASE,
)
INLINE_DESTRUCTIVE_RE = re.compile(
    r"(shutil\.rmtree|os\.remove|os\.unlink|os\.rmdir|os\.removedirs|os\.system|os\.popen|"
    r"subprocess|\.unlink\s*\(|\.rmdir\s*\(|send2trash|os\.rename|os\.replace|shutil\.move|"
    r"os\.chmod|os\.chown|ctypes|importlib\.import_module|__import__|exec\s*\(|eval\s*\()",
)
INLINE_SECRET_RE = re.compile(r"(\.env\b|secrets/|credentials/|\.ssh|\.aws|gcloud|\.pem\b|\.key\b|\.p12\b|\.pfx\b|os\.environ|getenv)")

SOURCE_DOCUMENT_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".xlsx", ".xls", ".docx", ".doc")
BASH_WRITE_PROTECTED_ROOTS = hc.SOURCE_DOCUMENT_ROOTS + (".claude/hooks",)

READ_TOOLS = {"Read", "Grep", "Glob", "LS"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

_SUBST = "__SUBST__"
_HEREDOC_RE = re.compile(r"(?<!<)<<-?\s*(?:'([A-Za-z_][A-Za-z0-9_]*)'|\"([A-Za-z_][A-Za-z0-9_]*)\"|([A-Za-z_][A-Za-z0-9_]*))(?!<)")
_REDIRECT_OUT_RE = re.compile(r"^(\d*|&)(>>|>\||>)(.*)$")
_REDIRECT_IN_RE = re.compile(r"^(\d*)<(?!<)(.*)$")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_RUN_DIR_RE = re.compile(r"output/audits/([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)")


class Deny(Exception):
    """Raised inside the checker with the reason to deny."""


# --- shell parsing ----------------------------------------------------------

def _extract_heredocs(text: str) -> tuple[str, list[str]]:
    """Strip heredoc bodies from `text`; return (remaining_text, bodies)."""
    bodies: list[str] = []
    guard = 0
    while True:
        guard += 1
        if guard > 50:
            raise HookError("too many heredocs")
        m = _HEREDOC_RE.search(text)
        if not m:
            return text, bodies
        word = m.group(1) or m.group(2) or m.group(3)
        nl = text.find("\n", m.end())
        if nl < 0:
            raise HookError(f"heredoc <<{word} has no body/terminator; cannot verify it")
        head = text[: m.start()] + text[m.end():nl]
        rest = text[nl + 1:]
        lines = rest.split("\n")
        body_lines: list[str] = []
        end_idx = None
        for i, line in enumerate(lines):
            if line.strip() == word or line == word:
                end_idx = i
                break
            body_lines.append(line)
        if end_idx is None:
            raise HookError(f"heredoc <<{word} is not terminated; cannot verify it")
        bodies.append("\n".join(body_lines))
        text = head + "\n" + "\n".join(lines[end_idx + 1:])


def _match_paren(text: str, start: int) -> int:
    """text[start] == '(' ; return index just past the matching ')'."""
    depth = 0
    quote = None
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < n:
                i += 2
                continue
            if c == quote:
                quote = None
        elif c == "\\" and i + 1 < n:
            i += 2
            continue
        elif c in "'\"":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise HookError("unbalanced parenthesis in command")


def split_commands(text: str, _depth: int = 0) -> list[str]:
    """Split a shell command line into simple-command strings.

    Contents of $(...), `...` and (...) are returned as their own entries
    (recursively) and replaced with a placeholder in the enclosing command.
    Raises HookError on anything that cannot be verified.
    """
    if _depth > 20:
        raise HookError("command nesting too deep")
    if "\x00" in text:
        raise HookError("NUL byte in command")
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    n = len(text)

    def flush() -> None:
        s = "".join(buf).strip()
        buf.clear()
        if s:
            segments.append(s)

    while i < n:
        c = text[i]
        if quote == "'":
            buf.append(c)
            if c == "'":
                quote = None
            i += 1
            continue
        if quote == '"':
            if c == "\\" and i + 1 < n:
                buf.append(text[i:i + 2])
                i += 2
                continue
            if c == '"':
                quote = None
                buf.append(c)
                i += 1
                continue
            if c == "$":
                if text.startswith("$((", i):
                    raise HookError("arithmetic expansion cannot be verified")
                if text.startswith("$(", i):
                    end = _match_paren(text, i + 1)
                    segments.extend(split_commands(text[i + 2:end - 1], _depth + 1))
                    buf.append(" " + _SUBST + " ")
                    i = end
                    continue
                raise HookError("variable expansion cannot be verified; use literal paths and values")
            if c == "`":
                j = text.find("`", i + 1)
                if j < 0:
                    raise HookError("unterminated backtick")
                segments.extend(split_commands(text[i + 1:j], _depth + 1))
                buf.append(" " + _SUBST + " ")
                i = j + 1
                continue
            buf.append(c)
            i += 1
            continue
        # unquoted context
        if c == "\\" and i + 1 < n:
            buf.append(text[i:i + 2])
            i += 2
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "$":
            if text.startswith("$((", i):
                raise HookError("arithmetic expansion cannot be verified")
            if text.startswith("$(", i):
                end = _match_paren(text, i + 1)
                segments.extend(split_commands(text[i + 2:end - 1], _depth + 1))
                buf.append(" " + _SUBST + " ")
                i = end
                continue
            raise HookError("variable expansion cannot be verified; use literal paths and values")
        if c == "`":
            j = text.find("`", i + 1)
            if j < 0:
                raise HookError("unterminated backtick")
            segments.extend(split_commands(text[i + 1:j], _depth + 1))
            buf.append(" " + _SUBST + " ")
            i = j + 1
            continue
        if c in ("<", ">") and text.startswith(c + "(", i):
            raise HookError("process substitution cannot be verified")
        if c == "(":
            end = _match_paren(text, i)
            flush()
            segments.extend(split_commands(text[i + 1:end - 1], _depth + 1))
            i = end
            continue
        if c == ")":
            raise HookError("unbalanced parenthesis in command")
        if c in (";", "\n"):
            flush()
            i += 1
            continue
        if c == "|":
            flush()
            i += 2 if text.startswith("||", i) else 1
            continue
        if c == "&":
            if text.startswith("&&", i):
                flush()
                i += 2
                continue
            if text.startswith("&>", i):
                buf.append("&>")
                i += 2
                continue
            if buf and buf[-1].endswith(">"):
                buf.append("&")
                i += 1
                continue
            flush()
            i += 1
            continue
        buf.append(c)
        i += 1
    if quote:
        raise HookError("unterminated quote in command")
    flush()
    return segments


def tokenize(segment: str) -> list[str]:
    try:
        toks = shlex.split(segment, posix=True, comments=False)
    except ValueError as exc:
        raise HookError(f"cannot tokenize command segment: {exc}") from exc
    return [t for t in toks if t.strip() != ""]


# --- path checks ------------------------------------------------------------

def _check_read_path(path: Path, repo_root: Path, label: str) -> None:
    hit = hc.matches_secret(path, repo_root)
    if hit:
        raise Deny(f"{label} '{path}' matches protected secret/credential pattern '{hit}'")
    if hc.is_within(path, repo_root):
        if hc.symlink_escapes(path, repo_root):
            raise Deny(f"{label} '{path}' resolves through a symlink to outside the repository")
        return
    if hc.under_read_only_tool_path(path):
        return
    raise Deny(f"{label} '{path}' is outside the repository and not an approved read-only tool path")


def _check_write_path(path: Path, repo_root: Path, label: str, content: str | None = None) -> str:
    """Validate a Write/Edit target. Returns the repo-relative path."""
    hit = hc.matches_secret(path, repo_root)
    if hit:
        raise Deny(f"{label} '{path}' matches protected secret/credential pattern '{hit}'")
    if not hc.is_within(path, repo_root):
        raise Deny(f"{label} '{path}' is outside the repository")
    if hc.symlink_escapes(path, repo_root):
        raise Deny(f"{label} '{path}' resolves through a symlink to outside the repository")
    rel = hc.relative_to_repo(path, repo_root) or ""
    if rel in hc.ENFORCEMENT_FILES:
        raise Deny(f"{label} '{rel}' is an enforcement file; only a human activates or edits it")
    if hc.is_source_document_path(rel) and rel.lower().endswith(SOURCE_DOCUMENT_EXTENSIONS):
        raise Deny(f"{label} '{rel}' is a source document; source documents are never changed")
    if rel.startswith("output/.hook_state"):
        raise Deny(f"{label} '{rel}' is hook state; only the hooks write it")
    top = rel.split("/")[0]
    if top not in hc.ALLOWED_WRITE_ROOTS:
        raise Deny(f"{label} '{rel}' is outside the allowed write roots {hc.ALLOWED_WRITE_ROOTS}")
    if content and rel.startswith("output/") and hc.UNMASKED_SSN_RE.search(content):
        raise Deny(f"{label} '{rel}' would write an unmasked SSN into output/; mask it first")
    return rel


def _is_pathlike(token: str, cwd: Path) -> bool:
    if token in ("-", "--", "."):
        return token == "."
    if token.startswith("~") or token.startswith("/") or token.startswith("."):
        return True
    if "/" in token or "\\" in token:
        return True
    if re.match(r"^[A-Za-z]:", token):
        return True
    for pat in hc.SECRET_COMPONENT_PATTERNS:
        if pat != "gcloud" and fnmatch.fnmatchcase(token, pat):
            return True
    try:
        return (cwd / token).exists()
    except OSError:
        return True


def _expand_candidates(token: str, repo_root: Path, cwd: Path) -> list[Path]:
    base = hc.normalize_path(token, repo_root, cwd)
    if any(ch in token for ch in "*?["):
        matches = glob.glob(str(base), recursive=True)
        if matches:
            return [Path(os.path.normpath(m)) for m in matches]
    return [base]


def _check_arg_paths(args: list[str], repo_root: Path, cwd: Path) -> None:
    for tok in args:
        if _SUBST in tok:
            continue
        cand = tok
        if tok.startswith("-") and "=" in tok:
            cand = tok.split("=", 1)[1]
        if cand.startswith("-") or cand == "":
            continue
        if not _is_pathlike(cand, cwd):
            continue
        for p in _expand_candidates(cand, repo_root, cwd):
            _check_read_path(p, repo_root, "path argument")


def _rel(path: Path, repo_root: Path) -> str:
    return hc.relative_to_repo(path, repo_root) or str(path)


def _is_bash_write_protected(rel: str) -> bool:
    if rel in hc.ENFORCEMENT_FILES:
        return True
    for root in BASH_WRITE_PROTECTED_ROOTS:
        if rel == root or rel.startswith(root + "/"):
            return True
    return False


def _check_bash_write_target(token: str, repo_root: Path, cwd: Path, what: str) -> None:
    """Shell-level writes (redirects, tee) may only land under output/."""
    if _SUBST in token:
        raise Deny(f"{what} target comes from a command substitution and cannot be verified")
    if token.startswith("&"):
        return  # >&1, >&2
    if token in ("/dev/null", "/dev/stdout", "/dev/stderr"):
        return
    p = hc.normalize_path(token, repo_root, cwd)
    hit = hc.matches_secret(p, repo_root)
    if hit:
        raise Deny(f"{what} onto '{p}' matches protected pattern '{hit}'")
    if not hc.is_within(p, repo_root) or hc.symlink_escapes(p, repo_root):
        raise Deny(f"{what} onto '{p}' is outside the repository")
    rel = _rel(p, repo_root)
    if not (rel == "output" or rel.startswith("output/")) or rel.startswith("output/.hook_state"):
        raise Deny(f"{what} onto '{rel}' is denied: shell writes may only target output/ (use the Write tool for source files)")


def _check_protected_destination(token: str, repo_root: Path, cwd: Path, cmd: str) -> None:
    if _SUBST in token:
        raise Deny(f"{cmd} destination comes from a command substitution and cannot be verified")
    p = hc.normalize_path(token, repo_root, cwd)
    rel = _rel(p, repo_root)
    if _is_bash_write_protected(rel):
        raise Deny(f"{cmd} onto '{rel}' is denied: source documents and enforcement files are never changed")


# --- per-command checks -----------------------------------------------------

def _scan_inline_code(code: str, origin: str) -> None:
    m = INLINE_NETWORK_RE.search(code)
    if m:
        raise Deny(f"{origin} uses network module/function '{m.group(0)}'; outbound network is denied in the read-only MVP")
    m = INLINE_DESTRUCTIVE_RE.search(code)
    if m:
        raise Deny(f"{origin} uses '{m.group(0)}', which can delete/modify files or run subprocesses; denied")
    m = INLINE_SECRET_RE.search(code)
    if m:
        raise Deny(f"{origin} references '{m.group(0)}', which may expose secrets; denied")


def _split_redirects(tokens: list[str], repo_root: Path, cwd: Path) -> list[str]:
    """Validate redirections and return the tokens with redirections removed."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        m = _REDIRECT_OUT_RE.match(tok)
        if m and tok not in ("&",):
            target = m.group(3)
            if target == "":
                i += 1
                if i >= len(tokens):
                    raise HookError("redirect without a target")
                target = tokens[i]
            _check_bash_write_target(target, repo_root, cwd, "redirect")
            i += 1
            continue
        if tok.startswith("<<<"):
            i += 1
            if tok == "<<<":
                i += 1
            continue
        m = _REDIRECT_IN_RE.match(tok)
        if m:
            target = m.group(2)
            if target == "":
                i += 1
                if i >= len(tokens):
                    raise HookError("input redirect without a target")
                target = tokens[i]
            if _SUBST in target:
                raise Deny("input redirect source comes from a command substitution and cannot be verified")
            _check_read_path(hc.normalize_path(target, repo_root, cwd), repo_root, "input redirect")
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def _check_git(args: list[str]) -> None:
    sub = None
    opts: list[str] = []
    for a in args:
        if sub is None and not a.startswith("-"):
            sub = a
            continue
        opts.append(a)
    if sub is None:
        return
    joined = " ".join(opts)
    if sub == "clean":
        raise Deny("git clean deletes untracked files; denied")
    if sub == "reset" and ("--hard" in opts or "--merge" in opts):
        raise Deny("git reset --hard discards work; denied")
    if sub == "push":
        if any(o in ("-f", "--force", "--force-with-lease", "--force-if-includes", "--delete", "-d") or o.startswith("+") for o in opts):
            raise Deny("git push --force / delete rewrites remote history; denied")
        raise Deny("git push is outbound network; denied during the read-only MVP (a human pushes)")
    if sub in ("fetch", "pull", "clone", "ls-remote", "submodule", "svn", "request-pull", "send-email"):
        raise Deny(f"git {sub} is outbound network; denied during the read-only MVP")
    if sub == "branch" and any(o in ("-D", "-d", "--delete") or (o.startswith("-") and "D" in o and not o.startswith("--")) for o in opts):
        raise Deny("git branch -D/-d deletes branches; denied")
    if sub in ("rm", "filter-branch", "filter-repo", "reflog", "prune", "gc"):
        raise Deny(f"git {sub} is destructive; denied")
    if sub == "checkout" and ("--" in opts or "." in opts or "-f" in opts or "--force" in opts):
        raise Deny("git checkout -- / . / --force discards working changes; denied")
    if sub == "restore":
        raise Deny("git restore discards working changes; denied")
    if sub == "stash" and any(o in ("drop", "clear", "pop") for o in opts):
        raise Deny(f"git stash {joined} can destroy work; denied")
    if sub == "worktree" and "remove" in opts:
        raise Deny("git worktree remove deletes files; denied")


def _check_find(args: list[str], repo_root: Path, cwd: Path) -> None:
    if "-delete" in args:
        raise Deny("find -delete deletes files; denied")
    for i, a in enumerate(args):
        if a in ("-exec", "-execdir", "-ok", "-okdir"):
            sub: list[str] = []
            for b in args[i + 1:]:
                if b in (";", "+", "\\;"):
                    break
                sub.append(b)
            if sub:
                check_tokens(sub, repo_root, cwd)


def _check_python(args: list[str], repo_root: Path, cwd: Path, heredoc_bodies: list[str]) -> None:
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-c":
            code = args[i + 1] if i + 1 < len(args) else ""
            _scan_inline_code(code, "inline python (-c)")
            return
        if a == "-m":
            mod = args[i + 1] if i + 1 < len(args) else ""
            if mod in DENY_PY_MODULES or mod.split(".")[0] in {"pip", "http", "urllib", "smtplib", "ftplib", "socket"}:
                raise Deny(f"python -m {mod} is network/installer functionality; denied in the read-only MVP")
            if mod == "pytest" or mod.startswith("scripts.") or mod in ("json.tool", "unittest", "pydoc", "doctest", "timeit", "compileall", "py_compile"):
                return
            raise Deny(f"python -m {mod} is not on the module allowlist (pytest, scripts.*, json.tool, unittest)")
        if a == "-":
            for body in heredoc_bodies:
                _scan_inline_code(body, "python script on stdin")
            return
        if a in ("-W", "-X", "--check-hash-based-pycs"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return  # script path: covered by the generic path check
    for body in heredoc_bodies:
        _scan_inline_code(body, "python script on stdin")


def _unwrap(tokens: list[str]) -> list[str]:
    """Strip shell keywords, env assignments, and benign wrappers."""
    toks = list(tokens)
    changed = True
    while changed and toks:
        changed = False
        if toks[0] in SHELL_KEYWORDS_STRIP:
            toks.pop(0)
            changed = True
            continue
        if _ENV_ASSIGN_RE.match(toks[0]):
            toks.pop(0)
            changed = True
            continue
        if toks[0] in WRAPPERS_STRIP:
            toks.pop(0)
            if toks and toks[0].startswith("-") and toks[0] not in ("-",):
                # `timeout 30 cmd`, `nice -n 5 cmd`: drop option and (numeric) value
                toks.pop(0)
                if toks and re.match(r"^\d+[smhd]?$", toks[0]):
                    toks.pop(0)
            elif toks and re.match(r"^\d+[smhd]?$", toks[0]):
                toks.pop(0)
            changed = True
            continue
        if toks[0] == "env":
            toks.pop(0)
            while toks and (_ENV_ASSIGN_RE.match(toks[0]) or toks[0] in ("-i", "-u", "--ignore-environment")):
                if toks[0] == "-u":
                    toks.pop(0)
                toks.pop(0)
            if not toks:
                raise Deny("bare `env` dumps the environment (may contain secrets); denied")
            changed = True
            continue
        if toks[0] == "xargs":
            toks.pop(0)
            while toks and toks[0].startswith("-"):
                flag = toks.pop(0)
                if flag in ("-n", "-I", "-P", "-L", "-d", "-s", "-E", "-a", "--max-args", "--replace", "--delimiter") and toks:
                    toks.pop(0)
            if not toks:
                raise Deny("xargs without an explicit command cannot be verified; denied")
            changed = True
            continue
    return toks


def check_tokens(tokens: list[str], repo_root: Path, cwd: Path, heredoc_bodies: list[str] | None = None) -> Path:
    """Check one simple command. Returns the (possibly updated) cwd."""
    heredoc_bodies = heredoc_bodies or []
    tokens = _split_redirects(tokens, repo_root, cwd)
    tokens = _unwrap(tokens)
    if not tokens:
        return cwd
    if tokens[0] in ("for", "select", "case", "function"):
        _check_arg_paths(tokens[1:], repo_root, cwd)
        return cwd
    head = tokens[0]
    if _SUBST in head:
        raise Deny("command name comes from a command substitution and cannot be verified")
    if head.startswith("-"):
        raise Deny(f"command segment starts with an option '{head}'; cannot verify")
    if "/" in head or "\\" in head:
        _check_read_path(hc.normalize_path(head, repo_root, cwd), repo_root, "executable path")
        cmd = os.path.basename(head)
    else:
        cmd = head
    args = tokens[1:]

    if cmd in PRIVILEGE_COMMANDS:
        raise Deny(f"'{cmd}' escalates privileges; denied")
    if cmd in UNCERTAIN_COMMANDS:
        raise Deny(f"'{cmd}' cannot be verified by the hook; denied")
    if cmd in ENV_DUMP_COMMANDS:
        dumps = (
            not args
            or (cmd == "export" and any(a in ("-p",) for a in args))
            or (cmd in ("declare", "typeset") and any(a in ("-p", "-x") for a in args) and len(args) == 1)
            or (cmd == "set" and not all(a.startswith(("-", "+")) for a in args))
            or cmd in ("printenv", "env")
        )
        if dumps:
            raise Deny(f"'{cmd}' can dump environment variables (may contain secrets); denied")
    if cmd in DESTRUCTIVE_COMMANDS or cmd.startswith("mkfs"):
        raise Deny(f"'{cmd}' is a deletion/destructive command; denied (derived files under output/ are regenerated per run)")
    if cmd in NETWORK_COMMANDS:
        raise Deny(f"'{cmd}' is an outbound-network/installer command; denied during the read-only MVP")
    if cmd in PIP_LIKE:
        if not args or args[0] not in PIP_READ_ONLY_SUBCOMMANDS:
            raise Deny(f"'{cmd} {' '.join(args[:1])}' installs or downloads packages; denied during the read-only MVP")
        return cwd
    if cmd in INLINE_CODE_COMMANDS:
        raise Deny(f"'{cmd}' runs inline code the hook cannot verify; denied")
    if cmd in ("chmod", "chown", "chgrp") and any(a in ("-R", "--recursive") or (a.startswith("-") and not a.startswith("--") and "R" in a) for a in args):
        raise Deny(f"'{cmd} -R' recursively changes permissions/ownership; denied")
    if cmd == "git":
        _check_git(args)
    if cmd == "find":
        _check_find(args, repo_root, cwd)
    if cmd in SHELLS:
        if "-c" in args:
            code = args[args.index("-c") + 1] if args.index("-c") + 1 < len(args) else ""
            check_command(code, repo_root, cwd)
        elif any(a in ("-s",) for a in args):
            raise Deny(f"'{cmd} -s' executes stdin; cannot verify")
    if cmd in PYTHONS:
        _check_python(args, repo_root, cwd, heredoc_bodies)
    if cmd == "mv":
        for a in args:
            if not a.startswith("-"):
                if a.lower().endswith(".pdf"):
                    raise Deny(f"mv of a PDF ('{a}') is denied: source documents are never moved")
                _check_protected_destination(a, repo_root, cwd, "mv")
    if cmd in ("cp", "ln", "install", "rsync"):
        if cmd == "rsync" and any(":" in a and not a.startswith("-") for a in args):
            raise Deny("rsync to/from a remote host is outbound network; denied")
        dests = [a for a in args if not a.startswith("-")]
        if dests:
            _check_protected_destination(dests[-1], repo_root, cwd, cmd)
    if cmd in ("touch", "truncate", "mkdir"):
        for a in args:
            if not a.startswith("-"):
                _check_protected_destination(a, repo_root, cwd, cmd)
    if cmd == "tee":
        for a in args:
            if not a.startswith("-"):
                _check_bash_write_target(a, repo_root, cwd, "tee")
    if cmd in ("sed", "perl") and any(a == "-i" or a.startswith("-i") or a == "--in-place" for a in args):
        for a in args:
            if not a.startswith("-"):
                _check_protected_destination(a, repo_root, cwd, f"{cmd} -i")
    if cmd in ("source", "."):
        pass  # path check below denies sourcing secrets
    for body in heredoc_bodies:
        if cmd not in PYTHONS:
            _scan_inline_code(body, "heredoc body")

    _check_arg_paths(args, repo_root, cwd)

    if cmd == "cd":
        target = next((a for a in args if not a.startswith("-")), None)
        if target is None:
            raise Deny("bare `cd` changes to $HOME (outside the repository); denied")
        new_cwd = hc.normalize_path(target, repo_root, cwd)
        if not hc.is_within(new_cwd, repo_root):
            raise Deny(f"cd '{new_cwd}' leaves the repository; denied")
        return new_cwd
    return cwd


def check_command(command: str, repo_root: Path, cwd: Path | None = None) -> None:
    """Raise Deny/HookError if `command` violates policy."""
    if not isinstance(command, str):
        raise HookError("Bash command must be a string")
    if command.strip() == "":
        return
    text, bodies = _extract_heredocs(command)
    cwd = cwd or repo_root
    for seg in split_commands(text):
        toks = tokenize(seg)
        toks = [t for t in toks if t != _SUBST]
        if not toks:
            continue
        cwd = check_tokens(toks, repo_root, cwd, bodies)


# --- tool-level decision -----------------------------------------------------

def _mcp_decision(tool_name: str) -> tuple[bool, str]:
    lowered = tool_name.lower()
    for verb in MCP_WRITE_VERBS:
        if verb in lowered:
            return False, f"MCP tool '{tool_name}' contains write verb '{verb}': external writes (LOS, email, SMS, AUS, TRID, pricing, submission) are denied in every release without human approval"
    if tool_name in MCP_ALLOWLIST:
        return True, f"MCP tool '{tool_name}' is on the read-only allowlist"
    return False, f"MCP tool '{tool_name}' is not on the MCP allowlist (empty during the read-only MVP); denied"


def decide(tool_name: str, tool_input: dict[str, Any] | None, repo_root: Path | None = None) -> tuple[bool, str]:
    """Pure policy decision. Never raises on policy violations; raises HookError on bad input."""
    repo_root = Path(repo_root) if repo_root else hc.REPO_ROOT
    if not isinstance(tool_name, str) or not tool_name:
        raise HookError("tool_name must be a non-empty string")
    if tool_input is None:
        tool_input = {}
    if not isinstance(tool_input, dict):
        raise HookError("tool_input must be a JSON object")
    try:
        if tool_name in ("WebFetch", "WebSearch"):
            return False, f"{tool_name} is outbound network; denied during the read-only MVP"
        if tool_name.startswith("mcp__"):
            return _mcp_decision(tool_name)
        if tool_name == "Bash":
            command = tool_input.get("command")
            if not isinstance(command, str):
                raise HookError("Bash tool_input.command must be a string")
            check_command(command, repo_root)
            return True, "bash command passed all checks"
        if tool_name in READ_TOOLS:
            key = "file_path" if tool_name == "Read" else "path"
            raw = tool_input.get(key)
            if raw is None and tool_name != "Read":
                return True, f"{tool_name} without a path is scoped to the working directory"
            if not isinstance(raw, str):
                raise HookError(f"{tool_name} tool_input.{key} must be a string")
            _check_read_path(hc.normalize_path(raw, repo_root), repo_root, f"{tool_name} path")
            return True, f"{tool_name} path is inside the repository and not protected"
        if tool_name in WRITE_TOOLS:
            key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
            raw = tool_input.get(key)
            if not isinstance(raw, str):
                raise HookError(f"{tool_name} tool_input.{key} must be a string")
            content_parts: list[str] = []
            for k in ("content", "new_string", "new_source"):
                v = tool_input.get(k)
                if isinstance(v, str):
                    content_parts.append(v)
            edits = tool_input.get("edits")
            if isinstance(edits, list):
                for e in edits:
                    if isinstance(e, dict) and isinstance(e.get("new_string"), str):
                        content_parts.append(e["new_string"])
            rel = _check_write_path(hc.normalize_path(raw, repo_root), repo_root, f"{tool_name} path", "\n".join(content_parts))
            return True, f"{tool_name} to '{rel}' is inside an allowed write root"
        return False, f"tool '{tool_name}' is not covered by the security plan; denied by default"
    except Deny as exc:
        return False, str(exc)
    except HookError as exc:
        return False, f"cannot verify: {exc}"


def run_dirs_touched(tool_name: str, tool_input: dict[str, Any], repo_root: Path) -> list[str]:
    """Run directories under output/audits referenced by an allowed tool call."""
    found: list[str] = []
    if tool_name in WRITE_TOOLS:
        raw = tool_input.get("notebook_path" if tool_name == "NotebookEdit" else "file_path")
        if isinstance(raw, str):
            rel = hc.relative_to_repo(hc.normalize_path(raw, repo_root), repo_root)
            rd = hc.run_dir_for(rel) if rel else None
            if rd:
                found.append(rd)
    elif tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if isinstance(cmd, str):
            for m in _RUN_DIR_RE.finditer(cmd):
                found.append(f"{hc.AUDIT_OUTPUT_ROOT}/{m.group(1)}/{m.group(2)}")
    return sorted(set(found))


def main() -> int:
    payload = hc.read_stdin_json()
    tool_name = payload.get("tool_name")
    tool_input = hc.tool_input_of(payload)
    if not isinstance(tool_name, str):
        raise HookError("payload has no tool_name")
    allow, reason = decide(tool_name, tool_input)
    if not allow:
        hc.block(f"pre_tool_guard: DENIED {tool_name}: {reason}")
        return 2
    sid = hc.session_id_of(payload)
    if sid:
        hc.record_session_activity(sid, run_dirs_touched(tool_name, tool_input, hc.REPO_ROOT))
    return 0


if __name__ == "__main__":
    hc.run_fail_closed(main, "pre_tool_guard")
