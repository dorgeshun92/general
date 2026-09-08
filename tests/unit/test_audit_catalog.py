import textwrap
from pathlib import Path

import pytest

from scripts.audit.catalog import DEFAULT_CATALOG_PATH, UNREVIEWED_LABEL, CatalogError, load_catalog

VALID_SOURCE = textwrap.dedent(
    """
    catalog_version: "0.2.0"
    normalized_at: "2026-09-01"
    reviewed_by: null
    reviewed_at: null
    sources:
      - source_id: SRC-EXAMPLE
        title: Synthetic example checklist
        filename: example.pdf
        sha256: "{sha}"
        page_count: 1
    items:
      - id: PRE-EXAMPLE-001
        source_document: SRC-EXAMPLE
        source_page: 1
        source_text: "Synthetic example item one (not a real rule)."
        phase: PREAPPROVAL
        category: OTHER
        applies_when: always
        required_evidence: [OTHER]
        fields_to_compare: []
        evaluation_method: human_judgment
        result_values: [PASS, FAIL, MISSING, REVIEW]
        blocking_if_failed: true
        proposed_action: "Ask a human."
        required_human_role: PROCESSOR
      - id: SUB-EXAMPLE-001
        source_document: SRC-EXAMPLE
        source_page: 1
        source_text: "Synthetic example item two (not a real rule)."
        phase: SUBMISSION
        category: OTHER
        applies_when: always
        required_evidence: []
        fields_to_compare: []
        evaluation_method: deterministic
        result_values: [PASS, FAIL]
        blocking_if_failed: false
        proposed_action: "Ask a human."
        required_human_role: null
    conflicts: []
    consolidation_decisions: []
    """
).format(sha="a" * 64)


def test_repository_catalog_loads_and_is_empty_and_unreviewed():
    cat = load_catalog(DEFAULT_CATALOG_PATH)
    assert cat.is_empty
    assert not cat.is_reviewed
    assert cat.items_in_phase("PREAPPROVAL") == []
    assert cat.items_in_phase("SUBMISSION") == []
    assert cat.review_status_label() == UNREVIEWED_LABEL
    assert "EMPTY" in cat.describe() and "checklist-normalization" in cat.describe()
    meta = cat.meta_for_phase("PREAPPROVAL")
    assert meta["is_empty"] is True and meta["reviewed_by"] is None and meta["items_in_phase"] == []


def test_phase_filter_and_lookup(tmp_path: Path):
    p = tmp_path / "catalog.yaml"
    p.write_text(VALID_SOURCE, encoding="utf-8")
    cat = load_catalog(p)
    assert not cat.is_empty
    assert cat.rule_ids_in_phase("PREAPPROVAL") == ["PRE-EXAMPLE-001"]
    assert cat.rule_ids_in_phase("SUBMISSION") == ["SUB-EXAMPLE-001"]
    assert cat.get("PRE-EXAMPLE-001")["category"] == "OTHER"
    assert "PRE-EXAMPLE-001" in cat and "PRE-EXAMPLE-999" not in cat
    with pytest.raises(KeyError):
        cat.get("PRE-EXAMPLE-999")
    with pytest.raises(CatalogError):
        cat.items_in_phase("CLOSING")
    assert cat.review_status_label() == UNREVIEWED_LABEL


def test_reviewed_catalog_reports_reviewer(tmp_path: Path):
    p = tmp_path / "catalog.yaml"
    p.write_text(VALID_SOURCE.replace("reviewed_by: null", 'reviewed_by: "Example Reviewer"'), encoding="utf-8")
    cat = load_catalog(p)
    assert cat.is_reviewed
    assert "Example Reviewer" in cat.review_status_label()


def test_invalid_catalog_fails_closed(tmp_path: Path):
    p = tmp_path / "catalog.yaml"
    p.write_text(VALID_SOURCE.replace("phase: PREAPPROVAL", "phase: WHENEVER"), encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(p)


def test_integrity_failure_fails_closed(tmp_path: Path):
    p = tmp_path / "catalog.yaml"
    p.write_text(VALID_SOURCE.replace("source_document: SRC-EXAMPLE", "source_document: SRC-NOPE"), encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(p)


def test_missing_or_malformed_file_fails_closed(tmp_path: Path):
    with pytest.raises(CatalogError):
        load_catalog(tmp_path / "does-not-exist.yaml")
    p = tmp_path / "bad.yaml"
    p.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(p)
