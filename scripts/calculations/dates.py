"""Document age and expiration checks."""
from __future__ import annotations

import datetime as _dt
from typing import Any

from ._core import CalculationInputError, build_trail, to_date, to_int

METHOD = "date_expiration_check"
METHOD_VERSION = "1.0.0"

BASIS_MAX_AGE = "max_age_days"
BASIS_EXPIRATION_DATE = "expiration_date"

FORMULA = (
    "age_days = (as_of_date - document_date).days; "
    "effective_expiration_date = expiration_date if given else document_date + max_age_days; "
    "days_remaining = (effective_expiration_date - as_of_date).days; "
    "expired = days_remaining < 0  [a document is still valid ON its expiration date]"
)


def date_expiration_check(
    *,
    document_date: Any,
    as_of_date: Any,
    max_age_days: Any = None,
    expiration_date: Any = None,
) -> dict[str, Any]:
    """Age a document and decide whether it has expired as of ``as_of_date``.

    Provide ``max_age_days`` (int, >= 0) and/or an explicit ``expiration_date``;
    when both are given the explicit ``expiration_date`` overrides the max-age
    rule. A document is valid on its expiration date and expired the day after.
    """
    doc = to_date(document_date, "document_date")
    as_of = to_date(as_of_date, "as_of_date")
    if as_of < doc:
        raise CalculationInputError(
            f"as_of_date ({as_of.isoformat()}) must not be before document_date ({doc.isoformat()})"
        )
    if max_age_days is None and expiration_date is None:
        raise CalculationInputError("provide max_age_days and/or expiration_date")

    max_age = None if max_age_days is None else to_int(max_age_days, "max_age_days", minimum=0)
    explicit = None if expiration_date is None else to_date(expiration_date, "expiration_date")
    if explicit is not None and explicit < doc:
        raise CalculationInputError(
            f"expiration_date ({explicit.isoformat()}) must not be before document_date ({doc.isoformat()})"
        )

    age_days = (as_of - doc).days
    max_age_expiration = None if max_age is None else doc + _dt.timedelta(days=max_age)
    if explicit is not None:
        basis = BASIS_EXPIRATION_DATE
        effective = explicit
    else:
        basis = BASIS_MAX_AGE
        effective = max_age_expiration
    days_remaining = (effective - as_of).days
    expired = days_remaining < 0

    warnings: list[str] = []
    if expired:
        warnings.append(
            f"document expired on {effective.isoformat()} ({-days_remaining} day(s) before as_of_date)"
        )
    if explicit is not None and max_age_expiration is not None and explicit != max_age_expiration:
        warnings.append(
            f"explicit expiration_date {explicit.isoformat()} overrides the max_age_days rule "
            f"(which would expire on {max_age_expiration.isoformat()})"
        )

    return build_trail(
        method=METHOD,
        method_version=METHOD_VERSION,
        inputs={
            "document_date": doc,
            "as_of_date": as_of,
            "max_age_days": max_age,
            "expiration_date": explicit,
        },
        formula=FORMULA,
        intermediate_values={
            "age_days": age_days,
            "max_age_expiration_date": max_age_expiration,
            "explicit_expiration_date": explicit,
            "effective_expiration_date": effective,
            "days_remaining": days_remaining,
        },
        output={
            "age_days": age_days,
            "expired": expired,
            "days_remaining": days_remaining,
            "effective_expiration_date": effective.isoformat(),
            "basis": basis,
        },
        warnings=warnings,
    )
