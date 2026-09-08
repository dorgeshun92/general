"""Funds-to-close arithmetic and sufficiency of verified funds."""
from __future__ import annotations

from decimal import localcontext
from typing import Any

from ._core import CONTEXT, build_trail, money, to_decimal

METHOD = "funds_to_close"
METHOD_VERSION = "1.0.0"

FORMULA = (
    "cash_to_close = purchase_price - loan_amount + closing_costs + prepaids "
    "- seller_concessions - earnest_money_deposit - other_credits; "
    "total_funds_required = cash_to_close + required_reserves; "
    "surplus_or_shortfall = verified_available_funds - total_funds_required; "
    "sufficient = surplus_or_shortfall >= 0"
)


def funds_to_close(
    *,
    purchase_price: Any,
    loan_amount: Any,
    closing_costs: Any,
    prepaids: Any,
    seller_concessions: Any,
    earnest_money_deposit: Any,
    verified_available_funds: Any,
    other_credits: Any = "0",
    required_reserves: Any = "0",
) -> dict[str, Any]:
    """Cash to close plus a surplus/shortfall against verified funds and reserves.

    Every input is a non-negative decimal string (or Decimal); ``purchase_price``
    must be positive. ``other_credits`` and ``required_reserves`` default to 0.
    This is arithmetic only: allowable concession limits, reserve requirements
    and which funds count as verified are guideline decisions made elsewhere.
    """
    warnings: list[str] = []
    with localcontext(CONTEXT):
        price = to_decimal(purchase_price, "purchase_price", allow_zero=False)
        loan = to_decimal(loan_amount, "loan_amount")
        costs = to_decimal(closing_costs, "closing_costs")
        prepaid = to_decimal(prepaids, "prepaids")
        concessions = to_decimal(seller_concessions, "seller_concessions")
        emd = to_decimal(earnest_money_deposit, "earnest_money_deposit")
        credits = to_decimal(other_credits, "other_credits")
        available = to_decimal(verified_available_funds, "verified_available_funds")
        reserves = to_decimal(required_reserves, "required_reserves")

        down_payment = price - loan
        gross_costs = costs + prepaid
        total_credits = concessions + emd + credits
        cash_to_close = down_payment + gross_costs - total_credits
        total_required = cash_to_close + reserves
        surplus = available - total_required
        sufficient = surplus >= 0

    if loan > price:
        warnings.append(
            "loan_amount exceeds purchase_price (negative down payment); confirm the loan structure with human review"
        )
    if cash_to_close < 0:
        warnings.append(
            "cash_to_close is negative (credits exceed costs and down payment); "
            "whether excess credits may be returned to the borrower is guideline-dependent and requires review"
        )
    if concessions > gross_costs:
        warnings.append(
            "seller_concessions exceed closing_costs plus prepaids; allowable concession limits are "
            "guideline-dependent and require review"
        )
    if not sufficient:
        warnings.append(
            f"verified_available_funds are short of total_funds_required by {money(abs(surplus))}"
        )

    return build_trail(
        method=METHOD,
        method_version=METHOD_VERSION,
        inputs={
            "purchase_price": price,
            "loan_amount": loan,
            "closing_costs": costs,
            "prepaids": prepaid,
            "seller_concessions": concessions,
            "earnest_money_deposit": emd,
            "other_credits": credits,
            "verified_available_funds": available,
            "required_reserves": reserves,
        },
        formula=FORMULA,
        intermediate_values={
            "down_payment": down_payment,
            "gross_costs": gross_costs,
            "total_credits": total_credits,
            "cash_to_close_unrounded": cash_to_close,
            "total_funds_required_unrounded": total_required,
            "surplus_or_shortfall_unrounded": surplus,
        },
        output={
            "down_payment": money(down_payment),
            "cash_to_close": money(cash_to_close),
            "required_reserves": money(reserves),
            "total_funds_required": money(total_required),
            "verified_available_funds": money(available),
            "surplus_or_shortfall": money(surplus),
            "sufficient": sufficient,
        },
        warnings=warnings,
    )
