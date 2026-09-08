# LN-EXAMPLE-0001 — harness self-test, NOT a human-prepared answer key

`expected_audit.json` here is a schema-valid `audit_result` built only so
`scripts/eval/run_eval.py` and `tests/unit/test_eval_*.py` can exercise the
harness end to end. Its rule ids (`SUB-EXAMPLE-001` … `SUB-EXAMPLE-005`) are
placeholders that do not exist in `config/checklist_catalog.yaml`, and every
explanation says "EXAMPLE — harness self-test, not a real checklist rule".

`expected_documents.json` maps placeholder filenames to document types so the
classification-accuracy metric has something to score in the self-test.

Nothing in this directory was prepared by a licensed LO or processor and it
must never be cited as evidence that the system is correct. The matching
fixture's `MANIFEST.yaml` sets `harness_self_test: true`, so the harness skips
it during real evaluations unless `--include-self-test` is given.
