#!/usr/bin/env python3
"""Coverage report: every extracted source-checklist line and the catalog ids that cover it.

    python scripts/catalog/coverage_report.py --catalog config/checklist_catalog.yaml \
        --extracted docs/source-checklists/ --out output/catalog/coverage_report.md

Input format for each `<name>.extracted.txt`: pages separated by lines `=== PAGE n ===`.
Matching normalizes whitespace and case; a line is covered by an item when the
item's source_text is contained in the line, or the line is contained in the
item's source_text (lines shorter than MIN_LINE_CHARS never match that way).

Labels:
  NO ID          a source line no catalog item covers (heading, instruction, or a missed item)
  ORPHAN ITEM    a catalog item whose source_text was found in no source line
  PAGE MISMATCH  an item found only on pages other than its source_page (in its own source file when
                 the sources[] filename resolves, else in any file)

Exit codes: 0 clean, 1 orphan items or page mismatches present, 2 could not run.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

PAGE_RE = re.compile(r"^\s*===\s*PAGE\s+(\d+)\s*===\s*$", re.IGNORECASE)
MIN_LINE_CHARS = 3
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _WS.sub(" ", str(text)).strip().lower()


def parse_extracted(text: str) -> list[dict[str, Any]]:
    """Return [{page, line_no, text}] for every non-empty, non-separator line."""
    page = 1
    out: list[dict[str, Any]] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        m = PAGE_RE.match(raw)
        if m:
            page = int(m.group(1))
            continue
        stripped = raw.strip()
        if not stripped:
            continue
        out.append({"page": page, "line_no": line_no, "text": stripped, "norm": normalize(stripped)})
    return out


def line_matches(line_norm: str, item_norm: str) -> bool:
    if not item_norm or not line_norm:
        return False
    if item_norm in line_norm:
        return True
    return len(line_norm) >= MIN_LINE_CHARS and line_norm in item_norm


def source_filename_map(catalog: dict) -> dict[str, str]:
    """source_id -> extracted file stem (filename without .pdf)."""
    out = {}
    for src in catalog.get("sources") or []:
        if isinstance(src, dict) and src.get("source_id") and src.get("filename"):
            out[src["source_id"]] = Path(str(src["filename"])).stem
    return out


def build_coverage(catalog: dict, extracted_dir: Path) -> dict[str, Any]:
    items = [i for i in (catalog.get("items") or []) if isinstance(i, dict) and i.get("id")]
    norm_items = [(i["id"], normalize(i.get("source_text") or ""), i) for i in items]
    src_stem = source_filename_map(catalog)
    files = sorted(extracted_dir.glob("*.extracted.txt"))
    per_source: list[dict[str, Any]] = []
    found_at: dict[str, list[tuple[str, int]]] = {iid: [] for iid, _, _ in norm_items}
    for path in files:
        stem = path.name[: -len(".extracted.txt")]
        lines = parse_extracted(path.read_text(encoding="utf-8", errors="replace"))
        rows = []
        for ln in lines:
            ids = [iid for iid, inorm, _ in norm_items if line_matches(ln["norm"], inorm)]
            for iid in ids:
                found_at[iid].append((stem, ln["page"]))
            rows.append({"page": ln["page"], "line_no": ln["line_no"], "text": ln["text"], "ids": ids})
        covered = sum(1 for r in rows if r["ids"])
        per_source.append({"file": path.name, "stem": stem, "rows": rows,
                           "lines_total": len(rows), "lines_covered": covered, "lines_uncovered": len(rows) - covered})

    orphans: list[dict] = []
    page_mismatches: list[dict] = []
    for iid, _, item in norm_items:
        locations = found_at[iid]
        if not locations:
            orphans.append({"id": iid, "source_document": item.get("source_document"), "source_page": item.get("source_page")})
            continue
        own_stem = src_stem.get(item.get("source_document"))
        own = [loc for loc in locations if own_stem is None or loc[0] == own_stem]
        candidates = own if own else locations
        pages = sorted({p for _, p in candidates})
        if item.get("source_page") not in pages:
            page_mismatches.append({"id": iid, "source_document": item.get("source_document"),
                                    "source_page": item.get("source_page"), "found_pages": pages,
                                    "found_in": sorted({s for s, _ in candidates})})
    return {
        "sources": per_source,
        "items_total": len(norm_items),
        "orphan_items": orphans,
        "page_mismatches": page_mismatches,
        "lines_total": sum(s["lines_total"] for s in per_source),
        "lines_covered": sum(s["lines_covered"] for s in per_source),
        "lines_uncovered": sum(s["lines_uncovered"] for s in per_source),
        "clean": not orphans and not page_mismatches,
    }


def _cell(text: str, limit: int = 120) -> str:
    text = text.replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_markdown(cov: dict[str, Any], catalog_path: Path, extracted_dir: Path) -> str:
    out = ["# Checklist catalog coverage report", "",
           f"Catalog: `{catalog_path}`  Extracted sources: `{extracted_dir}`", "",
           "## Summary", "",
           f"- Extracted source files: {len(cov['sources'])}",
           f"- Catalog items: {cov['items_total']}",
           f"- Source lines: {cov['lines_total']} (covered {cov['lines_covered']}, NO ID {cov['lines_uncovered']})",
           f"- ORPHAN ITEM (source_text found in no source line): {len(cov['orphan_items'])}",
           f"- PAGE MISMATCH (found on a page other than source_page): {len(cov['page_mismatches'])}",
           f"- Result: {'CLEAN' if cov['clean'] else 'ATTENTION REQUIRED'}", ""]
    if cov["orphan_items"]:
        out += ["## Orphan items", "", "| id | source_document | source_page |", "|---|---|---|"]
        out += [f"| {o['id']} | {o['source_document']} | {o['source_page']} |" for o in cov["orphan_items"]]
        out.append("")
    if cov["page_mismatches"]:
        out += ["## Page mismatches", "", "| id | source_document | source_page | found on pages | found in |", "|---|---|---|---|---|"]
        out += [f"| {m['id']} | {m['source_document']} | {m['source_page']} | {m['found_pages']} | {', '.join(m['found_in'])} |"
                for m in cov["page_mismatches"]]
        out.append("")
    for src in cov["sources"]:
        out += [f"## {src['file']}", "",
                f"Lines: {src['lines_total']} (covered {src['lines_covered']}, NO ID {src['lines_uncovered']})", "",
                "| page | line | text | ids |", "|---|---|---|---|"]
        for r in src["rows"]:
            ids = ", ".join(r["ids"]) if r["ids"] else "NO ID"
            out.append(f"| {r['page']} | {r['line_no']} | {_cell(r['text'])} | {ids} |")
        out.append("")
    if not cov["sources"]:
        out += ["No `*.extracted.txt` files found.", ""]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", type=Path, default=REPO_ROOT / "config/checklist_catalog.yaml")
    ap.add_argument("--extracted", type=Path, default=REPO_ROOT / "docs/source-checklists")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        catalog = yaml.safe_load(args.catalog.read_text(encoding="utf-8")) or {}
        if not isinstance(catalog, dict):
            raise ValueError("catalog must be a mapping")
        if not args.extracted.is_dir():
            raise ValueError(f"extracted directory not found: {args.extracted}")
        cov = build_coverage(catalog, args.extracted)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render_markdown(cov, args.catalog, args.extracted), encoding="utf-8")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Coverage report: {args.out}")
    print(f"  lines {cov['lines_total']}, covered {cov['lines_covered']}, NO ID {cov['lines_uncovered']}, "
          f"orphan items {len(cov['orphan_items'])}, page mismatches {len(cov['page_mismatches'])}")
    return 0 if cov["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
