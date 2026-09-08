"""Bank/asset statement balance reconciliation."""
from __future__ import annotations

from decimal import localcontext
from typing import Any

from ._core import CONTEXT, build_trail, money, to_decimal

METHOD = "statement_balance_reconciliation"
METHOD_VERSION = "1.0.0"
DEFAULT_TOLERANCE = "0.00"

FORMULA = (
    "computed_ending_balance = beginning_balance + total_deposits - total_withdrawals; "
    "difference = ending_balance - computed_ending_balance; "
    "reconciles = |difference| <= tolerance"
)


def statement_balance_reconciliation(
    *,
    beginning_balance: Any,
    total_deposits: Any,
    total_withdrawals: Any,
    ending_balance: Any,
    tolerance: Any = DEFAULT_TOLERANCE,
) -> dict[str, Any]:
    """Check ``beginning + deposits - withdrawals`` against the stated ending balance.

    Balances may be negative (overdrafts); deposit and withdrawal totals and
    the tolerance must be non-negative. ``difference`` is stated minus computed.
    """
    warnings: list[str] = []
    with localcontext(CONTEXT):
        beginning = to_decimal(beginning_balance, "beginning_balance", allow_negative=True)
        deposits = to_decimal(total_deposits, "total_deposits")
        withdrawals = to_decimal(total_withdrawals, "total_withdrawals")
        ending = to_decimal(ending_balance, "ending_balance", allow_negative=True)
        tol = to_decimal(tolerance, "tolerance")

        computed = beginning + deposits - withdrawals
        difference = ending - computed
        reconciles = abs(difference) <= tol

    if not reconciles:
        warnings.append(
            f"statement does not reconcile: stated ending balance differs from computed by "
            f"{money(difference)} (tolerance {money(tol)}); check for missed transactions or a misread balance"
        )

    return build_trail(
        method=METHOD,
        method_version=METHOD_VERSION,
        inputs={
            "beginning_balance": beginning,
            "total_deposits": deposits,
            "total_withdrawals": withdrawals,
            "ending_balance": ending,
            "tolerance": tol,
        },
        formula=FORMULA,
        intermediate_values={
            "computed_ending_balance": computed,
            "difference_unrounded": difference,
            "absolute_difference": abs(difference),
        },
        output={
            "computed_ending_balance": money(computed),
            "stated_ending_balance": money(ending),
            "difference": money(difference),
            "tolerance": money(tol),
            "reconciles": reconciles,
        },
        warnings=warnings,
    )
