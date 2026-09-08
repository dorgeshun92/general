#!/usr/bin/env python3
"""Page counts and readability status per file. Read-only: files are opened with "rb" only.

    python scripts/intake/pdf_pages.py <file-or-directory> [--json]

Status values (a subset of the document_inventory ``unreadable.reason`` enum plus OK):
    OK                  readable PDF, page_count known
    ENCRYPTED           pypdf reports ``is_encrypted``; no password is ever attempted
    CORRUPT             pypdf cannot parse the file (bad header, truncated xref, ...)
    EMPTY               zero-byte file
    UNSUPPORTED_FORMAT  extension is not .pdf; the file is listed but never opened as a PDF

Limitations: a valid PDF whose pages are images only still reports OK here (text-layer
detection lives in extract_text.py). A PDF with a wrong extension is UNSUPPORTED_FORMAT, not
sniffed by content.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pypdf import PdfReader  # noqa: E402

PDF_EXTENSIONS = {".pdf"}


@dataclass(frozen=True)
class PdfProbe:
    path: str
    status: str            # OK | ENCRYPTED | CORRUPT | EMPTY | UNSUPPORTED_FORMAT
    page_count: int | None
    size_bytes: int
    detail: str = ""


def probe_pdf(path: Path) -> PdfProbe:
    """Classify one file without modifying it. Never raises for content problems."""
    path = Path(path)
    size = path.stat().st_size
    if path.suffix.lower() not in PDF_EXTENSIONS:
        return PdfProbe(str(path), "UNSUPPORTED_FORMAT", None, size,
                        f"extension {path.suffix or '(none)'} is not .pdf; not opened")
    if size == 0:
        return PdfProbe(str(path), "EMPTY", None, size, "zero-byte file")

    # pypdf logs parse warnings for damaged files; silence them so the CLI output stays clean.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        with open(path, "rb") as fh:
            reader = PdfReader(fh, strict=False)
            if reader.is_encrypted:
                # Deliberately no decrypt() call, not even with an empty password: the MVP
                # must not attempt to open protected documents.
                return PdfProbe(str(path), "ENCRYPTED", None, size,
                                "PDF is encrypted; no password attempted")
            count = len(reader.pages)
    except Exception as exc:  # noqa: BLE001 - any parser failure means unreadable
        return PdfProbe(str(path), "CORRUPT", None, size, f"{type(exc).__name__}: {exc}"[:300])
    return PdfProbe(str(path), "OK", count, size, "")


def probe_tree(root: Path) -> list[PdfProbe]:
    """Probe every regular file under ``root`` (sorted for determinism)."""
    root = Path(root)
    if root.is_file():
        return [probe_pdf(root)]
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            files.append(Path(dirpath) / name)
    return [probe_pdf(p) for p in sorted(files)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path)
    ap.add_argument("--json", action="store_true", help="emit a JSON list instead of a table")
    args = ap.parse_args(argv)
    if not args.path.exists():
        print(f"ERROR: {args.path} does not exist", file=sys.stderr)
        return 2
    probes = probe_tree(args.path)
    if args.json:
        print(json.dumps([asdict(p) for p in probes], indent=2))
    else:
        for p in probes:
            pages = "-" if p.page_count is None else str(p.page_count)
            print(f"{p.status:<18} {pages:>5} pages {p.size_bytes:>9} B  {p.path}  {p.detail}".rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
