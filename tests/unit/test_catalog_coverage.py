"""Tests for scripts/catalog/coverage_report.py with a small synthetic catalog and extraction."""
from __future__ import annotations

from pathlib import Path

import yaml

from scripts.catalog import coverage_report as cr

REPO_ROOT = Path(__file__).resolve().parents[2]

EXTRACTED = """=== PAGE 1 ===
Pre-approval Checklist
Obtain a tri-merge credit report for every borrower.

Verify two years of employment history.
=== PAGE 2 ===
Collect the most recent 30 days of paystubs.
Notes for the loan partner
"""


def _item(iid, text, page, src="SRC-TEST"):
    return {"id": iid, "source_document": src, "source_page": page, "source_text": text, "phase": "PREAPPROVAL",
            "category": "CREDIT", "applies_when": "always", "required_evidence": [], "fields_to_compare": [],
            "evaluation_method": "deterministic", "result_values": ["PASS", "FAIL", "MISSING"],
            "blocking_if_failed": True, "proposed_action": "act", "required_human_role": None}


def _catalog(items, sources=None):
    return {"catalog_version": "0.1.0", "normalized_at": None, "reviewed_by": None, "reviewed_at": None,
            "sources": sources if sources is not None else
            [{"source_id": "SRC-TEST", "title": "Test", "filename": "test.pdf", "sha256": "0" * 64, "page_count": 2}],
            "items": items, "conflicts": [], "consolidation_decisions": []}


def _setup(tmp_path, items, sources=None):
    extracted = tmp_path / "src"
    extracted.mkdir()
    (extracted / "test.extracted.txt").write_text(EXTRACTED, encoding="utf-8")
    cat = tmp_path / "catalog.yaml"
    cat.write_text(yaml.safe_dump(_catalog(items, sources)), encoding="utf-8")
    return cat, extracted


def test_covered_no_id_orphan_and_page_mismatch(tmp_path):
    items = [
        _item("PRE-CREDIT-001", "obtain a  TRI-MERGE credit report for every borrower.", 1),  # covered (case/ws)
        _item("PRE-EMPLOYMENT-001", "Verify two years of employment history", 1),             # covered
        _item("PRE-INCOME-001", "Collect the most recent 30 days of paystubs.", 1),           # page mismatch (is on 2)
        _item("PRE-ASSETS-001", "Two months of bank statements.", 2),                          # orphan
    ]
    cat, extracted = _setup(tmp_path, items)
    out = tmp_path / "report.md"
    code = cr.main(["--catalog", str(cat), "--extracted", str(extracted), "--out", str(out)])
    assert code == 1
    cov = cr.build_coverage(yaml.safe_load(cat.read_text()), extracted)
    rows = {r["text"]: r["ids"] for r in cov["sources"][0]["rows"]}
    assert rows["Obtain a tri-merge credit report for every borrower."] == ["PRE-CREDIT-001"]
    assert rows["Verify two years of employment history."] == ["PRE-EMPLOYMENT-001"]
    assert rows["Collect the most recent 30 days of paystubs."] == ["PRE-INCOME-001"]
    assert rows["Pre-approval Checklist"] == [] and rows["Notes for the loan partner"] == []
    assert cov["lines_total"] == 5 and cov["lines_covered"] == 3 and cov["lines_uncovered"] == 2
    assert [o["id"] for o in cov["orphan_items"]] == ["PRE-ASSETS-001"]
    assert [m["id"] for m in cov["page_mismatches"]] == ["PRE-INCOME-001"]
    assert cov["page_mismatches"][0]["found_pages"] == [2]
    md = out.read_text(encoding="utf-8")
    assert "| 1 | 2 | Pre-approval Checklist | NO ID |" in md
    assert "ORPHAN ITEM" in md and "PAGE MISMATCH" in md and "ATTENTION REQUIRED" in md
    assert "| PRE-ASSETS-001 | SRC-TEST | 2 |" in md


def test_clean_catalog_exits_zero(tmp_path):
    items = [_item("PRE-CREDIT-001", "Obtain a tri-merge credit report for every borrower.", 1),
             _item("PRE-INCOME-001", "Collect the most recent 30 days of paystubs.", 2)]
    cat, extracted = _setup(tmp_path, items)
    out = tmp_path / "r.md"
    assert cr.main(["--catalog", str(cat), "--extracted", str(extracted), "--out", str(out)]) == 0
    assert "CLEAN" in out.read_text(encoding="utf-8")


def test_line_contained_in_source_text_matches_but_short_lines_do_not(tmp_path):
    items = [_item("PRE-CREDIT-001", "Pre-approval Checklist items: obtain a tri-merge credit report", 1)]
    cat, extracted = _setup(tmp_path, items)
    cov = cr.build_coverage(yaml.safe_load(cat.read_text()), extracted)
    rows = {r["text"]: r["ids"] for r in cov["sources"][0]["rows"]}
    assert rows["Pre-approval Checklist"] == ["PRE-CREDIT-001"]
    assert not cr.line_matches("a", "a long source text")
    assert cr.line_matches("a long source text with more", "long source text")


def test_empty_catalog_all_no_id(tmp_path):
    cat, extracted = _setup(tmp_path, [], sources=[])
    out = tmp_path / "r.md"
    assert cr.main(["--catalog", str(cat), "--extracted", str(extracted), "--out", str(out)]) == 0
    cov = cr.build_coverage(yaml.safe_load(cat.read_text()), extracted)
    assert cov["lines_uncovered"] == 5 and cov["lines_covered"] == 0
    assert out.read_text(encoding="utf-8").count("NO ID") >= 5


def test_real_empty_catalog_and_missing_dir(tmp_path):
    out = tmp_path / "r.md"
    assert cr.main(["--catalog", str(REPO_ROOT / "config/checklist_catalog.yaml"),
                    "--extracted", str(REPO_ROOT / "docs/source-checklists"), "--out", str(out)]) == 0
    assert cr.main(["--catalog", str(REPO_ROOT / "config/checklist_catalog.yaml"),
                    "--extracted", str(tmp_path / "missing"), "--out", str(out)]) == 2


def test_parse_extracted_pages():
    lines = cr.parse_extracted("a\n=== PAGE 3 ===\n\n b \n=== page 4 ===\nc")
    assert [(l["page"], l["text"]) for l in lines] == [(1, "a"), (3, "b"), (4, "c")]
