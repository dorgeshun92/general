# tests/expected/

Human-prepared answer keys, one directory per fixture loan id, matching
`tests/fixtures/deidentified/<loan-id>/`.

Each answer key holds `expected_audit.json` (validated against
`schemas/audit_result.schema.json`) plus optional `expected_calculations.json`.

Answer keys are prepared by an experienced LO or processor WITHOUT seeing
the system's output. The evaluation harness never modifies them; proposed
changes go to `output/eval/answer_key_proposals/` for licensed review.

Optional `expected_documents.json` maps each fixture filename to its expected
`document_type`; the harness scores document-classification accuracy from it.

Stop-condition form: for a designed edge case the answer key is just
`{"expected_stop_condition": "<REASON>"}` (e.g. `ENCRYPTED_DOCUMENT`). The
correct outcome is then the ABSENCE of a completed audit and the presence of
that reason in the run's `run_manifest.json`. See `LN-EDGE-ENCRYPTED/`.

`LN-EXAMPLE-0001/` is a harness self-test, not a human-prepared answer key.
