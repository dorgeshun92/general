"""Unit, boundary and negative tests for scripts.calculations.liability."""
import pytest

from scripts.calculations import CalculationInputError, liability_payment_comparison
from scripts.calculations.liability import HUMAN_REVIEW_WARNING


def test_all_equal_no_warning():
    trail = liability_payment_comparison(
        credit_report_payment="350.00", stated_1003_payment="350.00", document_payment="350.00"
    )
    out = trail["output"]
    assert out["all_equal"] is True
    assert out["matches_within_tolerance"] is True
    assert out["highest_source"] == "credit_report"
    assert out["highest_sources"] == ["credit_report", "1003", "document"]
    assert out["highest_payment"] == "350.00"
    assert out["differences"] == {
        "credit_report_minus_1003": "0.00",
        "credit_report_minus_document": "0.00",
        "1003_minus_document": "0.00",
    }
    assert out["max_absolute_difference"] == "0.00"
    assert trail["warnings"] == []


def test_small_difference_within_tolerance_still_warns():
    # 350.00 - 349.50 = 0.50 <= 1.00 tolerance, but sources differ -> human review warning
    trail = liability_payment_comparison(credit_report_payment="350.00", stated_1003_payment="349.50")
    out = trail["output"]
    assert out["differences"]["credit_report_minus_1003"] == "0.50"
    assert out["differences"]["credit_report_minus_document"] is None
    assert out["differences"]["1003_minus_document"] is None
    assert out["payments"]["document"] is None
    assert out["highest_source"] == "credit_report"
    assert out["highest_sources"] == ["credit_report"]
    assert out["matches_within_tolerance"] is True
    assert out["all_equal"] is False
    assert trail["warnings"] == [HUMAN_REVIEW_WARNING]
    assert "guideline lookup / human review" in trail["warnings"][0]
    assert trail["inputs"]["document_payment"] is None
    assert trail["inputs"]["tolerance"] == "1.00"


def test_exactly_at_tolerance_matches():
    # 351.00 - 350.00 = 1.00 <= 1.00
    trail = liability_payment_comparison(credit_report_payment="350.00", stated_1003_payment="351.00")
    assert trail["output"]["differences"]["credit_report_minus_1003"] == "-1.00"
    assert trail["output"]["matches_within_tolerance"] is True
    assert trail["output"]["highest_source"] == "1003"


def test_just_over_tolerance_does_not_match():
    trail = liability_payment_comparison(credit_report_payment="350.00", stated_1003_payment="351.01")
    assert trail["output"]["matches_within_tolerance"] is False
    assert trail["output"]["max_absolute_difference"] == "1.01"


def test_document_highest():
    # credit 350, 1003 340, document 400 ; max abs diff = 400 - 340 = 60
    trail = liability_payment_comparison(
        credit_report_payment="350", stated_1003_payment="340", document_payment="400"
    )
    out = trail["output"]
    assert out["highest_source"] == "document"
    assert out["highest_payment"] == "400.00"
    assert out["differences"] == {
        "credit_report_minus_1003": "10.00",
        "credit_report_minus_document": "-50.00",
        "1003_minus_document": "-60.00",
    }
    assert out["max_absolute_difference"] == "60.00"
    assert out["matches_within_tolerance"] is False
    assert trail["warnings"] == [HUMAN_REVIEW_WARNING]


def test_tie_between_two_sources_lists_both_in_fixed_order():
    trail = liability_payment_comparison(
        credit_report_payment="300", stated_1003_payment="400", document_payment="400"
    )
    assert trail["output"]["highest_source"] == "1003"
    assert trail["output"]["highest_sources"] == ["1003", "document"]


def test_custom_tolerance():
    trail = liability_payment_comparison(
        credit_report_payment="350", stated_1003_payment="360", tolerance="10"
    )
    assert trail["output"]["matches_within_tolerance"] is True
    assert trail["output"]["tolerance"] == "10.00"


def test_zero_payments_boundary():
    trail = liability_payment_comparison(credit_report_payment="0", stated_1003_payment="0")
    assert trail["output"]["all_equal"] is True
    assert trail["output"]["highest_payment"] == "0.00"


@pytest.mark.parametrize("field", ["credit_report_payment", "stated_1003_payment", "document_payment", "tolerance"])
def test_negative_rejected(field):
    kwargs = dict(credit_report_payment="1", stated_1003_payment="1", document_payment="1")
    kwargs[field] = "-1"
    with pytest.raises(CalculationInputError):
        liability_payment_comparison(**kwargs)


@pytest.mark.parametrize("field", ["credit_report_payment", "stated_1003_payment", "document_payment", "tolerance"])
@pytest.mark.parametrize("bad", [350.0, 350])
def test_float_and_int_rejected(field, bad):
    kwargs = dict(credit_report_payment="1", stated_1003_payment="1", document_payment="1")
    kwargs[field] = bad
    with pytest.raises(TypeError):
        liability_payment_comparison(**kwargs)


def test_reproducible():
    kwargs = dict(credit_report_payment="350", stated_1003_payment="340", document_payment="400")
    assert liability_payment_comparison(**kwargs) == liability_payment_comparison(**kwargs)
