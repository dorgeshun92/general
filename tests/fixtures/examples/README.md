# Schema example fixtures

These files are **schema examples, not answer keys**. They exist so the unit
tests can prove that the JSON Schemas in `schemas/` and the validator in
`scripts/validate_schema.py` accept well-formed documents and reject the
specific defects the repository rules forbid. Nothing here describes a real
borrower, a real loan, or a real checklist rule.

- Loan `LN-EXAMPLE-0001`, borrowers "Test Borrower Alpha" / "Test Borrower
  Beta", "Example Bank", "Example Employer LLC" and every other name are
  synthetic. Account numbers appear only masked (`****1234`). Money is a
  decimal string, dates are ISO 8601, unknown values are `null` with
  `status: UNKNOWN` (see `loan.lender` in `loan_file.valid.json`).
- `checklist_catalog.*.yaml` items are placeholders (`EXAMPLE ITEM n —
  placeholder statement for schema testing, not a checklist rule`) from a
  source titled `EXAMPLE SOURCE (not a real checklist)`. They must never be
  copied into `config/checklist_catalog.yaml`; the repository rule is never to
  invent mortgage guidelines.
- Audit `rule_id`s such as `PRE-EXAMPLE-001` only exercise the id pattern.
- Every `*.valid.*` file must validate with zero errors through both
  `scripts.common.schema_registry.validate_document` and
  `scripts.validate_schema.validate_path` (schema + referential integrity).
- Every `*.invalid.*` file is a copy of its valid sibling with **exactly one
  defect**. The schemas set `additionalProperties: false` at the top level, so
  the defect cannot be annotated inside the file; it is recorded in the
  filename and in the table below, and asserted in
  `tests/unit/test_schema_examples.py`.

The `.json` fixtures are generated from a throwaway script (deep copy of the
valid object plus one mutation); the `.yaml` catalogs are hand-written.

## Invalid files and their single defect

| File | Defect | Caught by |
|---|---|---|
| `loan_file.invalid.float_money.json` | `loan.loan_amount.value` is the JSON number `400000.0` instead of the decimal string `"400000.00"` | JSON Schema (`money_fact`) |
| `loan_file.invalid.fact_without_evidence.json` | `loan.loan_amount` has a non-null value but `evidence_ids` is `[]` | JSON Schema (`fact` else-branch, `minItems: 1`) |
| `loan_file.invalid.dangling_evidence_ref.json` | `loan.loan_amount.evidence_ids` cites `EV-999`, which is not in `evidence[]` | Referential integrity in `validate_schema.py` only; the JSON Schema alone accepts this file |
| `loan_file.invalid.unmasked_account.json` | `assets[0].account_number_masked` is the full number `123456781234` | JSON Schema (`masked_account` pattern) |
| `audit_result.invalid.pass_without_evidence.json` | finding `F-001` is `PASS` with `evidence_ids: []` | JSON Schema (finding conditional) |
| `audit_result.invalid.missing_without_action.json` | finding `F-003` is `MISSING` with `proposed_action: null` | JSON Schema (finding conditional) |
| `audit_result.invalid.review_without_reason.json` | finding `F-004` is `REVIEW` with `review_reason: null` | JSON Schema (finding conditional) |
| `audit_result.invalid.ready_with_open_blocking.json` | `overall_status` is `READY` while blocking finding `F-002` is `MISSING` | Referential integrity |
| `audit_result.invalid.count_mismatch.json` | `summary.counts.PASS` declares 3 but findings contain 2 | Referential integrity |
| `audit_result.invalid.ready_without_los_export.json` | `overall_status` is `READY` with `los_export_present: false` | Referential integrity |
| `document_inventory.invalid.bad_sha256.json` | `documents[0].sha256` is not a 64-character lowercase hex digest | JSON Schema (`sha256` pattern) |
| `checklist_catalog.invalid.duplicate_id.yaml` | the third item reuses id `PRE-EXAMPLE-001` | Referential integrity |
| `checklist_catalog.invalid.always_not_applicable.yaml` | `PRE-EXAMPLE-001` has `applies_when: always` yet lists `NOT_APPLICABLE` in `result_values` | Referential integrity |

## Valid files

| File | What it demonstrates |
|---|---|
| `loan_file.valid.json` | Full canonical loan file: 5 documents, 10 evidence records, a salaried income with a calculation trail, an asset with a large unsourced deposit, two liabilities, a SUBJECT property and one REO, a purchase contract, one review item, `los_export: null`, and several honest-unknown facts |
| `audit_result.valid.preapproval.json` | `PREAPPROVAL` with `overall_status: null`; one finding per result value with the correct conditional fields; matching summary counts; a conflict, a missing document, a draft client need, and an approval request |
| `audit_result.valid.submission_ready.json` | `SUBMISSION_READINESS` / `READY`; every blocking finding is `PASS` with evidence or `NOT_APPLICABLE` with evidence; `los_export_present: true` |
| `audit_result.valid.submission_not_ready.json` | `SUBMISSION_READINESS` / `NOT_READY`; an open blocking `MISSING` finding and a `missing_documents` entry |
| `document_inventory.valid.json` | Seven files including an identical-hash duplicate and an encrypted, unreadable upload |
| `checklist_catalog.valid.yaml` | Placeholder catalog with one source, three items (one `ambiguous`), one unresolved conflict |
