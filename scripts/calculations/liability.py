"""Liability payment comparison across the credit report, the 1003, and a supporting document."""
from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any

from ._core import CONTEXT, build_trail, money, to_decimal

METHOD = "liability_payment_comparison"
METHOD_VERSION = "1.0.0"
DEFAULT_TOLERANCE = "1.00"

SOURCE_CREDIT_REPORT = "credit_report"
SOURCE_1003 = "1003"
SOURCE_DOCUMENT = "document"
SOURCE_ORDER = (SOURCE_CREDIT_REPORT, SOURCE_1003, SOURCE_DOCUMENT)

HUMAN_REVIEW_WARNING = (
    "payment amounts differ between sources; selection of payment to use requires "
    "guideline lookup / human review"
)

FORMULA = (
    "for each pair (a, b): difference[a_minus_b] = payment[a] - payment[b]; "
    "max_absolute_difference = max(|difference|); "
    "matches_within_tolerance = max_absolute_difference <= tolerance; "
    "highest_source = source with the largest payment (ties listed in highest_sources, "
    "first in order credit_report, 1003, document)"
)


def liability_payment_comparison(
    *,
    credit_report_payment: Any,
    stated_1003_payment: Any,
    document_payment: Any = None,
    tolerance: Any = DEFAULT_TOLERANCE,
) -> dict[str, Any]:
    """Compare a liability's monthly payment as reported by up to three sources.

    Reports the highest source, every pairwise difference, and whether all
    sources agree within ``tolerance``. It never chooses which payment to use:
    whenever any two sources differ (even within tolerance) a human-review
    warning is added, because that choice is guideline-dependent.
    """
    warnings: list[str] = []
    with localcontext(CONTEXT):
        payments: dict[str, Decimal | None] = {
            SOURCE_CREDIT_REPORT: to_decimal(credit_report_payment, "credit_report_payment"),
            SOURCE_1003: to_decimal(stated_1003_payment, "stated_1003_payment"),
            SOURCE_DOCUMENT: (
                None if document_payment is None else to_decimal(document_payment, "document_payment")
            ),
        }
        tol = to_decimal(tolerance, "tolerance")
        present = [s for s in SOURCE_ORDER if payments[s] is not None]

        differences: dict[str, Decimal | None] = {}
        for i, a in enumerate(SOURCE_ORDER):
            for b in SOURCE_ORDER[i + 1 :]:
                pa, pb = payments[a], payments[b]
                differences[f"{a}_minus_{b}"] = None if pa is None or pb is None else pa - pb
        abs_differences = [abs(d) for d in differences.values() if d is not None]
        max_abs_difference = max(abs_differences)
        highest_payment = max(payments[s] for s in present)
        highest_sources = [s for s in present if payments[s] == highest_payment]
        all_equal = max_abs_difference == 0
        matches = max_abs_difference <= tol

    if not all_equal:
        warnings.append(HUMAN_REVIEW_WARNING)

    return build_trail(
        method=METHOD,
        method_version=METHOD_VERSION,
        inputs={
            "credit_report_payment": payments[SOURCE_CREDIT_REPORT],
            "stated_1003_payment": payments[SOURCE_1003],
            "document_payment": payments[SOURCE_DOCUMENT],
            "tolerance": tol,
        },
        formula=FORMULA,
        intermediate_values={
            "sources_present": present,
            "differences_unrounded": differences,
            "max_absolute_difference_unrounded": max_abs_difference,
        },
        output={
            "payments": {s: (None if p is None else money(p)) for s, p in payments.items()},
            "highest_source": highest_sources[0],
            "highest_sources": highest_sources,
            "highest_payment": money(highest_payment),
            "differences": {k: (None if d is None else money(d)) for k, d in differences.items()},
            "max_absolute_difference": money(max_abs_difference),
            "tolerance": money(tol),
            "all_equal": all_equal,
            "matches_within_tolerance": matches,
        },
        warnings=warnings,
    )
