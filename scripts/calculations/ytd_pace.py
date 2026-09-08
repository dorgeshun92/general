"""Year-to-date pace comparison: YTD monthly average versus an expected monthly amount."""
from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any

from ._core import CONTEXT, CalculationInputError, build_trail, money, ratio, to_date, to_decimal

METHOD = "ytd_pace_comparison"
METHOD_VERSION = "1.0.0"

DAYS_PER_YEAR = Decimal("365.25")
MONTHS_PER_YEAR = Decimal(12)
DAYS_PER_MONTH = DAYS_PER_YEAR / MONTHS_PER_YEAR  # exactly 30.4375
DEFAULT_THRESHOLD_PERCENT = "10"

FORMULA = (
    "days_elapsed = (as_of_date - period_start).days + 1  [inclusive of both dates]; "
    "months_elapsed = days_elapsed / (365.25 / 12); "
    "ytd_monthly_average = ytd_gross / months_elapsed; "
    "variance_amount = ytd_monthly_average - expected_monthly; "
    "variance_percent = variance_amount / expected_monthly * 100; "
    "exceeds_threshold = |variance_percent| > variance_threshold_percent"
)


def ytd_pace_comparison(
    *,
    ytd_gross: Any,
    period_start: Any,
    as_of_date: Any,
    expected_monthly: Any,
    variance_threshold_percent: Any = DEFAULT_THRESHOLD_PERCENT,
) -> dict[str, Any]:
    """Compare the YTD monthly average against ``expected_monthly``.

    Months elapsed use the average-month convention ``days / (365.25 / 12)`` with
    an INCLUSIVE day count (period_start and as_of_date both count), so a
    January 1 - January 31 period is 31 days = 1.0185 months. A warning is
    added when ``|variance_percent|`` strictly exceeds the threshold.
    """
    start = to_date(period_start, "period_start")
    as_of = to_date(as_of_date, "as_of_date")
    if as_of < start:
        raise CalculationInputError(
            f"as_of_date ({as_of.isoformat()}) must not be before period_start ({start.isoformat()})"
        )
    warnings: list[str] = []
    with localcontext(CONTEXT):
        ytd = to_decimal(ytd_gross, "ytd_gross")
        expected = to_decimal(expected_monthly, "expected_monthly", allow_zero=False)
        threshold = to_decimal(variance_threshold_percent, "variance_threshold_percent")

        days_elapsed = (as_of - start).days + 1
        months_elapsed = Decimal(days_elapsed) / DAYS_PER_MONTH
        average = ytd / months_elapsed
        variance_amount = average - expected
        variance_percent = variance_amount / expected * Decimal(100)
        exceeds = abs(variance_percent) > threshold

    if variance_amount > 0:
        direction = "above"
    elif variance_amount < 0:
        direction = "below"
    else:
        direction = "on_pace"

    if exceeds:
        warnings.append(
            f"YTD monthly average is {direction} expected_monthly by {ratio(abs(variance_percent))}%, "
            f"which exceeds the {ratio(threshold)}% threshold; explain the variance and route to human review"
        )

    return build_trail(
        method=METHOD,
        method_version=METHOD_VERSION,
        inputs={
            "ytd_gross": ytd,
            "period_start": start,
            "as_of_date": as_of,
            "expected_monthly": expected,
            "variance_threshold_percent": threshold,
        },
        formula=FORMULA,
        intermediate_values={
            "days_elapsed_inclusive": days_elapsed,
            "days_per_month": DAYS_PER_MONTH,
            "months_elapsed": months_elapsed,
            "ytd_monthly_average_unrounded": average,
            "variance_amount_unrounded": variance_amount,
            "variance_percent_unrounded": variance_percent,
        },
        output={
            "days_elapsed": days_elapsed,
            "months_elapsed": ratio(months_elapsed),
            "ytd_monthly_average": money(average),
            "expected_monthly": money(expected),
            "variance_amount": money(variance_amount),
            "variance_percent": ratio(variance_percent),
            "variance_threshold_percent": ratio(threshold),
            "direction": direction,
            "exceeds_threshold": exceeds,
        },
        warnings=warnings,
    )
