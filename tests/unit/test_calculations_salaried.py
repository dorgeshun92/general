"""Unit, boundary and negative tests for scripts.calculations.salaried."""
from decimal import Decimal

import pytest

from scripts.calculations import CalculationInputError, salaried_monthly_base
from scripts.calculations.salaried import PAY_PERIODS_PER_YEAR


def test_annual_salary_golden():
    # 60000 / 12 = 5000.00
    trail = salaried_monthly_base(annual_salary="60000")
    assert trail["output"]["monthly_base"] == "5000.00"
    assert trail["output"]["annualized_salary"] == "60000.00"
    assert trail["inputs"] == {"annual_salary": "60000", "period_gross": None, "pay_frequency": None}
    assert trail["intermediate_values"]["periods_per_year"] is None
    assert trail["warnings"] == []


def test_annual_salary_rounds_half_up_at_final_step():
    # 65432.10 / 12 = 5452.675 exactly -> ROUND_HALF_UP -> 5452.68
    # (banker's rounding or float arithmetic would give 5452.67 / 5452.674999...)
    trail = salaried_monthly_base(annual_salary="65432.10")
    assert trail["intermediate_values"]["monthly_base_unrounded"] == "5452.675"
    assert trail["output"]["monthly_base"] == "5452.68"


def test_biweekly_golden_half_up():
    # 2307.69 * 26 = 59999.94 ; / 12 = 4999.995 -> ROUND_HALF_UP -> 5000.00
    trail = salaried_monthly_base(period_gross="2307.69", pay_frequency="BIWEEKLY")
    assert trail["intermediate_values"]["periods_per_year"] == "26"
    assert trail["intermediate_values"]["annualized_salary"] == "59999.94"
    assert trail["intermediate_values"]["monthly_base_unrounded"] == "4999.995"
    assert trail["output"]["monthly_base"] == "5000.00"
    assert trail["output"]["annualized_salary"] == "59999.94"


@pytest.mark.parametrize(
    "gross, frequency, expected",
    [
        # 1000 * 52 = 52000 ; / 12 = 4333.333... -> 4333.33
        ("1000", "WEEKLY", "4333.33"),
        # 2500 * 24 = 60000 ; / 12 = 5000
        ("2500", "SEMIMONTHLY", "5000.00"),
        # 4200 * 12 = 50400 ; / 12 = 4200
        ("4200", "MONTHLY", "4200.00"),
        # 60000 * 1 = 60000 ; / 12 = 5000
        ("60000", "ANNUAL", "5000.00"),
        # 1153.85 * 26 = 30000.10 ; / 12 = 2500.008333... -> 2500.01
        ("1153.85", "BIWEEKLY", "2500.01"),
    ],
)
def test_frequencies(gross, frequency, expected):
    trail = salaried_monthly_base(period_gross=gross, pay_frequency=frequency)
    assert trail["output"]["monthly_base"] == expected
    assert trail["inputs"]["pay_frequency"] == frequency


def test_frequency_is_case_and_separator_insensitive():
    trail = salaried_monthly_base(period_gross="1000", pay_frequency=" bi-weekly ")
    assert trail["inputs"]["pay_frequency"] == "BIWEEKLY"
    assert trail["output"]["monthly_base"] == "2166.67"  # 1000*26/12 = 2166.666... -> 2166.67


def test_frequency_table_matches_spec():
    assert PAY_PERIODS_PER_YEAR == {"WEEKLY": 52, "BIWEEKLY": 26, "SEMIMONTHLY": 24, "MONTHLY": 12, "ANNUAL": 1}


def test_decimal_input_accepted():
    trail = salaried_monthly_base(annual_salary=Decimal("60000.00"))
    assert trail["output"]["monthly_base"] == "5000.00"
    assert trail["inputs"]["annual_salary"] == "60000.00"


def test_zero_salary_boundary_warns():
    trail = salaried_monthly_base(annual_salary="0")
    assert trail["output"]["monthly_base"] == "0.00"
    assert any("zero" in w for w in trail["warnings"])


def test_small_salary_boundary():
    # 0.01 / 12 = 0.000833... -> 0.00
    trail = salaried_monthly_base(annual_salary="0.01")
    assert trail["output"]["monthly_base"] == "0.00"
    assert trail["warnings"] == []


@pytest.mark.parametrize("bad", [60000.0, 60000, True])
def test_float_and_int_money_rejected(bad):
    with pytest.raises(TypeError):
        salaried_monthly_base(annual_salary=bad)
    with pytest.raises(TypeError):
        salaried_monthly_base(period_gross=bad, pay_frequency="MONTHLY")


def test_negative_salary_rejected():
    with pytest.raises(CalculationInputError):
        salaried_monthly_base(annual_salary="-1")
    with pytest.raises(CalculationInputError):
        salaried_monthly_base(period_gross="-0.01", pay_frequency="WEEKLY")


@pytest.mark.parametrize("bad", ["", "abc", "1,000", "1e5", "$100", "NaN", "Infinity", "1.", ".5"])
def test_malformed_decimal_strings_rejected(bad):
    with pytest.raises(CalculationInputError):
        salaried_monthly_base(annual_salary=bad)


@pytest.mark.parametrize("bad", ["DAILY", "FORTNIGHTLY", "", "52", None, 26])
def test_unknown_frequency_rejected(bad):
    with pytest.raises(CalculationInputError):
        salaried_monthly_base(period_gross="1000", pay_frequency=bad)


def test_both_paths_rejected():
    with pytest.raises(CalculationInputError):
        salaried_monthly_base(annual_salary="60000", period_gross="5000", pay_frequency="MONTHLY")
    with pytest.raises(CalculationInputError):
        salaried_monthly_base(annual_salary="60000", pay_frequency="MONTHLY")


def test_incomplete_period_path_rejected():
    with pytest.raises(CalculationInputError):
        salaried_monthly_base(period_gross="5000")
    with pytest.raises(CalculationInputError):
        salaried_monthly_base(pay_frequency="MONTHLY")
    with pytest.raises(CalculationInputError):
        salaried_monthly_base()


def test_reproducible():
    a = salaried_monthly_base(period_gross="2307.69", pay_frequency="BIWEEKLY")
    b = salaried_monthly_base(period_gross="2307.69", pay_frequency="BIWEEKLY")
    assert a == b
