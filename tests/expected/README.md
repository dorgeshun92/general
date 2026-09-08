# tests/expected/

Human-prepared answer keys, one directory per fixture loan id, matching
`tests/fixtures/deidentified/<loan-id>/`.

Each answer key holds `expected_audit.json` (validated against
`schemas/audit_result.schema.json`) plus optional `expected_calculations.json`.

Answer keys are prepared by an experienced LO or processor WITHOUT seeing
the system's output. The evaluation harness never modifies them; proposed
changes go to `output/eval/answer_key_proposals/` for licensed review.
