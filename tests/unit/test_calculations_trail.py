"""Cross-cutting tests: schema validity of every method's output, reproducibility,
JSON-safety, rounding helpers, and the no-float guarantee of the package source."""
import ast
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.calculations import METHODS, ROUNDING_POLICY, build_trail
from scripts.calculations._core import CalculationInputError, fmt, money, ratio, to_date, to_decimal, to_int
from scripts.common.schema_registry import validate_ref

TRAIL_REF = "common.defs.schema.json#/$defs/calculation_trail"
PACKAGE_DIR = Path(__file__).resolve().parents[2] / "scripts" / "calculations"

# One representative call per supported method, including calls that produce warnings.
SAMPLES = {
    "salaried_annual": ("salaried_monthly_base", dict(annual_salary="65432.10")),
    "salaried_period": ("salaried_monthly_base", dict(period_gross="2307.69", pay_frequency="BIWEEKLY")),
    "salaried_zero": ("salaried_monthly_base", dict(annual_salary="0")),
    "hourly": ("hourly_monthly_base", dict(hourly_rate="18.75", verified_hours_per_week="32.5", verification_source="VOE doc-0007 p.1")),
    "hourly_overtime_warn": ("hourly_monthly_base", dict(hourly_rate="25", verified_hours_per_week="45", verification_source="VOE")),
    "ytd": ("ytd_pace_comparison", dict(ytd_gross="30000", period_start="2025-01-01", as_of_date="2025-06-30", expected_monthly="5000")),
    "ytd_warn": ("ytd_pace_comparison", dict(ytd_gross="10000", period_start="2025-01-01", as_of_date="2025-06-30", expected_monthly="5000")),
    "reconciliation": ("statement_balance_reconciliation", dict(beginning_balance="1000.00", total_deposits="500.00", total_withdrawals="200.00", ending_balance="1300.00")),
    "reconciliation_fail": ("statement_balance_reconciliation", dict(beginning_balance="-50", total_deposits="0", total_withdrawals="0", ending_balance="1")),
    "funds": ("funds_to_close", dict(purchase_price="400000", loan_amount="320000", closing_costs="8000", prepaids="4000", seller_concessions="5000", earnest_money_deposit="10000", verified_available_funds="90000", required_reserves="10000")),
    "funds_short": ("funds_to_close", dict(purchase_price="400000", loan_amount="400001", closing_costs="8000", prepaids="4000", seller_concessions="15000", earnest_money_deposit="10000", verified_available_funds="0")),
    "liability": ("liability_payment_comparison", dict(credit_report_payment="350", stated_1003_payment="340", document_payment="400")),
    "liability_two": ("liability_payment_comparison", dict(credit_report_payment="350", stated_1003_payment="350")),
    "dates": ("date_expiration_check", dict(document_date="2025-01-15", as_of_date="2025-03-15", max_age_days=120)),
    "dates_override": ("date_expiration_check", dict(document_date="2025-01-01", as_of_date="2025-02-01", max_age_days=120, expiration_date="2025-01-31")),
}

SUPPORTED_METHOD_NAMES = sorted(name for name in METHODS if name != "unsupported")


def _run(sample_key):
    method, kwargs = SAMPLES[sample_key]
    return METHODS[method](**kwargs)


def test_every_supported_method_has_a_sample():
    covered = {SAMPLES[k][0] for k in SAMPLES}
    assert covered == set(SUPPORTED_METHOD_NAMES)


@pytest.mark.parametrize("sample_key", sorted(SAMPLES))
def test_output_validates_against_calculation_trail_schema(sample_key):
    trail = _run(sample_key)
    assert validate_ref(trail, TRAIL_REF) == []


@pytest.mark.parametrize("sample_key", sorted(SAMPLES))
def test_trail_shape_and_conventions(sample_key):
    method, _ = SAMPLES[sample_key]
    trail = _run(sample_key)
    assert set(trail) == {"method", "method_version", "inputs", "formula", "intermediate_values", "output", "warnings", "rounding_policy"}
    assert trail["method"] == method
    assert trail["method_version"] == "1.0.0"
    assert trail["rounding_policy"] == ROUNDING_POLICY
    assert isinstance(trail["warnings"], list) and all(isinstance(w, str) for w in trail["warnings"])
    # inputs are recorded as strings (or null)
    for key, value in trail["inputs"].items():
        assert value is None or isinstance(value, str), (key, value)


