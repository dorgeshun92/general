"""MANIFEST.yaml loading, approved-location enforcement, and the live-PII heuristic.

Every loan directory the read-only MVP reads must:
  * live under one of ``approved_input_roots`` in config/approved_data_locations.yaml
    (real paths are compared, so a symlink that escapes the approved tree is refused), and
  * contain ``MANIFEST.yaml`` with::

        loan_id: LN-EDGE-CLEAN            # ^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$
        deidentified: true                # must be the YAML boolean true, nothing else
        deidentified_by: "fixture generator"
        deidentified_at: "2026-09-08"     # ISO date (YYYY-MM-DD)
        description: "optional free text"
        pii_pattern_allowlist:            # optional; see looks_like_live_pii()
          - "12345678"

Any failure raises :class:`IntakeStopCondition` with a human-readable reason. Callers
(inventory.py, the intake skill, the orchestrator) must stop, print the reason, and never
"continue past a stop condition by guessing".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import yaml

from scripts.common.hashing import sha256_file
from scripts.common.masking import LONG_DIGITS_RE, SSN_RE, mask_account

REPO_ROOT = Path(__file__).resolve().parents[2]
APPROVED_LOCATIONS_CONFIG = REPO_ROOT / "config" / "approved_data_locations.yaml"
MANIFEST_FILENAME = "MANIFEST.yaml"

LOAN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class IntakeStopCondition(Exception):
    """A condition under which intake must stop without guessing. ``reason`` is printable."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Manifest:
    loan_id: str
    deidentified: bool
    deidentified_by: str
    deidentified_at: str
    description: str | None
    pii_pattern_allowlist: tuple[str, ...]
    path: Path
    sha256: str
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


# --------------------------------------------------------------------------------------
# Approved locations
# --------------------------------------------------------------------------------------

def load_approved_input_roots(config_path: Path = APPROVED_LOCATIONS_CONFIG) -> list[Path]:
    """Resolved (real) paths of ``approved_input_roots``; relative entries are repo-relative."""
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise IntakeStopCondition(f"cannot read approved data locations {config_path}: {exc}") from exc
    roots = data.get("approved_input_roots") or []
    if not isinstance(roots, list) or not roots:
        raise IntakeStopCondition(f"{config_path} declares no approved_input_roots; refusing to read anything")
    resolved = []
    for entry in roots:
        p = Path(str(entry))
        if not p.is_absolute():
            p = REPO_ROOT / p
        resolved.append(p.resolve())
    return resolved


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def check_approved_location(loan_dir: Path, approved_roots: Iterable[Path] | None = None) -> Path:
    """Return the resolved loan directory if it is a real directory under an approved root.

    Real paths are compared on both sides, so ``approved/LN-X -> /elsewhere/LN-X`` symlinks and
    ``..`` tricks are refused. Callers must use the returned resolved path for all file access.
    """
    roots = list(approved_roots) if approved_roots is not None else load_approved_input_roots()
    roots = [Path(r).resolve() for r in roots]
    given = Path(loan_dir)
    if not given.exists():
        raise IntakeStopCondition(f"input directory does not exist: {given}")
    resolved = given.resolve()
    if not resolved.is_dir():
        raise IntakeStopCondition(f"input path is not a directory: {given}")
    if not any(_is_within(resolved, root) and resolved != root for root in roots):
        raise IntakeStopCondition(
            f"input directory {given} (real path {resolved}) is not a loan directory under an approved "
            f"input root {[str(r) for r in roots]}; refusing to read it"
        )
    return resolved


def check_file_within(path: Path, loan_dir_resolved: Path) -> Path:
    """Resolve one inventoried file and refuse it if it (via symlink) escapes the loan directory."""
    real = Path(path).resolve()
    if not _is_within(real, loan_dir_resolved):
        raise IntakeStopCondition(
            f"file {path} resolves to {real}, outside the loan directory {loan_dir_resolved}; "
            "symlink escapes are refused"
        )
    return real


# --------------------------------------------------------------------------------------
# MANIFEST.yaml
# --------------------------------------------------------------------------------------

def manifest_path(loan_dir: Path) -> Path:
    return Path(loan_dir) / MANIFEST_FILENAME


def compute_manifest_sha256(loan_dir: Path) -> str:
    """SHA-256 of the MANIFEST.yaml bytes (recorded in every output as provenance)."""
    return sha256_file(manifest_path(loan_dir))


