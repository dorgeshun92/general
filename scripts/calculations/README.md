# scripts/calculations

Deterministic, Decimal-only calculations for MVP 1 (read-only auditor). These
functions produce **facts**; deciding what to do with them (which payment to
use, whether a variance is acceptable, which funds count) is guideline
judgment that stays with Claude reasoning plus a licensed human reviewer.

## Usage

```python
from scripts.calculations import salaried_monthly_base
trail = salaried_monthly_base(period_gross="2307.69", pay_frequency="BIWEEKLY")
trail["output"]["monthly_base"]   # '5000.00'
```

```
python -m scripts.calculations hourly_monthly_base \
  --json '{"hourly_rate": "25.00", "verified_hours_per_week": "40", "verification_source": "VOE doc-0007 p.1"}'
```

Exit codes: `0` success, `1` validation error, `2` usage error, `3` unsupported calculation.

Every function returns a plain dict that validates against
`schemas/common.defs.schema.json#/$defs/calculation_trail`:

| key | content |
|---|---|
| `method`, `method_version` | method name and semver |
| `inputs` | the normalized inputs as strings (Decimal -> exponent-free string, dates -> ISO 8601, ints -> str, `null` kept) |
| `formula` | human-readable formula |
| `intermediate_values` | every intermediate at full precision, unrounded |
| `output` | structured dict of rounded decimal strings, bools, ints |
| `warnings` | list of strings; never a decision, always a prompt for review |
| `rounding_policy` | the policy string below |

## Input rules

| type | accepted | rejected |
|---|---|---|
| money, rates, percentages, hours | `Decimal`, decimal string `"1234.56"` | `float`, bare `int`, `bool`, NaN, Infinity (`TypeError` / `CalculationInputError`) |
| day counts (`max_age_days`) | `int`, digit string | `bool`, `float`, `Decimal` |
| dates | `datetime.date`, `"YYYY-MM-DD"` | any other format, impossible calendar dates |

Money inputs are non-negative unless the field is documented as a balance
(statement balances may be negative for overdrafts).

## Rounding policy

`ROUNDING_POLICY` (in `_core.py`, carried in every result):

- Decimal arithmetic under a 28-significant-digit `ROUND_HALF_UP` context. No floats anywhere.
- Intermediate values are kept and reported unrounded.
- Money is quantized to `0.01` with `ROUND_HALF_UP` **once, at the final output step**.
- Percentages and ratios are quantized to `0.0001` with `ROUND_HALF_UP` at the final output step.
- Tolerance and threshold comparisons use the **unrounded** values.
- Negative zero is never emitted.

## Methods

| method | version | formula | notes |
|---|---|---|---|
| `salaried_monthly_base` | 1.0.0 | `annual_salary / 12` or `period_gross * periods_per_year / 12` | frequencies WEEKLY=52, BIWEEKLY=26, SEMIMONTHLY=24, MONTHLY=12, ANNUAL=1; exactly one input path; zero salary warns |
| `hourly_monthly_base` | 1.0.0 | `hourly_rate * verified_hours_per_week * 52 / 12` | `verification_source` is required and recorded, not verified; hours must be in (0, 168]; hours > 40 warns (overtime excluded) |
| `ytd_pace_comparison` | 1.0.0 | `days = (as_of - start).days + 1`; `months = days / (365.25/12)`; `avg = ytd / months`; `variance = avg - expected`; `pct = variance / expected * 100` | inclusive day count (see decisions); warning when `abs(pct) > threshold` (default 10) |
| `statement_balance_reconciliation` | 1.0.0 | `computed = beginning + deposits - withdrawals`; `difference = ending - computed`; `reconciles = abs(difference) <= tolerance` | tolerance default `0.00` |
| `funds_to_close` | 1.0.0 | `cash_to_close = price - loan + closing_costs + prepaids - concessions - EMD - other_credits`; `surplus = verified_funds - (cash_to_close + reserves)` | all inputs non-negative, price > 0; warnings for loan > price, negative cash to close, concessions > costs+prepaids, shortfall |
| `liability_payment_comparison` | 1.0.0 | pairwise differences; `matches_within_tolerance = max(abs(diff)) <= tolerance` | tolerance default `1.00`; reports highest source (ties listed); never selects a payment - warns for human review whenever sources differ |
| `date_expiration_check` | 1.0.0 | `age = as_of - document_date`; `expiration = expiration_date or document_date + max_age_days`; `days_remaining = expiration - as_of`; `expired = days_remaining < 0` | valid ON the expiration date; explicit `expiration_date` overrides `max_age_days` |

## Explicitly unsupported

`scripts.calculations.unsupported(kind)` raises `UnsupportedCalculation` for
`COMMISSION`, `BONUS`, `OVERTIME`, `RENTAL`, `SELF_EMPLOYED`,
`ASSET_DEPLETION`, `GUIDELINE_DEPENDENT`. These require averaging-period,
trending, add-back, occupancy, and term choices that are guideline
interpretations. They will not be implemented until a licensed reviewer
supplies written calculation specifications. Until then: classify the
documents and route the item to REVIEW.

## Decisions a licensed reviewer should confirm

1. **Months elapsed convention** (`ytd_pace_comparison`): inclusive day count
   divided by 30.4375 (365.25 / 12). Jan 1 - Jan 31 = 31 days = 1.0185 months,
   not 1.0. An alternative is whole-months-completed counting.
2. **Expiration boundary** (`date_expiration_check`): a document is valid on
   its expiration date / at exactly `max_age_days` and expired the day after.
3. **Tolerance boundaries** are inclusive (`<=`) and use unrounded values.
4. **Liability warning trigger**: any difference, even within tolerance,
   raises the human-review warning; `matches_within_tolerance` is reported
   separately.
5. **Hourly overtime guard**: hours > 40 are computed but warned, not rejected.
