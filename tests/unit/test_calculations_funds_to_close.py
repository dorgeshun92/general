"""Unit, boundary and negative tests for scripts.calculations.funds_to_close."""
import pytest

from scripts.calculations import CalculationInputError, funds_to_close

BASE = dict(
    purchase_price="400000",
    loan_amount="320000",
    closing_costs="8000",
    prepaids="4000",
    seller_concessions="5000",
    earnest_money_deposit="10000",
    other_credits="0",
    verified_available_funds="90000",
    required_reserves="10000",
)


def test_golden():
    # down payment = 400000 - 320000 = 80000
    # cash_to_close = 80000 + 8000 + 4000 - 5000 - 10000 - 0 = 77000
    # total required = 77000 + 10000 = 87000
    # surplus = 90000 - 87000 = 3000 -> sufficient
    trail = funds_to_close(**BASE)
    out = trail["output"]
    assert out["down_payment"] == "80000.00"
    assert out["cash_to_close"] == "77000.00"
    assert out["total_funds_required"] == "87000.00"
    assert out["surplus_or_shortfall"] == "3000.00"
    assert out["sufficient"] is True
    assert trail["warnings"] == []
    assert trail["inputs"]["other_credits"] == "0"


def test_defaults_for_optional_credits_and_reserves():
    kwargs = {k: v for k, v in BASE.items() if k not in ("other_credits", "required_reserves")}
    trail = funds_to_close(**kwargs)
    # cash_to_close 77000 ; reserves 0 ; surplus = 90000 - 77000 = 13000
    assert trail["inputs"]["other_credits"] == "0"
    assert trail["inputs"]["required_reserves"] == "0"
    assert trail["output"]["surplus_or_shortfall"] == "13000.00"


def test_exactly_sufficient_boundary():
    trail = funds_to_close(**{**BASE, "verified_available_funds": "87000"})
    assert trail["output"]["surplus_or_shortfall"] == "0.00"
    assert trail["output"]["sufficient"] is True
    assert trail["warnings"] == []


def test_one_cent_short():
    trail = funds_to_close(**{**BASE, "verified_available_funds": "86999.99"})
    assert trail["output"]["surplus_or_shortfall"] == "-0.01"
    assert trail["output"]["sufficient"] is False
    assert any("short" in w for w in trail["warnings"])


def test_other_credits_reduce_cash_to_close():
    # 77000 - 1500 = 75500
    trail = funds_to_close(**{**BASE, "other_credits": "1500.00"})
    assert trail["output"]["cash_to_close"] == "75500.00"


def test_cents_are_exact():
    # 250000.55 - 200000.44 = 50000.11 ; + 3210.99 + 1234.56 = 54445.66 ; - 1000.01 - 2500.00 - 0.10 = 50945.55
    trail = funds_to_close(
        purchase_price="250000.55", loan_amount="200000.44", closing_costs="3210.99", prepaids="1234.56",
        seller_concessions="1000.01", earnest_money_deposit="2500.00", other_credits="0.10",
        verified_available_funds="50945.55",
    )
    assert trail["output"]["cash_to_close"] == "50945.55"
    assert trail["output"]["surplus_or_shortfall"] == "0.00"
    assert trail["output"]["sufficient"] is True


def test_loan_exceeds_price_warns():
    trail = funds_to_close(**{**BASE, "loan_amount": "400000.01"})
    assert trail["output"]["down_payment"] == "-0.01"
    assert any("exceeds purchase_price" in w for w in trail["warnings"])


def test_negative_cash_to_close_warns():
    # 80000 + 8000 + 4000 - 5000 - 100000 = -13000
    trail = funds_to_close(**{**BASE, "earnest_money_deposit": "100000"})
    assert trail["output"]["cash_to_close"] == "-13000.00"
    assert any("cash_to_close is negative" in w for w in trail["warnings"])


def test_concessions_over_costs_warns():
    # concessions 12000.01 > 8000 + 4000
    trail = funds_to_close(**{**BASE, "seller_concessions": "12000.01"})
    assert any("seller_concessions exceed" in w for w in trail["warnings"])
    # exactly equal does not warn
    trail = funds_to_close(**{**BASE, "seller_concessions": "12000.00"})
    assert not any("seller_concessions exceed" in w for w in trail["warnings"])


def test_cash_purchase_zero_loan_allowed():
    trail = funds_to_close(**{**BASE, "loan_amount": "0"})
    # 400000 + 12000 - 15000 = 397000
    assert trail["output"]["cash_to_close"] == "397000.00"


@pytest.mark.parametrize(
    "field",
    ["purchase_price", "loan_amount", "closing_costs", "prepaids", "seller_concessions",
     "earnest_money_deposit", "other_credits", "verified_available_funds", "required_reserves"],
)
def test_negative_inputs_rejected(field):
    with pytest.raises(CalculationInputError):
        funds_to_close(**{**BASE, field: "-0.01"})


def test_zero_purchase_price_rejected():
    with pytest.raises(CalculationInputError):
        funds_to_close(**{**BASE, "purchase_price": "0"})


@pytest.mark.parametrize("field", list(BASE))
@pytest.mark.parametrize("bad", [1000.0, 1000])
def test_float_and_int_rejected(field, bad):
    with pytest.raises(TypeError):
        funds_to_close(**{**BASE, field: bad})


def test_reproducible():
    assert funds_to_close(**BASE) == funds_to_close(**BASE)
