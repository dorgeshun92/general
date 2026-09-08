"""Shared primitives for scripts/calculations.

ROUNDING POLICY (applies to every method in this package)
- All arithmetic uses decimal.Decimal under a fixed context (28 significant
  digits, ROUND_HALF_UP). Floats are never created, accepted, or emitted.
- Intermediate values are kept at full context precision and are reported
  unrounded under ``intermediate_values`` so a reviewer can re-derive them.
- Money outputs are quantized to 2 decimal places with ROUND_HALF_UP exactly
  once, at the final output step.
- Percentages and ratios are quantized to 4 decimal places with
  ROUND_HALF_UP exactly once, at the final output step.
- Tolerance / threshold comparisons use the unrounded value, so the reported
  intermediate value (not a rounding artefact) decides the outcome.

INPUT POLICY
- Money, rates, percentages and hours: ``Decimal`` or a decimal string such as
  "1234.56". ``float`` and bare ``int`` raise ``TypeError`` (an int is
  ambiguous as money and a float is never exact).
- Day counts: ``int`` (or a digit string). ``bool`` is rejected.
- Dates: ``datetime.date`` or an ISO 8601 "YYYY-MM-DD" string.
"""
from __future__ import annotations

import datetime as _dt
import re
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Any, Mapping

ROUNDING_POLICY = (
    "Decimal arithmetic at 28 significant digits (ROUND_HALF_UP context); "
    "intermediate values reported unrounded; money quantized to 0.01 ROUND_HALF_UP "
    "at the final output step only; percentages and ratios quantized to 0.0001 "
    "ROUND_HALF_UP at the final output step only; tolerance and threshold "
    "comparisons use unrounded values"
)

MONEY_QUANTUM = Decimal("0.01")
RATIO_QUANTUM = Decimal("0.0001")
CONTEXT = Context(prec=28, rounding=ROUND_HALF_UP)

_DECIMAL_STRING_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
_INT_STRING_RE = re.compile(r"^-?[0-9]+$")
_ISO_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class CalculationInputError(ValueError):
    """An input failed validation (bad value, range, format, or combination)."""


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #
def to_decimal(
    value: Any,
    name: str,
    *,
    allow_negative: bool = False,
    allow_zero: bool = True,
) -> Decimal:
    """Coerce a Decimal or decimal string to Decimal. Rejects float, int, bool, NaN, Infinity."""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        raise TypeError(
            f"{name}: must be a Decimal or a decimal string such as '1234.56', "
            f"not {type(value).__name__}; floats and bare integers are rejected for money, rate, "
            "percentage and hour inputs"
        )
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, str):
        text = value.strip()
        if not _DECIMAL_STRING_RE.match(text):
            raise CalculationInputError(
                f"{name}: '{value}' is not a decimal string (expected digits with an optional "
                "sign and fractional part, e.g. '1234.56')"
            )
        result = Decimal(text)
    else:
        raise TypeError(
            f"{name}: must be a Decimal or a decimal string, not {type(value).__name__}"
        )
    if not result.is_finite():
        raise CalculationInputError(f"{name}: must be a finite number, got {value!r}")
    if result < 0 and not allow_negative:
        raise CalculationInputError(f"{name}: must not be negative, got {result}")
    if result == 0 and not allow_zero:
        raise CalculationInputError(f"{name}: must be greater than zero, got {result}")
    return result


def to_int(value: Any, name: str, *, minimum: int | None = None) -> int:
    """Coerce an int (or digit string) day count. Rejects bool, float, Decimal."""
    if isinstance(value, bool):
        raise TypeError(f"{name}: must be an integer day count, not bool")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and _INT_STRING_RE.match(value.strip()):
        result = int(value.strip())
    else:
        raise TypeError(
            f"{name}: must be an integer day count, not {type(value).__name__} ({value!r})"
        )
    if minimum is not None and result < minimum:
        raise CalculationInputError(f"{name}: must be at least {minimum}, got {result}")
    return result


def to_date(value: Any, name: str) -> _dt.date:
    """Coerce a datetime.date or ISO 8601 'YYYY-MM-DD' string to datetime.date."""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not _ISO_DATE_RE.match(text):
            raise CalculationInputError(f"{name}: '{value}' is not an ISO 8601 date (YYYY-MM-DD)")
        try:
            return _dt.date.fromisoformat(text)
        except ValueError as exc:
            raise CalculationInputError(f"{name}: '{value}' is not a valid calendar date") from exc
    raise TypeError(
        f"{name}: must be a datetime.date or an ISO 8601 date string, not {type(value).__name__}"
    )


def to_text(value: Any, name: str) -> str:
    """Require a non-empty string (used for recorded-but-unverified inputs such as a source)."""
    if not isinstance(value, str) or not value.strip():
        raise CalculationInputError(f"{name}: must be a non-empty string")
    return value.strip()


# --------------------------------------------------------------------------- #
# Formatting and rounding (the ONLY place rounding happens)
# --------------------------------------------------------------------------- #
def fmt(value: Decimal) -> str:
    """Full-precision, exponent-free decimal string.

    Negative zero is reported without a sign, and a zero whose exponent is below
    -4 (an arithmetic artefact such as 0E-27) collapses to "0"; a caller-supplied
    "0.00" is echoed as "0.00".
    """
    if value == 0:
        if value.as_tuple().exponent < -4:
            return "0"
        value = abs(value)
    return format(value, "f")


def money(value: Decimal) -> str:
    """Final-step rounding for money: 2 places, ROUND_HALF_UP. Returns a decimal string."""
    quantized = value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    if quantized == 0:
        quantized = abs(quantized)  # never emit '-0.00'
    return format(quantized, "f")


def ratio(value: Decimal) -> str:
    """Final-step rounding for percentages/ratios: 4 places, ROUND_HALF_UP."""
    quantized = value.quantize(RATIO_QUANTUM, rounding=ROUND_HALF_UP)
    if quantized == 0:
        quantized = abs(quantized)
    return format(quantized, "f")


# --------------------------------------------------------------------------- #
# Trail construction
# --------------------------------------------------------------------------- #
def _normalize(value: Any, *, keep_ints: bool = False) -> Any:
    """Make a value JSON-native. Decimal -> exponent-free string, date -> ISO 8601.

    ints become strings for ``inputs``/``intermediate_values`` (everything there
    is a string or null); ``output`` keeps ints for day counts (keep_ints=True).
    Floats are never accepted.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return fmt(value)
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, int):
        return value if keep_ints else str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return {str(k): _normalize(v, keep_ints=keep_ints) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v, keep_ints=keep_ints) for v in value]
    raise TypeError(f"cannot record a value of type {type(value).__name__} in a calculation trail")


def build_trail(
    *,
    method: str,
    method_version: str,
    inputs: Mapping[str, Any],
    formula: str,
    intermediate_values: Mapping[str, Any],
    output: Any,
    warnings: list[str],
) -> dict[str, Any]:
    """Build the plain-dict calculation_trail record shared by every method.

    ``inputs`` and ``intermediate_values`` are normalized to strings (Decimal ->
    exponent-free decimal string, dates -> ISO 8601, ints -> str, None kept).
    ``output`` is normalized the same way except that ints (day counts) are kept
    as ints.
    """
    return {
        "method": method,
        "method_version": method_version,
        "inputs": _normalize(dict(inputs)),
        "formula": formula,
        "intermediate_values": _normalize(dict(intermediate_values)),
        "output": _normalize(output, keep_ints=True),
        "warnings": list(warnings),
        "rounding_policy": ROUNDING_POLICY,
    }
