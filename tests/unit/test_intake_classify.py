from __future__ import annotations

import pytest

from scripts.common.schema_registry import validate_ref
from scripts.intake.classify import (
    KEYWORD_TABLE,
    borrower_name_candidates,
    classify,
    document_date,
    extract_fields,
    find_dates,
    masked_account_numbers,
    normalize_date,
    normalize_name,
    statement_period,
)
from scripts.intake.extract_text import extract_pages
from tests.unit.test_intake_helpers import fixture_dir


def test_keyword_table_uses_only_schema_document_types():
    for doc_type in KEYWORD_TABLE:
        assert validate_ref(doc_type, "common.defs.schema.json#/$defs/document_type") == []


@pytest.mark.parametrize("rel, expected", [
    ("01_urla_1003.pdf", "URLA_1003"),
    ("income/paystub_2026-08.pdf", "PAYSTUB"),
    ("income/w2_2025.pdf", "W2"),
    ("assets/bank_statement_2026-07.pdf", "BANK_STATEMENT"),
    ("contract/purchase_contract.pdf", "PURCHASE_CONTRACT"),
])
def test_clean_package_classifies_high(rel, expected):
    path = fixture_dir("LN-EDGE-CLEAN") / rel
    text = extract_pages(path).pages[0].text
    result = classify(path.name, text)
    assert (result.document_type, result.confidence) == (expected, "HIGH")
    assert result.matched_text_markers


def test_filename_only_is_medium():
    result = classify("paystub_march.pdf", "nothing useful on this page")
    assert (result.document_type, result.confidence) == ("PAYSTUB", "MEDIUM")
    assert result.matched_filename_tokens == ["paystub"]


def test_nothing_matches_is_unknown_low():
    result = classify("scan0001.pdf", "")
    assert (result.document_type, result.confidence) == ("UNKNOWN", "LOW")


def test_text_beats_filename_and_notes_disagreement():
    result = classify("w2_2025.pdf", "EARNINGS STATEMENT gross pay net pay pay period")
    assert (result.document_type, result.confidence) == ("PAYSTUB", "HIGH")
    assert "filename suggests W2" in result.note


def test_short_filename_tokens_need_word_boundaries():
    assert classify("identity_theft_notice.pdf", "").document_type == "UNKNOWN"
    assert classify("drivers_id.pdf", "").document_type == "PHOTO_ID"


def test_classification_is_deterministic():
    text = "statement period beginning balance ending balance pay period"
    assert classify("x.pdf", text) == classify("x.pdf", text)


@pytest.mark.parametrize("raw, iso", [
    ("2026-08-15", "2026-08-15"), ("8/15/2026", "2026-08-15"), ("08/15/2026", "2026-08-15"),
    ("August 15, 2026", "2026-08-15"), ("Aug 15 2026", "2026-08-15"), ("Sept. 1, 2026", "2026-09-01"),
    ("13/40/2026", None), ("Foo 1, 2026", None), ("01/01/1850", None),
])
def test_normalize_date(raw, iso):
    assert normalize_date(raw) == iso


def test_document_date_prefers_a_date_line():
    text = "Pay Period: 08/01/2026 - 08/15/2026\nPay Date: 08/20/2026\n"
    assert document_date(text) == ("2026-08-20", "MEDIUM")
    assert document_date("Balance as of 2026-07-31") == ("2026-07-31", "LOW")
    assert document_date("no dates here") == (None, "LOW")
    assert [c.iso for c in find_dates(text)] == ["2026-08-01", "2026-08-15", "2026-08-20"]


def test_statement_period_patterns():
    assert statement_period("Statement period: 07/01/2026 - 07/31/2026") == ({"start": "2026-07-01", "end": "2026-07-31"}, "HIGH")
    assert statement_period("Pay Period: 08/01/2026 through 08/15/2026")[0] == {"start": "2026-08-01", "end": "2026-08-15"}
    assert statement_period("from June 1, 2026 to June 30, 2026") == ({"start": "2026-06-01", "end": "2026-06-30"}, "MEDIUM")
    assert statement_period("Period: 07/31/2026 - 07/01/2026") == ({"start": None, "end": None}, "LOW")  # end before start
    assert statement_period("no period") == ({"start": None, "end": None}, "LOW")


def test_borrower_name_candidates():
    text = ("Borrower Name: Test Borrower Clean\nEmployee: Test Borrower Alpha-Smith\n"
            "Account holder: T. B. Alpha\nName:\nTest Borrower Next Line\nBuyer: Test Borrower Clean\n"
            "Employee’s name: Curly Quote Person\nName: 12345 Main St\nBorrower: N/A\n")
    names = borrower_name_candidates(text)
    assert names == ["Test Borrower Clean", "Test Borrower Alpha-Smith", "T. B. Alpha",
                     "Test Borrower Next Line", "Curly Quote Person"]
    assert normalize_name("Test  Borrower Alpha-Smith") == "test borrower alpha smith"


def test_masked_account_numbers_from_masked_text():
    assert masked_account_numbers("Account ****5678 and ****5678 again; SSN ***-**-1234; ref **AB12") == ["****5678", "**AB12"]
    for tok in masked_account_numbers("acct ****5678"):
        assert validate_ref(tok, "common.defs.schema.json#/$defs/masked_account") == []


def test_extract_fields_labels_confidence():
    fields = extract_fields("Account holder: Test Borrower Clean\nStatement period: 07/01/2026 - 07/31/2026\nAccount ****5678")
    assert fields.borrower_name_confidence == "LOW"
    assert fields.statement_period_confidence == "HIGH"
    assert fields.document_date == "2026-07-01"
    assert fields.masked_account_numbers == ["****5678"]
