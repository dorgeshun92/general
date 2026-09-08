# Decisions needed before Phase 2

Answers here unblock the drafting assistant (MVP 2) and every integration.
Record the answer, who decided, and the date. Leave "OPEN" until decided.

| # | Question | Answer | Decided by | Date |
|---|----------|--------|------------|------|
| 1 | Primary LOS: Arive, LendingPad, or both? | OPEN | | |
| 2 | Can the LOS export a complete file as JSON, XML, MISMO, CSV, or PDF? | OPEN | | |
| 3 | Which loan products belong in the first release: conventional only, or FHA/VA/USDA/Non-QM too? | OPEN | | |
| 4 | Which internal or investor guidelines are approved sources? (register in `config/approved_sources.yaml`) | OPEN | | |
| 5 | Who signs off on income calculations and policy interpretations? (fill `config/roles.yaml`) | OPEN | | |
| 6 | Where may de-identified test files be stored? (default: `tests/fixtures/deidentified/` only) | OPEN | | |
| 7 | What constitutes a blocking versus advisory finding? (sets `blocking_if_failed` in the catalog) | OPEN | | |
| 8 | Which actions require LO, processor, compliance, or management approval? | OPEN | | |

## Decisions already made by the playbook (v1.0)

- Build a read-only auditor first. No LOS writes until it passes formal accuracy testing.
- Pricing, lender choice, guideline exceptions, adverse-action implications, AUS
  submission, TRID triggering, and final submission stay approval-gated in every release.
- Self-employed income is not calculated in version 1; it is classified and routed to REVIEW.
- Commission, bonus, overtime, rental, self-employed, asset depletion, and
  guideline-dependent income calculations wait for written specs from a licensed reviewer.
