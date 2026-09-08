"""Deterministic Decimal calculations for the Mpire mortgage operations copilot (MVP 1).

Every public function returns a plain dict matching
``common.defs.schema.json#/$defs/calculation_trail``: method, method_version,
inputs, formula, intermediate_values, output, warnings, rounding_policy.
See README.md in this package for the method table and the rounding policy.
"""
from __future__ import annotations

from ._core import ROUNDING_POLICY, CalculationInputError, build_trail
from .dates import date_expiration_check
from .funds_to_close import funds_to_close
from .hourly import hourly_monthly_base
from .liability import liability_payment_comparison
from .reconciliation import statement_balance_reconciliation
from .salaried import salaried_monthly_base
from .unsupported import UNSUPPORTED_KINDS, UnsupportedCalculation, unsupported
from .ytd_pace import ytd_pace_comparison

METHODS = {
    "salaried_monthly_base": salaried_monthly_base,
    "hourly_monthly_base": hourly_monthly_base,
    "ytd_pace_comparison": ytd_pace_comparison,
    "statement_balance_reconciliation": statement_balance_reconciliation,
    "funds_to_close": funds_to_close,
    "liability_payment_comparison": liability_payment_comparison,
    "date_expiration_check": date_expiration_check,
    "unsupported": unsupported,
}

__all__ = [
    "ROUNDING_POLICY",
    "CalculationInputError",
    "UnsupportedCalculation",
    "UNSUPPORTED_KINDS",
    "METHODS",
    "build_trail",
    "salaried_monthly_base",
    "hourly_monthly_base",
    "ytd_pace_comparison",
    "statement_balance_reconciliation",
    "funds_to_close",
    "liability_payment_comparison",
    "date_expiration_check",
    "unsupported",
]
