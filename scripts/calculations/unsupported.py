"""Explicitly unsupported calculation kinds.

These income and asset calculations depend on guideline interpretation
(averaging periods, trending, add-backs, occupancy factors, term selection)
and are NOT implemented until a licensed reviewer supplies written
calculation specifications. Callers must classify the documents and route the
item to REVIEW instead of computing anything.
"""
from __future__ import annotations

from typing import Any

from ._core import CalculationInputError

UNSUPPORTED_KINDS: tuple[str, ...] = (
    "COMMISSION",
    "BONUS",
    "OVERTIME",
    "RENTAL",
    "SELF_EMPLOYED",
    "ASSET_DEPLETION",
    "GUIDELINE_DEPENDENT",
)


class UnsupportedCalculation(NotImplementedError):
    """Raised for calculation kinds that require written specifications from a licensed reviewer."""

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(
            f"{kind} calculations are not supported in this release: written calculation "
            "specifications from a licensed reviewer are required before implementation. "
            "Classify the documents and route the item to REVIEW."
        )


def unsupported(kind: Any) -> None:
    """Always raises. ``UnsupportedCalculation`` for a known unsupported kind, otherwise CalculationInputError."""
    if not isinstance(kind, str):
        raise CalculationInputError(f"kind: must be one of {list(UNSUPPORTED_KINDS)}")
    normalized = kind.strip().upper().replace("-", "_").replace(" ", "_")
    if normalized in UNSUPPORTED_KINDS:
        raise UnsupportedCalculation(normalized)
    raise CalculationInputError(
        f"kind: unknown calculation kind '{kind}'; unsupported kinds are {list(UNSUPPORTED_KINDS)}"
    )
