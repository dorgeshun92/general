"""Tests for scripts/catalog/stats.py."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.catalog import stats

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_empty_catalog_is_valid_and_prints_zero_counts(capsys):
    assert stats.main(["--catalog", str(REPO_ROOT / "config/checklist_catalog.yaml")]) == 0
    out = capsys.readouterr().out
    assert "Validation: VALID" in out and "Items: 0" in out and "not yet reviewed" in out


def test_populated_catalog_counts_and_json(tmp_path, capsys):
    item = {"id": "PRE-CREDIT-001", "source_document": "SRC-A", "source_page": 1, "source_text": "x",
            "phase": "PREAPPROVAL", "category": "CREDIT", "applies_when": "always", "required_evidence": [],
            "fields_to_compare": [], "evaluation_method": "deterministic", "result_values": ["PASS", "FAIL", "REVIEW"],
            "blocking_if_failed": True, "proposed_action": "act", "required_human_role": None, "ambiguous": True}
    item2 = {**item, "id": "SUB-ASSETS-001", "phase": "SUBMISSION", "category": "ASSETS",
             "evaluation_method": "human_judgment", "blocking_if_failed": False, "ambiguous": False}
    cat = {"catalog_version": "1.0", "normalized_at": "2026-09-08", "reviewed_by": None, "reviewed_at": None,
           "sources": [{"source_id": "SRC-A", "title": "A", "filename": "a.pdf", "sha256": "0" * 64, "page_count": 1}],
           "items": [item, item2],
           "conflicts": [{"conflict_id": "CAT-CF-001", "rule_ids": ["PRE-CREDIT-001", "SUB-ASSETS-001"],
                          "description": "d", "resolution": None, "resolved_by": None}],
           "consolidation_decisions": []}
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(cat), encoding="utf-8")
    assert stats.main(["--catalog", str(path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    s = data["stats"]
    assert s["by_phase"] == {"PREAPPROVAL": 1, "SUBMISSION": 1}
    assert s["by_category"] == {"ASSETS": 1, "CREDIT": 1}
    assert s["by_evaluation_method"] == {"deterministic": 1, "human_judgment": 1}
    assert s["by_blocking_if_failed"] == {"False": 1, "True": 1}
    assert s["by_ambiguous"] == {"False": 1, "True": 1}
    assert s["conflicts_unresolved"] == ["CAT-CF-001"] and s["reviewed"] is False


def test_invalid_catalog_exits_one_but_prints(tmp_path, capsys):
    cat = {"catalog_version": "1.0", "sources": [], "items": [{"id": "BAD"}], "conflicts": [],
           "consolidation_decisions": []}
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(cat), encoding="utf-8")
    assert stats.main(["--catalog", str(path)]) == 1
    out = capsys.readouterr().out
    assert "INVALID" in out and "Items: 1" in out


def test_unreadable_catalog_exits_two(tmp_path):
    assert stats.main(["--catalog", str(tmp_path / "missing.yaml")]) == 2
