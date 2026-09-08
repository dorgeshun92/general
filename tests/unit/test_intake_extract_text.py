from __future__ import annotations

import pytest

from scripts.common.masking import contains_unmasked_pii
from scripts.intake.extract_text import METHOD_NO_TEXT_LAYER, METHOD_TEXT_LAYER, detect_missing_pages, extract_pages, main
from scripts.intake.make_edge_fixtures import build_pdf
from tests.unit.test_intake_helpers import fixture_dir


def test_pages_keep_boundaries_and_methods():
    result = extract_pages(fixture_dir("LN-EDGE-CLEAN") / "01_urla_1003.pdf")
    assert result.page_count == 2
    assert [p.page for p in result.pages] == [1, 2]
    assert all(p.method == METHOD_TEXT_LAYER and p.chars > 0 for p in result.pages)
    assert "Uniform Residential Loan Application" in result.pages[0].text
    assert "Section 2" in result.pages[1].text and "Section 2" not in result.pages[0].text
    assert result.possible_missing_pages is False
    assert result.content_sha256


def test_no_text_layer_pages_are_recorded_not_ocred():
    result = extract_pages(fixture_dir("LN-EDGE-UNREADABLE") / "scan_no_text_layer.pdf")
    assert [p.method for p in result.pages] == [METHOD_NO_TEXT_LAYER] * 2
    assert [p.chars for p in result.pages] == [0, 0]
    assert result.has_text is False and result.pages_without_text == [1, 2]
    assert result.content_sha256 is None


def test_missing_pages_flagged_on_fixture():
    result = extract_pages(fixture_dir("LN-EDGE-MISSING-PAGES") / "bank_statement_partial.pdf")
    assert result.possible_missing_pages is True
    assert "claims 3 pages" in result.missing_pages_detail
    assert result.pages[0].page_markers == [(1, 3)]


@pytest.mark.parametrize("markers, count, expected", [
    ([[(1, 2)], [(2, 2)]], 2, False),
    ([[(1, 3)], [(2, 3)]], 2, True),        # Y > actual count
    ([[(1, 3)], [(3, 3)]], 3, True),        # gap in X sequence
    ([[], []], 2, False),                   # no markers at all
    ([[(1, 1)], [(1, 1)]], 2, False),       # documented limitation: repeated "Page 1 of 1" is not flagged
])
def test_detect_missing_pages_rules(markers, count, expected):
    flagged, _detail = detect_missing_pages(markers, count)
    assert flagged is expected


def test_output_is_masked_and_pii_is_reported(tmp_path):
    pdf = tmp_path / "raw.pdf"
    pdf.write_bytes(build_pdf([["SSN: 123-45-6789", "Account: 12345678", "Routing: 87654321", "Amount $1,234,567.89"]]))
    result = extract_pages(pdf)
    text = result.pages[0].text
    assert "123-45-6789" not in text and "12345678" not in text and "87654321" not in text
    assert "***-**-6789" in text and "****5678" in text and "****4321" in text
    assert "$1,234,567.89" in text  # money is left alone
    assert contains_unmasked_pii(text) == []
    kinds = {f.kind for f in result.pii}
    assert kinds == {"SSN_PATTERN", "MULTIPLE_LONG_DIGIT_RUNS"}
    assert all(not f.allowlisted for f in result.pii)
    assert all("123-45-6789" not in f.reason and "12345678" not in f.reason for f in result.pii)


def test_allowlist_marks_findings(tmp_path):
    pdf = tmp_path / "raw.pdf"
    pdf.write_bytes(build_pdf([["Account: 12345678", "Routing: 87654321"]]))
    result = extract_pages(pdf, pii_allowlist=["12345678", "87654321"])
    assert result.pii and all(f.allowlisted for f in result.pii)


def test_same_text_different_bytes_share_fingerprint():
    d = fixture_dir("LN-EDGE-DUPLICATE")
    a = extract_pages(d / "paystub_a.pdf")
    c = extract_pages(d / "paystub_c_resaved.pdf")
    assert a.content_sha256 == c.content_sha256
    assert (d / "paystub_a.pdf").read_bytes() != (d / "paystub_c_resaved.pdf").read_bytes()


def test_encrypted_is_refused():
    with pytest.raises(ValueError, match="encrypted"):
        extract_pages(fixture_dir("LN-EDGE-ENCRYPTED") / "protected_bank_statement.pdf")


def test_cli(capsys):
    assert main([str(fixture_dir("LN-EDGE-CLEAN") / "income/w2_2025.pdf")]) == 0
    assert "Wage and Tax Statement" in capsys.readouterr().out
    assert main([str(fixture_dir("LN-EDGE-UNREADABLE") / "garbage.pdf")]) == 2
