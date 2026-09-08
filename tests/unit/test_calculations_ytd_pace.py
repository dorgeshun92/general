"""Unit, boundary and negative tests for scripts.calculations.ytd_pace."""
import datetime as dt

import pytest

from scripts.calculations import CalculationInputError, ytd_pace_comparison


def test_golden_half_year():
    # Jan 1 - Jun 30 2025 inclusive: 31+28+31+30+31+30 = 181 days
    # months = 181 / 30.4375 = 5.9466119096509240246406570842
    # average = 30000 / months = 30000 * 30.4375 / 181 = 913125 / 181 = 5044.889502762430939226519337 -> 5044.89
    # variance = 5044.889502... - 5000 = 44.889502... -> 44.89
    # percent = 44.889502... / 5000 * 100 = 0.89779005... -> 0.8978
    trail = ytd_pace_comparison(
        ytd_gross="30000", period_start="2025-01-01", as_of_date="2025-06-30", expected_monthly="5000"
    )
    iv = trail["intermediate_values"]
    assert iv["days_elapsed_inclusive"] == "181"
    assert iv["days_per_month"] == "30.4375"
    assert iv["months_elapsed"].startswith("5.94661190965092")
    out = trail["output"]
    assert out["days_elapsed"] == 181
    assert out["months_elapsed"] == "5.9466"
    assert out["ytd_monthly_average"] == "5044.89"
    assert out["variance_amount"] == "44.89"
    assert out["variance_percent"] == "0.8978"
    assert out["direction"] == "above"
    assert out["exceeds_threshold"] is False
    assert out["variance_threshold_percent"] == "10.0000"
    assert trail["warnings"] == []
    assert trail["inputs"]["variance_threshold_percent"] == "10"


def test_exactly_at_threshold_boundary_not_exceeded():
    # 2024-01-01 .. 2025-05-01 inclusive: 366 (leap 2024) + 31+28+31+30+1 = 366 + 121 = 487 days
    # months = 487 / 30.4375 = 16 exactly (30.4375 * 16 = 487)
    # average = 17600 / 16 = 1100 ; variance = 100 ; percent = 100/1000*100 = 10.0000
    trail = ytd_pace_comparison(
        ytd_gross="17600", period_start="2024-01-01", as_of_date="2025-05-01",
        expected_monthly="1000", variance_threshold_percent="10",
    )
    assert trail["intermediate_values"]["months_elapsed"] == "16"
    assert trail["output"]["ytd_monthly_average"] == "1100.00"
    assert trail["output"]["variance_percent"] == "10.0000"
    assert trail["output"]["exceeds_threshold"] is False
    assert trail["warnings"] == []


def test_just_over_threshold_exceeded():
    trail = ytd_pace_comparison(
        ytd_gross="17600", period_start="2024-01-01", as_of_date="2025-05-01",
        expected_monthly="1000", variance_threshold_percent="9.9999",
    )
    assert trail["output"]["exceeds_threshold"] is True
    assert len(trail["warnings"]) == 1
    assert "above" in trail["warnings"][0]


def test_below_pace_warns_with_default_threshold():
    # 181 days -> average = 10000 * 30.4375 / 181 = 304375/181 = 1681.629834... -> 1681.63
    # variance = -3318.37 ; percent = -66.3674... -> exceeds 10
    trail = ytd_pace_comparison(
        ytd_gross="10000", period_start="2025-01-01", as_of_date="2025-06-30", expected_monthly="5000"
    )
    assert trail["output"]["ytd_monthly_average"] == "1681.63"
    assert trail["output"]["variance_amount"] == "-3318.37"
    assert trail["output"]["direction"] == "below"
    assert trail["output"]["exceeds_threshold"] is True
    assert "below" in trail["warnings"][0]


def test_leap_day_month_boundary():
    # 2024-02-01 .. 2024-02-29 inclusive = 29 days (leap year)
    # months = 29 / 30.4375 ; average = 2900 / (29/30.4375) = 100 * 30.4375 = 3043.75 exactly
    trail = ytd_pace_comparison(
        ytd_gross="2900", period_start="2024-02-01", as_of_date="2024-02-29", expected_monthly="3043.75"
    )
    assert trail["output"]["days_elapsed"] == 29
    assert trail["output"]["ytd_monthly_average"] == "3043.75"
    assert trail["output"]["variance_amount"] == "0.00"
    assert trail["output"]["variance_percent"] == "0.0000"
    assert trail["output"]["direction"] == "on_pace"


