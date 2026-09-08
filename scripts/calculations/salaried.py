"""Salaried monthly base income from an annual salary or a per-period gross amount."""
from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any

from ._core import CONTEXT, CalculationInputError, build_trail, money, to_decimal

METHOD = "salaried_monthly_base"
METHOD_VERSION = "1.0.0"

PAY_PERIODS_PER_YEAR: dict[str, int] = {
    "WEEKLY": 52,
    "BIWEEKLY": 26,
    "SEMIMONTHLY": 24,
    "MONTHLY": 12,
    "ANNUAL": 1,
}

FORMULA_ANNUAL = "monthly_base = annual_salary / 12"
FORMULA_PERIOD = (
    "annualized_salary = period_gross * periods_per_year[pay_frequency]; "
    "monthly_base = annualized_salary / 12"
)


def normalize_frequency(value: Any) -> str:
    if not isinstance(value, str):
        raise CalculationInputError(
            f"pay_frequency: must be one of {sorted(PAY_PERIODS_PER_YEAR)}, not {type(value).__name__}"
        )
    key = value.strip().upper().replace("-", "").replace("_", "")
    if key not in PAY_PERIODS_PER_YEAR:
        raise CalculationInputError(
            f"pay_frequency: unknown frequency '{value}'; expected one of {sorted(PAY_PERIODS_PER_YEAR)}"
        )
    return key


def salaried_monthly_base(
    *,
    annual_salary: Any = None,
    period_gross: Any = None,
    pay_frequency: Any = None,
) -> dict[str, Any]:
    """Monthly base salary.

    Provide EITHER ``annual_salary`` OR both ``period_gross`` and
    ``pay_frequency`` (WEEKLY=52, BIWEEKLY=26, SEMIMONTHLY=24, MONTHLY=12,
    ANNUAL=1). Supplying both paths, or an incomplete period path, is rejected.
    """
    if annual_salary is not None and (period_gross is not None or pay_frequency is not None):
        raise CalculationInputError(
            "provide either annual_salary or (period_gross and pay_frequency), not both"
        )
    if annual_salary is None and (period_gross is None or pay_frequency is None):
        raise CalculationInputError(
            "provide annual_salary, or both period_gross and pay_frequency"
        )

    warnings: list[str] = []
    with localcontext(CONTEXT):
        if annual_salary is not None:
            annual = to_decimal(annual_salary, "annual_salary")
            frequency = None
            periods = None
            gross = None
            formula = FORMULA_ANNUAL
        else:
            gross = to_decimal(period_gross, "period_gross")
            frequency = normalize_frequency(pay_frequency)
            periods = Decimal(PAY_PERIODS_PER_YEAR[frequency])
            annual = gross * periods
            formula = FORMULA_PERIOD
        monthly = annual / Decimal(12)

    if annual == 0:
        warnings.append(
            "annualized salary is zero; confirm the income source before relying on this value"
        )

    return build_trail(
        method=METHOD,
        method_version=METHOD_VERSION,
        inputs={
            "annual_salary": annual if annual_salary is not None else None,
            "period_gross": gross,
            "pay_frequency": frequency,
        },
        formula=formula,
        intermediate_values={
            "periods_per_year": periods,
            "annualized_salary": annual,
            "monthly_base_unrounded": monthly,
        },
        output={
            "monthly_base": money(monthly),
            "annualized_salary": money(annual),
        },
        warnings=warnings,
    )