def load_manifest(loan_dir: Path) -> Manifest:
    """Parse and check MANIFEST.yaml. Raises IntakeStopCondition on any defect."""
    path = manifest_path(loan_dir)
    if not path.is_file():
        raise IntakeStopCondition(f"{path} is missing; every loan directory needs a MANIFEST.yaml")
    try:
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as exc:
        raise IntakeStopCondition(f"{path} cannot be parsed: {exc}") from exc
    if not isinstance(raw, dict):
        raise IntakeStopCondition(f"{path} must be a YAML mapping")

    loan_id = raw.get("loan_id")
    if not isinstance(loan_id, str) or not LOAN_ID_RE.match(loan_id):
        raise IntakeStopCondition(f"{path}: loan_id {loan_id!r} is missing or does not match {LOAN_ID_RE.pattern}")

    # `deidentified` must be the YAML boolean true. "true"/"yes"/1 are rejected on purpose so
    # nobody can satisfy the gate with a loosely typed value.
    # PyYAML (YAML 1.1) also parses yes/on/True as a boolean, so check the raw line as well:
    # the file must literally say "deidentified: true".
    literal_true = re.search(r"^deidentified:\s*true\s*(#.*)?$", text, re.MULTILINE) is not None
    if raw.get("deidentified") is not True or not literal_true:
        raise IntakeStopCondition(
            f"{path}: deidentified must be literally true (found {raw.get('deidentified')!r}); "
            "the directory is treated as live data and refused"
        )

    by = raw.get("deidentified_by")
    if not isinstance(by, str) or not by.strip():
        raise IntakeStopCondition(f"{path}: deidentified_by must be a non-empty string")

    at = raw.get("deidentified_at")
    at_str = at.isoformat() if isinstance(at, date) else at  # YAML parses bare dates to date objects
    if not isinstance(at_str, str) or not ISO_DATE_RE.match(at_str):
        raise IntakeStopCondition(f"{path}: deidentified_at must be an ISO date YYYY-MM-DD (found {at!r})")
    try:
        date.fromisoformat(at_str)
    except ValueError as exc:
        raise IntakeStopCondition(f"{path}: deidentified_at is not a real calendar date: {exc}") from exc

    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        raise IntakeStopCondition(f"{path}: description must be a string when present")

    allow = raw.get("pii_pattern_allowlist") or []
    if not isinstance(allow, list) or not all(isinstance(a, (str, int)) for a in allow):
        raise IntakeStopCondition(f"{path}: pii_pattern_allowlist must be a list of literal strings")
    allowlist = tuple(str(a).strip() for a in allow if str(a).strip())

    return Manifest(
        loan_id=loan_id,
        deidentified=True,
        deidentified_by=by.strip(),
        deidentified_at=at_str,
        description=description,
        pii_pattern_allowlist=allowlist,
        path=path,
        sha256=sha256_file(path),
        raw=raw,
    )


def check_manifest(loan_dir: Path, approved_roots: Iterable[Path] | None = None) -> tuple[Path, Manifest]:
    """Location check + manifest check in one call. Returns (resolved_dir, manifest)."""
    resolved = check_approved_location(loan_dir, approved_roots)
    manifest = load_manifest(resolved)
    return resolved, manifest


# --------------------------------------------------------------------------------------
# Live-PII heuristic (stop condition during development)
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class PiiFinding:
    kind: str          # "SSN_PATTERN" | "MULTIPLE_LONG_DIGIT_RUNS"
    masked_token: str  # never the raw value
    allowlisted: bool
    reason: str


def looks_like_live_pii(text: str, allowlist: Iterable[str] = ()) -> list[str]:
    """Return reasons (strings) why ``text`` looks like un-de-identified borrower data.

    Heuristics (deliberately crude; the goal is to stop, not to be precise):
      * any SSN-shaped value (###-##-####, ### ## ####, or a bare 9-digit run) -> reason;
      * two or more DISTINCT 8+ digit runs on the page (typical account/routing numbers) -> reason.
    Limitations: an SSN written with unusual separators, account numbers with internal spaces
    ("1234 5678 9012"), or numbers rendered as images are NOT detected. A single 8+ digit run
    is tolerated because reference/confirmation numbers are common on legitimate synthetic
    fixtures; ZIP+4 codes and 9-digit invoice numbers are false positives.

    ``allowlist`` holds literal tokens from MANIFEST.yaml ``pii_pattern_allowlist``. A finding is
    suppressed only when the exact matched token (digits and separators as they appear) equals an
    entry. The allowlist exists so a de-identified fixture can carry clearly fake numbers and still
    demonstrate that this check fires.
    """
    return [f.reason for f in pii_findings(text, allowlist) if not f.allowlisted]


def pii_findings(text: str, allowlist: Iterable[str] = ()) -> list[PiiFinding]:
    """Detailed form of looks_like_live_pii(): every finding, allowlisted or not, with masked tokens."""
    allowed = {a.strip() for a in allowlist}
    findings: list[PiiFinding] = []

    ssn_tokens = []
    for m in SSN_RE.finditer(text):
        token = m.group(0)
        if token not in ssn_tokens:
            ssn_tokens.append(token)
    for token in ssn_tokens:
        masked = f"***-**-{token[-4:]}"
        findings.append(PiiFinding(
            kind="SSN_PATTERN", masked_token=masked, allowlisted=token in allowed,
            reason=f"SSN-shaped value {masked} present in extracted text",
        ))

    # Distinct 8+ digit runs that are NOT already counted as SSN-shaped (a bare 9-digit run
    # matches both regexes; count it once, as the more serious SSN finding).
    ssn_digit_only = {t for t in ssn_tokens if t.isdigit()}
    runs: list[str] = []
    for m in LONG_DIGITS_RE.finditer(text):
        run = m.group(1)
        if run not in runs and run not in ssn_digit_only:
            runs.append(run)
    if len(runs) >= 2:
        for run in runs:
            masked = mask_account(run) or "****"
            findings.append(PiiFinding(
                kind="MULTIPLE_LONG_DIGIT_RUNS", masked_token=masked, allowlisted=run in allowed,
                reason=f"{len(runs)} distinct 8+ digit runs on one page (possible account numbers), e.g. {masked}",
            ))
    return findings
