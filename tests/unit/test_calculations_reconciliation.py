"""Unit, boundary and negative tests for scripts.calculations.reconciliation."""
import pytest

from scripts.calculations import CalculationInputError, statement_balance_reconciliation


def test_golden_reconciles():
    # 1000.00 + 500.00 - 200.00 = 1300.00 ; stated 1300.00 ; difference 0.00
    trail = statement_balance_reconciliation(
        beginning_balance="1000.00", total_deposits="500.00", total_withdrawals="200.00", ending_balance="1300.00"
    )
    assert trail["output"]["computed_ending_balance"] == "1300.00"
    assert trail["output"]["difference"] == "0.00"
    assert trail["output"]["reconciles"] is True
    assert trail["output"]["tolerance"] == "0.00"
    assert trail["inputs"]["tolerance"] == "0.00"
    assert trail["warnings"] == []


def test_one_cent_off_fails_at_zero_tolerance():
    # computed 1300.00 ; stated 1300.01 ; difference = +0.01 > 0.00
    trail = statement_balance_reconciliation(
        beginning_balance="1000.00", total_deposits="500.00", total_withdrawals="200.00", ending_balance="1300.01"
    )
    assert trail["output"]["difference"] == "0.01"
    assert trail["output"]["reconciles"] is False
    assert len(trail["warnings"]) == 1


def test_exactly_at_tolerance_reconciles():
    trail = statement_balance_reconciliation(
        beginning_balance="1000.00", total_deposits="500.00", total_withdrawals="200.00",
        ending_balance="1300.01", tolerance="0.01",
    )
    assert trail["output"]["reconciles"] is True


def test_just_over_tolerance_fails():
    trail = statement_balance_reconciliation(
        beginning_balance="1000.00", total_deposits="500.00", total_withdrawals="200.00",
        ending_balance="1300.01", tolerance="0.009",
    )
    assert trail["output"]["reconciles"] is False


def test_negative_difference_is_signed_stated_minus_computed():
    # computed 1300.00 ; stated 1299.50 ; difference = -0.50
    trail = statement_balance_reconciliation(
        beginning_balance="1000.00", total_deposits="500.00", total_withdrawals="200.00", ending_balance="1299.50"
    )
    assert trail["output"]["difference"] == "-0.50"
    assert trail["intermediate_values"]["absolute_difference"] == "0.50"
    assert trail["output"]["reconciles"] is False


def test_overdraft_balances_allowed():
    # -50.00 + 100.00 - 20.00 = 30.00
    trail = statement_balance_reconciliation(
        beginning_balance="-50.00", total_deposits="100.00", total_withdrawals="20.00", ending_balance="30.00"
    )
    assert trail["output"]["computed_ending_balance"] == "30.00"
    assert trail["output"]["reconciles"] is True


def test_negative_ending_allowed():
    # 10 + 0 - 25 = -15
    trail = statement_balance_reconciliation(
        beginning_balance="10", total_deposits="0", total_withdrawals="25", ending_balance="-15"
    )
    assert trail["output"]["computed_ending_balance"] == "-15.00"
    assert trail["output"]["reconciles"] is True


def test_all_zero_boundary():
    trail = statement_balance_reconciliation(
        beginning_balance="0", total_deposits="0", total_withdrawals="0", ending_balance="0"
    )
    assert trail["output"]["difference"] == "0.00"
    assert trail["output"]["reconciles"] is True


@pytest.mark.parametrize("field", ["total_deposits", "total_withdrawals", "tolerance"])
def test_negative_totals_and_tolerance_rejected(field):
    kwargs = dict(beginning_balance="0", total_deposits="0", total_withdrawals="0", ending_balance="0")
    kwargs[field] = "-0.01"
    with pytest.raises(CalculationInputError):
        statement_balance_reconciliation(**kwargs)


@pytest.mark.parametrize("field", ["beginning_balance", "total_deposits", "total_withdrawals", "ending_balance"])
@pytest.mark.parametrize("bad", [100.0, 100])
def test_float_and_int_rejected(field, bad):
    kwargs = dict(beginning_balance="0", total_deposits="0", total_withdrawals="0", ending_balance="0")
    kwargs[field] = bad
    with pytest.raises(TypeError):
        statement_balance_reconciliation(**kwargs)


def test_reproducible():
    kwargs = dict(beginning_balance="1000.00", total_deposits="500.00", total_withdrawals="200.00", ending_balance="1300.01")
    assert statement_balance_reconciliation(**kwargs) == statement_balance_reconciliation(**kwargs)