def test_non_leap_february_rejects_feb_29():
    with pytest.raises(CalculationInputError):
        ytd_pace_comparison(
            ytd_gross="2900", period_start="2025-02-01", as_of_date="2025-02-29", expected_monthly="3000"
        )


def test_same_day_boundary_is_one_day():
    # inclusive count: 1 day ; months = 1/30.4375 ; average = 100 * 30.4375 = 3043.75
    trail = ytd_pace_comparison(
        ytd_gross="100", period_start="2025-03-15", as_of_date="2025-03-15", expected_monthly="3043.75"
    )
    assert trail["output"]["days_elapsed"] == 1
    assert trail["output"]["ytd_monthly_average"] == "3043.75"


def test_full_calendar_year():
    # 2025-01-01 .. 2025-12-31 = 365 days ; months = 365/30.4375 = 11.99178...
    # average = 60000 * 30.4375 / 365 = 1826250/365 = 5003.42465... -> 5003.42
    trail = ytd_pace_comparison(
        ytd_gross="60000", period_start="2025-01-01", as_of_date="2025-12-31", expected_monthly="5000"
    )
    assert trail["output"]["months_elapsed"] == "11.9918"
    assert trail["output"]["ytd_monthly_average"] == "5003.42"


def test_date_objects_accepted_and_normalized():
    trail = ytd_pace_comparison(
        ytd_gross="100", period_start=dt.date(2025, 3, 15), as_of_date=dt.date(2025, 3, 15),
        expected_monthly="3043.75",
    )
    assert trail["inputs"]["period_start"] == "2025-03-15"
    assert trail["inputs"]["as_of_date"] == "2025-03-15"


def test_zero_ytd_boundary():
    trail = ytd_pace_comparison(
        ytd_gross="0", period_start="2025-01-01", as_of_date="2025-01-31", expected_monthly="5000"
    )
    assert trail["output"]["ytd_monthly_average"] == "0.00"
    assert trail["output"]["variance_percent"] == "-100.0000"
    assert trail["output"]["exceeds_threshold"] is True


def test_as_of_before_period_start_rejected():
    with pytest.raises(CalculationInputError):
        ytd_pace_comparison(
            ytd_gross="100", period_start="2025-03-15", as_of_date="2025-03-14", expected_monthly="100"
        )


@pytest.mark.parametrize("bad", ["2025/01/01", "01-01-2025", "20250101", "2025-13-01", "2025-02-30", "", 20250101])
def test_bad_dates_rejected(bad):
    with pytest.raises((CalculationInputError, TypeError)):
        ytd_pace_comparison(ytd_gross="100", period_start=bad, as_of_date="2025-06-30", expected_monthly="100")


@pytest.mark.parametrize("expected", ["0", "-5000"])
def test_expected_monthly_must_be_positive(expected):
    with pytest.raises(CalculationInputError):
        ytd_pace_comparison(
            ytd_gross="100", period_start="2025-01-01", as_of_date="2025-06-30", expected_monthly=expected
        )


def test_negative_ytd_rejected():
    with pytest.raises(CalculationInputError):
        ytd_pace_comparison(
            ytd_gross="-1", period_start="2025-01-01", as_of_date="2025-06-30", expected_monthly="100"
        )


def test_negative_threshold_rejected():
    with pytest.raises(CalculationInputError):
        ytd_pace_comparison(
            ytd_gross="100", period_start="2025-01-01", as_of_date="2025-06-30",
            expected_monthly="100", variance_threshold_percent="-1",
        )


@pytest.mark.parametrize("bad", [30000.0, 30000])
def test_float_and_int_money_rejected(bad):
    with pytest.raises(TypeError):
        ytd_pace_comparison(ytd_gross=bad, period_start="2025-01-01", as_of_date="2025-06-30", expected_monthly="100")
    with pytest.raises(TypeError):
        ytd_pace_comparison(ytd_gross="100", period_start="2025-01-01", as_of_date="2025-06-30", expected_monthly=bad)


def test_reproducible():
    kwargs = dict(ytd_gross="30000", period_start="2025-01-01", as_of_date="2025-06-30", expected_monthly="5000")
    assert ytd_pace_comparison(**kwargs) == ytd_pace_comparison(**kwargs)
