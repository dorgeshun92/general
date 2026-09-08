"""Command-line entry point.

    python -m scripts.calculations <method_name> --json '{"key": "value", ...}'

Prints the calculation_trail dict as JSON. Exit codes:
    0  success
    1  validation error (bad method, bad JSON, bad or missing inputs, floats/ints as money)
    2  argparse usage error
    3  UnsupportedCalculation (commission, bonus, overtime, rental, self-employed,
       asset depletion, guideline-dependent)
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from typing import Any

from . import METHODS, CalculationInputError, UnsupportedCalculation


def _reject_float(text: str) -> Any:
    raise CalculationInputError(
        f"JSON number {text} has a fraction or exponent; pass money, rates, percentages and hours "
        "as decimal strings such as \"1234.56\""
    )


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.calculations",
        description="Run one deterministic calculation and print its calculation_trail as JSON.",
    )
    parser.add_argument("method", help=f"one of: {', '.join(METHODS)}")
    parser.add_argument(
        "--json",
        dest="payload",
        required=True,
        help="JSON object of keyword arguments; money/rates/hours must be decimal strings",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        func = METHODS.get(args.method)
        if func is None:
            raise CalculationInputError(
                f"unknown method '{args.method}'; supported methods: {', '.join(METHODS)}"
            )
        try:
            payload = json.loads(args.payload, parse_float=_reject_float)
        except json.JSONDecodeError as exc:
            raise CalculationInputError(f"--json is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise CalculationInputError("--json must be a JSON object of keyword arguments")
        result = func(**payload)
    except UnsupportedCalculation as exc:
        print(f"UNSUPPORTED: {exc}", file=sys.stderr)
        return 3
    except (CalculationInputError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    sys.exit(main())
