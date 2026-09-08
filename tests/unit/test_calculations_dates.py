"""Unit, boundary and negative tests for scripts.calculations.dates."""
import datetime as dt

import pytest

from scripts.calculations import CalculationInputError, date_expiration_check


def test_golden_not_expired():
    # 2025-01-15 -> 2025-03-15: 16 days left in Jan + 28 Feb + 15 Mar = 59 days old
    # expiration = 2025-01-15 + 120 days = 2025-05-15 (16 -> Jan 31, +28 -> Feb 28 = 44,
    #   +31 -> Mar 31 = 75, +30 -> Apr 30 = 105, +15 -> May 15 = 120)
    # days_remaining = 2025-05-15 - 2025-03-15 = 16 + 30 + 15 = 61
    trail = date_expiration_check(document_date="2025-01-15", as_of_date="2025-03-15", max_age_days=120)
    out = trail["output"]
    assert out["age_days"] == 59
    assert out["expired"] is False
    assert out["days_remaining"] == 61
    assert out["effective_expiration_date"] == "2025-05-15"
    assert out["basis"] == "max_age_days"
    assert trail["inputs"] == {
        "document_date": "2025-01-15", "as_of_date": "2025-03-15", "max_age_days": "120", "expiration_date": None,
    }
    assert trail["intermediate_values"]["age_days"] == "59"
    assert trail["warnings"] == []


def test_exactly_at_max_age_is_still_valid():
    # 2025-01-01 + 120 days: 31 + 28 + 31 + 30 = 120 -> 2025-05-01
    trail = date_expiration_check(document_date="2025-01-01", as_of_date="2025-05-01", max_age_days=120)
    assert trail["output"]["age_days"] == 120
    assert trail["output"]["days_remaining"] == 0
    assert trail["output"]["expired"] is False


def test_one_day_past_max_age_is_expired():
    trail = date_expiration_check(document_date="2025-01-01", as_of_date="2025-05-02", max_age_days=120)
    assert trail["output"]["age_days"] == 121
    assert trail["output"]["days_remaining"] == -1
    assert trail["output"]["expired"] is True
    assert "expired on 2025-05-01" in trail["warnings"][0]


def test_same_day_zero_age():
    trail = date_expiration_check(document_date="2025-06-01", as_of_date="2025-06-01", max_age_days=0)
    assert trail["output"]["age_days"] == 0
    assert trail["output"]["days_remaining"] == 0
    assert trail["output"]["expired"] is False


def test_month_boundary():
    trail = date_expiration_check(document_date="2025-01-31", as_of_date="2025-02-01", max_age_days=30)
    assert trail["output"]["age_days"] == 1
    # 2025-01-31 + 30 = 2025-03-02 (Feb has 28 days: +28 -> Feb 28, +2 -> Mar 2)
    assert trail["output"]["effective_expiration_date"] == "2025-03-02"


def test_leap_day_counts():
    # 2024 is a leap year: Feb 28 -> Mar 1 is 2 days (Feb 29 exists)
    leap = date_expiration_check(document_date="2024-02-28", as_of_date="2024-03-01", max_age_days=90)
    assert leap["output"]["age_days"] == 2
    # 2025 is not: Feb 28 -> Mar 1 is 1 day
    common = date_expiration_check(document_date="2025-02-28", as_of_date="2025-03-01", max_age_days=90)
    assert common["output"]["age_days"] == 1
    # 2024-02-29 + 365 = 2025-02-28 (the next year has no Feb 29)
    year = date_expiration_check(document_date="2024-02-29", as_of_date="2024-02-29", max_age_days=365)
    assert year["output"]["effective_expiration_date"] == "2025-02-28"


def test_explicit_expiration_date_overrides_max_age():
    trail = date_expiration_check(
        document_date="2025-01-01", as_of_date="2025-02-01", max_age_days=120, expiration_date="2025-01-31"
    )
    out = trail["output"]
    assert out["basis"] == "expiration_date"
    assert out["effective_expiration_date"] == "2025-01-31"
    assert out["days_remaining"] == -1
    assert out["expired"] is True
    assert trail["intermediate_values"]["max_age_expiration_date"] == "2025-05-01"
    assert any("overrides" in w for w in trail["warnings"])


def test_explicit_expiration_date_alone():
    trail = date_expiration_check(document_date="2025-01-01", as_of_date="2025-02-01", expiration_date="2025-02-01")
    assert trail["output"]["expired"] is False
    assert trail["output"]["days_remaining"] == 0
    assert trail["inputs"]["max_age_days"] is None
    assert trail["warnings"] == []


def test_date_objects_and_digit_string_day_count_accepted():
    trail = date_expiration_check(
        document_date=dt.date(2025, 1, 15), as_of_date=dt.datetime(2025, 3, 15, 10, 30), max_age_days="120"
    )
    assert trail["output"]["age_days"] == 59
    assert trail["inputs"]["as_of_date"] == "2025-03-15"


def test_as_of_before_document_date_rejected():
    with pytest.raises(CalculationInputError):
        date_expiration_check(document_date="2025-03-15", as_of_date="2025-03-14", max_age_days=120)


def test_expiration_before_document_date_rejected():
    with pytest.raises(CalculationInputError):
        date_expiration_check(document_date="2025-03-15", as_of_date="2025-03-15", expiration_date="2025-03-14")


def test_missing_both_rules_rejected():
    with pytest.raises(CalculationInputError):
        date_expiration_check(document_date="2025-01-01", as_of_date="2025-02-01")


@pytest.mark.parametrize("bad", [120.0, True, "120.0", None.__class__])
def test_bad_max_age_types_rejected(bad):
    with pytest.raises(TypeError):
        date_expiration_check(document_date="2025-01-01", as_of_date="2025-02-01", max_age_days=bad)


def test_negative_max_age_rejected():
    with pytest.raises(CalculationInputError):
        date_expiration_check(document_date="2025-01-01", as_of_date="2025-02-01", max_age_days=-1)


@pytest.mark.parametrize("bad", ["2025-02-30", "2025/01/01", "1/1/2025", "20250101", ""])
def test_bad_dates_rejected(bad):
    with pytest.raises(CalculationInputError):
        date_expiration_check(document_date=bad, as_of_date="2025-06-01", max_age_days=10)


def test_reproducible():
    kwargs = dict(document_date="2025-01-15", as_of_date="2025-03-15", max_age_days=120)
    assert date_expiration_check(**kwargs) == date_expiration_check(**kwargs)