@pytest.mark.parametrize("sample_key", sorted(SAMPLES))
def test_trail_is_json_native_without_decimals(sample_key):
    trail = _run(sample_key)
    text = json.dumps(trail)  # would raise on Decimal / date objects
    assert json.loads(text) == trail

    def walk(value):
        assert not isinstance(value, (float, Decimal)), value
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(trail)


@pytest.mark.parametrize("sample_key", sorted(SAMPLES))
def test_reproducible_and_independent_copies(sample_key):
    first = _run(sample_key)
    snapshot = copy.deepcopy(first)
    second = _run(sample_key)
    assert first == second
    assert first is not second
    assert first == snapshot


@pytest.mark.parametrize("sample_key", sorted(SAMPLES))
def test_money_outputs_are_two_place_decimal_strings(sample_key):
    trail = _run(sample_key)
    for key, value in trail["output"].items():
        if isinstance(value, str) and value.replace("-", "", 1).replace(".", "", 1).isdigit():
            assert "." in value, (key, value)


def test_no_float_literals_or_calls_in_package_source():
    """The package must never construct a float: no float literals, no float() calls."""
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                raise AssertionError(f"{path.name}:{node.lineno} float literal {node.value!r}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
                raise AssertionError(f"{path.name}:{node.lineno} float() call")


# ---- helper-level tests ---------------------------------------------------- #
def test_money_rounds_half_up_once():
    assert money(Decimal("2.675")) == "2.68"
    assert money(Decimal("2.665")) == "2.67"
    assert money(Decimal("-2.675")) == "-2.68"
    assert money(Decimal("0.004")) == "0.00"
    assert money(Decimal("-0.004")) == "0.00"  # never -0.00
    assert money(Decimal("1E+2")) == "100.00"


def test_ratio_rounds_four_places():
    assert ratio(Decimal("0.89779005")) == "0.8978"
    assert ratio(Decimal("10")) == "10.0000"
    assert ratio(Decimal("-0.00004")) == "0.0000"


def test_fmt_is_exponent_free_full_precision():
    assert fmt(Decimal("1E+3")) == "1000"
    assert fmt(Decimal("4999.995")) == "4999.995"
    assert fmt(Decimal("-0")) == "0"
    assert fmt(Decimal("0E-10")) == "0"


def test_to_decimal_rules():
    assert to_decimal("  12.50 ", "x") == Decimal("12.50")
    assert to_decimal(Decimal("-1"), "x", allow_negative=True) == Decimal("-1")
    with pytest.raises(TypeError):
        to_decimal(1.5, "x")
    with pytest.raises(TypeError):
        to_decimal(1, "x")
    with pytest.raises(TypeError):
        to_decimal(None, "x")
    with pytest.raises(CalculationInputError):
        to_decimal(Decimal("NaN"), "x")
    with pytest.raises(CalculationInputError):
        to_decimal(Decimal("Infinity"), "x")
    with pytest.raises(CalculationInputError):
        to_decimal("0", "x", allow_zero=False)


def test_to_int_and_to_date_rules():
    assert to_int(5, "d") == 5
    assert to_int("5", "d") == 5
    with pytest.raises(TypeError):
        to_int(5.0, "d")
    with pytest.raises(TypeError):
        to_int(Decimal(5), "d")
    with pytest.raises(CalculationInputError):
        to_int(-1, "d", minimum=0)
    assert to_date("2024-02-29", "d").isoformat() == "2024-02-29"
    with pytest.raises(CalculationInputError):
        to_date("2023-02-29", "d")
    with pytest.raises(TypeError):
        to_date(20240229, "d")


def test_build_trail_rejects_floats_and_validates():
    trail = build_trail(
        method="m", method_version="1.0.0", inputs={"a": Decimal("1.5"), "n": 3, "none": None},
        formula="a", intermediate_values={"b": [Decimal("2")]}, output={"c": "1.50", "ok": True}, warnings=[],
    )
    assert trail["inputs"] == {"a": "1.5", "n": "3", "none": None}
    assert trail["intermediate_values"] == {"b": ["2"]}
    assert validate_ref(trail, TRAIL_REF) == []
    with pytest.raises(TypeError):
        build_trail(method="m", method_version="1.0.0", inputs={"a": 1.5}, formula="a",
                    intermediate_values={}, output=None, warnings=[])
