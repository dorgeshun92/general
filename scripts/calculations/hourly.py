"""Hourly monthly base income from an hourly rate and verified weekly hours."""
from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any

from ._core import CONTEXT, CalculationInputError, build_trail, money, to_decimal, to_text

METHOD = "hourly_monthly_base"
METHOD_VERSION = "1.0.0"

WEEKS_PER_YEAR = Decimal(52)
MONTHS_PER_YEAR = Decimal(12)
STANDARD_FULL_TIME_HOURS = Decimal(40)
MAX_HOURS_PER_WEEK = Decimal(168)

FORMULA = (
    "weekly_gross = hourly_rate * verified_hours_per_week; "
    "annual_gross = weekly_gross * 52; monthly_base = annual_gross / 12"
)


def hourly_monthly_base(
    *,
    hourly_rate: Any,
    verified_hours_per_week: Any,
    verification_source: Any,
) -> dict[str, Any]:
    """Monthly base from ``hourly_rate * verified_hours_per_week * 52 / 12``.

    ``verification_source`` is recorded verbatim in the trail (e.g. "VOE dated
    2025-03-01, doc-0007 p.1"); it is required but NOT validated for truth.
    Hours above 40 produce a warning because overtime is not a supported
    calculation and must be excluded from base income.
    """
    source = to_text(verification_source, "verification_source")
    warnings: list[str] = []
    with localcontext(CONTEXT):
        rate = to_decimal(hourly_rate, "hourly_rate", allow_zero=False)
        hours = to_decimal(verified_hours_per_week, "verified_hours_per_week", allow_negative=True)
        if hours <= 0:
            raise CalculationInputError(
                f"verified_hours_per_week: must be greater than zero, got {hours}"
            )
        if hours > MAX_HOURS_PER_WEEK:
            raise CalculationInputError(
                f"verified_hours_per_week: must not exceed {MAX_HOURS_PER_WEEK} (hours in a week), got {hours}"
            )
        weekly = rate * hours
        annual = weekly * WEEKS_PER_YEAR
        monthly = annual / MONTHS_PER_YEAR

    if hours > STANDARD_FULL_TIME_HOURS:
        warnings.append(
            f"verified_hours_per_week ({hours}) exceeds 40; base income must exclude overtime. "
            "Overtime is not a supported calculation and requires human review."
        )

    return build_trail(
        method=METHOD,
        method_version=METHOD_VERSION,
        inputs={
            "hourly_rate": rate,
            "verified_hours_per_week": hours,
            "verification_source": source,
        },
        formula=FORMULA,
        intermediate_values={
            "weekly_gross": weekly,
            "weeks_per_year": WEEKS_PER_YEAR,
            "annual_gross": annual,
            "months_per_year": MONTHS_PER_YEAR,
            "monthly_base_unrounded": monthly,
        },
        output={
            "monthly_base": money(monthly),
            "annual_gross": money(annual),
        },
        warnings=warnings,
    )
