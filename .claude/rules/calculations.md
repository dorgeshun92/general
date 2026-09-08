---
paths:
  - "scripts/calculations/**"
  - "tests/unit/test_calc*"
  - "tests/unit/test_calculations*"
---
# Calculations (supplements CLAUDE.md rule 4 and "Supported calculations (v1)")

## Decimal only
- Money and rates are `decimal.Decimal` built from strings, never `float`.
  JSON in/out carries decimal strings (`"1234.56"`), matching
  `common.defs.schema.json#/$defs/decimal_string`.
- Round once, at the end, with an explicit `ROUND_HALF_UP` and the documented
  precision (cents for money, 4 places for ratios). Never round intermediates.
- Dates are `datetime.date` from ISO 8601; month arithmetic is explicit per spec.

## Trail required
- Every calculation returns the value AND a trail: inputs (with evidence IDs),
  formula, each intermediate step, rounding applied, and the spec/guideline
  reference with its effective date. No trail → the result is REVIEW.
- A calculation with a missing or unverified input returns MISSING/REVIEW,
  never a guessed number. Unknown values stay `null`.
- Same inputs must yield byte-identical output (deterministic, no clock reads
  inside the calculation; the run supplies `as_of`).

## Supported in v1
Salaried monthly base; hourly base with verified hours and frequency;
YTD pace comparison; statement balance reconciliation; funds-to-close
arithmetic; liability payment comparison; date expiration checks.

## NOT implemented — classify and route to REVIEW, do not compute
Commission, bonus, overtime, rental, self-employed (Schedule C/K-1/1120S),
asset depletion, declining/variable income trending, and any
guideline-dependent income. Do not add one without a written spec from a
licensed reviewer and a matching answer key under `tests/expected/`.

## Tests
- One test per formula with hand-computed expected values from the spec, plus
  a rounding edge case and a missing-input case that asserts REVIEW.
