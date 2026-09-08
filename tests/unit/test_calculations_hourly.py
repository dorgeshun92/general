"""Unit, boundary and negative tests for scripts.calculations.hourly."""
from decimal import Decimal

import pytest

from scripts.calculations import CalculationInputError, hourly_monthly_base

SOURCE = "VOE dated 2025-03-01, doc-0007 p.1"


def test_golden_full_time():
    # 25.00 * 40 = 1000 ; * 52 = 52000 ; / 12 = 4333.333... -> 4333.33
    trail = hourly_monthly_base(hourly_rate="25.00", verified_hours_per_week="40", verification_source=SOURCE)
    assert trail["intermediate_values"]["weekly_gross"] == "1000.00"
    assert trail["intermediate_values"]["annual_gross"] == "52000.00"
    assert trail["output"]["monthly_base"] == "4333.33"
    assert trail["output"]["annual_gross"] == "52000.00"
    assert trail["inputs"]["verification_source"] == SOURCE
    assert trail["warnings"] == []


def test_golden_part_time_half_up():
    # 18.75 * 32.5 = 609.375 ; * 52 = 31687.5 ; / 12 = 2640.625 -> ROUND_HALF_UP -> 2640.63
    trail = hourly_monthly_base(hourly_rate="18.75", verified_hours_per_week="32.5", verification_source=SOURCE)
    assert trail["intermediate_values"]["weekly_gross"] == "609.375"
    assert trail["intermediate_values"]["monthly_base_unrounded"] == "2640.625"
    assert trail["output"]["monthly_base"] == "2640.63"


def test_over_40_hours_computes_but_warns():
    # 25 * 45 = 1125 ; * 52 = 58500 ; / 12 = 4875.00
    trail = hourly_monthly_base(hourly_rate="25", verified_hours_per_week="45", verification_source=SOURCE)
    assert trail["output"]["monthly_base"] == "4875.00"
    assert len(trail["warnings"]) == 1
    assert "overtime" in trail["warnings"][0].lower()


def test_exactly_40_hours_no_warning():
    trail = hourly_monthly_base(hourly_rate="25", verified_hours_per_week="40.00", verification_source=SOURCE)
    assert trail["warnings"] == []


def test_just_over_40_warns():
    trail = hourly_monthly_base(hourly_rate="25", verified_hours_per_week="40.01", verification_source=SOURCE)
    assert len(trail["warnings"]) == 1


def test_168_hours_boundary_accepted():
    # 10 * 168 = 1680 ; * 52 = 87360 ; / 12 = 7280.00
    trail = hourly_monthly_base(hourly_rate="10", verified_hours_per_week="168", verification_source=SOURCE)
    assert trail["output"]["monthly_base"] == "7280.00"


@pytest.mark.parametrize("hours", ["168.01", "169", "1000"])
def test_more_than_168_hours_rejected(hours):
    with pytest.raises(CalculationInputError):
        hourly_monthly_base(hourly_rate="10", verified_hours_per_week=hours, verification_source=SOURCE)


@pytest.mark.parametrize("hours", ["0", "0.00", "-1", "-40"])
def test_zero_or_negative_hours_rejected(hours):
    with pytest.raises(CalculationInputError):
        hourly_monthly_base(hourly_rate="10", verified_hours_per_week=hours, verification_source=SOURCE)


def test_minimal_positive_hours_boundary():
    # 10 * 0.01 = 0.1 ; * 52 = 5.2 ; / 12 = 0.4333... -> 0.43
    trail = hourly_monthly_base(hourly_rate="10", verified_hours_per_week="0.01", verification_source=SOURCE)
    assert trail["output"]["monthly_base"] == "0.43"


@pytest.mark.parametrize("rate", ["0", "-15.00"])
def test_zero_or_negative_rate_rejected(rate):
    with pytest.raises(CalculationInputError):
        hourly_monthly_base(hourly_rate=rate, verified_hours_per_week="40", verification_source=SOURCE)


@pytest.mark.parametrize("bad", [25.0, 25, True])
def test_float_and_int_rate_rejected(bad):
    with pytest.raises(TypeError):
        hourly_monthly_base(hourly_rate=bad, verified_hours_per_week="40", verification_source=SOURCE)


@pytest.mark.parametrize("bad", [40.0, 40])
def test_float_and_int_hours_rejected(bad):
    with pytest.raises(TypeError):
        hourly_monthly_base(hourly_rate="25", verified_hours_per_week=bad, verification_source=SOURCE)


@pytest.mark.parametrize("bad", ["", "   ", None, 7, ["VOE"]])
def test_verification_source_required(bad):
    with pytest.raises(CalculationInputError):
        hourly_monthly_base(hourly_rate="25", verified_hours_per_week="40", verification_source=bad)


def test_decimal_inputs_accepted():
    trail = hourly_monthly_base(
        hourly_rate=Decimal("25.00"), verified_hours_per_week=Decimal("40"), verification_source=SOURCE
    )
    assert trail["output"]["monthly_base"] == "4333.33"


def test_reproducible():
    kwargs = dict(hourly_rate="18.75", verified_hours_per_week="32.5", verification_source=SOURCE)
    assert hourly_monthly_base(**kwargs) == hourly_monthly_base(**kwargs)
