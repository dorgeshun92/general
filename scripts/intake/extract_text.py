#!/usr/bin/env python3
"""Per-page text extraction with page boundaries, masking, and missing-page flags.

    python scripts/intake/extract_text.py <file.pdf> [--json]

extract_pages() returns masked text only: SSNs and 8+ digit runs are masked with
scripts.common.masking.mask_text before anything leaves this module. The raw page text is
used inside the function for two things and then discarded:
  * the live-PII heuristic (manifest.pii_findings), reported with masked tokens only;
  * a content fingerprint (sha256 of whitespace-normalized text) for duplicate detection.

Pages with no extractable text get method NO_TEXT_LAYER and chars 0. OCR is out of scope for
now: it is recorded, not attempted.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pypdf import PdfReader  # noqa: E402

from scripts.common.hashing import sha256_bytes  # noqa: E402
from scripts.common.masking import mask_text  # noqa: E402
from scripts.intake.manifest import PiiFinding, pii_findings  # noqa: E402

METHOD_TEXT_LAYER = "TEXT_LAYER"
METHOD_NO_TEXT_LAYER = "NO_TEXT_LAYER"

# "Page 3 of 12" style footers/headers. Case-insensitive; tolerates "Page 3/12" too.
PAGE_OF_RE = re.compile(r"\bpage\s*(\d{1,4})\s*(?:of|/)\s*(\d{1,4})\b", re.IGNORECASE)


@dataclass
class PageText:
    page: int
    text: str          # masked
    method: str        # TEXT_LAYER | NO_TEXT_LAYER
    chars: int         # characters of extracted text before masking (0 for NO_TEXT_LAYER)
    page_markers: list[tuple[int, int]] = field(default_factory=list)  # (x, y) from "Page x of y"


@dataclass
class ExtractionResult:
    path: str
    page_count: int
    pages: list[PageText]
    possible_missing_pages: bool
    missing_pages_detail: str
    content_sha256: str | None          # fingerprint of normalized raw text; None if no text
    pii: list[PiiFinding] = field(default_factory=list)

    @property
    def has_text(self) -> bool:
        return any(p.method == METHOD_TEXT_LAYER for p in self.pages)

    @property
    def pages_without_text(self) -> list[int]:
        return [p.page for p in self.pages if p.method == METHOD_NO_TEXT_LAYER]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pii"] = [asdict(f) for f in self.pii]
        return d


def _normalize_for_fingerprint(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def detect_missing_pages(markers_by_page: Iterable[list[tuple[int, int]]], page_count: int) -> tuple[bool, str]:
    """Flag POSSIBLE_MISSING_PAGES from "Page X of Y" markers.

    Rules: (a) any Y greater than the actual page count; (b) the set of X values seen has a gap
    (e.g. 1, 3 without 2). Limitation: a bundle of several documents scanned into one PDF
    (several "Page 1 of 2") produces neither a gap nor Y > count and is NOT flagged, while a
    footer that says "Page 1 of 1" on every page of a multi-page doc is not flagged either.
    Text-free pages contribute no markers, so a missing page without a text layer can only be
    caught through Y > count.
    """
    xs: set[int] = set()
    ys: set[int] = set()
    for markers in markers_by_page:
        for x, y in markers:
            xs.add(x)
            ys.add(y)
    if not xs:
        return False, ""
    reasons = []
    max_y = max(ys)
    if max_y > page_count:
        reasons.append(f"footer claims {max_y} pages but the file has {page_count}")
    ordered = sorted(xs)
    expected = list(range(ordered[0], ordered[-1] + 1))
    if ordered != expected:
        gaps = sorted(set(expected) - xs)
        reasons.append(f"page numbers seen {ordered}; missing {gaps}")
    return (bool(reasons), "; ".join(reasons))


def extract_pages(path: Path, pii_allowlist: Iterable[str] = ()) -> ExtractionResult:
    """Extract every page of a readable PDF. Caller must have probed the file first (pdf_pages);
    an encrypted or corrupt file raises here, it is not silently skipped."""
    path = Path(path)
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    pages: list[PageText] = []
    raw_parts: list[str] = []
    findings: list[PiiFinding] = []
    with open(path, "rb") as fh:
        reader = PdfReader(fh, strict=False)
        if reader.is_encrypted:
            raise ValueError(f"{path} is encrypted; extraction refused")
        page_count = len(reader.pages)
        for index in range(page_count):
            try:
                raw = reader.pages[index].extract_text() or ""
            except Exception as exc:  # noqa: BLE001 - a broken page is recorded, not fatal
                raw = ""
                logging.getLogger(__name__).warning("page %d of %s unreadable: %s", index + 1, path, exc)
            stripped = raw.strip()
            if not stripped:
                pages.append(PageText(index + 1, "", METHOD_NO_TEXT_LAYER, 0, []))
                continue
            findings.extend(pii_findings(raw, pii_allowlist))
            markers = [(int(x), int(y)) for x, y in PAGE_OF_RE.findall(raw)]
            raw_parts.append(_normalize_for_fingerprint(raw))
            pages.append(PageText(index + 1, mask_text(raw), METHOD_TEXT_LAYER, len(stripped), markers))
    missing, detail = detect_missing_pages([p.page_markers for p in pages], page_count)
    fingerprint = sha256_bytes("\n".join(raw_parts).encode("utf-8")) if raw_parts else None
    return ExtractionResult(str(path), page_count, pages, missing, detail, fingerprint, findings)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", type=Path)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        result = extract_pages(args.file)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {args.file}: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"{args.file}: {result.page_count} page(s); possible_missing_pages={result.possible_missing_pages} {result.missing_pages_detail}")
        for p in result.pages:
            print(f"--- page {p.page} [{p.method}, {p.chars} chars] ---")
            print(p.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
