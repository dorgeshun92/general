"""Masking for SSNs and account numbers. Applied to every report and log line.

Rules:
- SSN-shaped values (###-##-####, or 9 digits in an SSN context) become ***-**-#### .
- Account numbers keep only the last four characters: ****1234.
- mask_text() is conservative: it masks anything that looks like an SSN and any
  run of 8+ digits (typical account numbers). Amounts with decimals or thousands
  separators are left alone, as are digit runs inside hex digests.
"""
from __future__ import annotations

import re

SSN_RE = re.compile(r"\b(\d{3})[- ]?(\d{2})[- ]?(\d{4})\b")
# Digit runs adjacent to hex letters are part of a hash (sha256 digests routinely contain 8+ consecutive
# digits) and are not masked; a genuine account number is not embedded in hex text.
LONG_DIGITS_RE = re.compile(r"(?<![\d.,$A-Fa-f])(\d{8,})(?![\d.,A-Fa-f])")


def mask_account(value: str | None, keep: int = 4) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\s", "", str(value))
    if len(digits) <= keep:
        return "*" * max(4, len(digits))
    return "*" * max(2, len(digits) - keep) + digits[-keep:]


def mask_ssn(value: str | None) -> str | None:
    if value is None:
        return None
    m = SSN_RE.search(str(value))
    if not m:
        return mask_account(value)
    return f"***-**-{m.group(3)}"


def mask_text(text: str) -> str:
    """Mask SSNs and long digit runs in free text (report bodies, log lines, snippets)."""
    text = SSN_RE.sub(lambda m: f"***-**-{m.group(3)}", text)
    text = LONG_DIGITS_RE.sub(lambda m: mask_account(m.group(1)), text)
    return text


def contains_unmasked_pii(text: str) -> list[str]:
    """Return human-readable descriptions of unmasked PII patterns found. Empty list = clean."""
    hits = []
    if SSN_RE.search(text):
        hits.append("SSN-shaped value")
    if LONG_DIGITS_RE.search(text):
        hits.append("8+ digit run (possible account number)")
    return hits
