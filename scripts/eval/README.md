# scripts/eval — evaluation harness

Scores produced audits against human answer keys in `tests/expected/` and
aggregates the Section 10 targets from `docs/build-playbook.md`. It never
edits `tests/expected/`; proposed answer-key changes go to
`output/eval/answer_key_proposals/<loan_id>.md` for licensed review.

```bash
python scripts/eval/run_eval.py --fixtures tests/fixtures/deidentified \
    --expected tests/expected --results output/audits \
    [--run] [--report output/eval/<timestamp>/] [--exact-evidence] \
    [--propose-answer-key-change <loan_id> <rule_id> "<reason>"] \
    [--proposals-dir output/eval/answer_key_proposals] [--include-self-test]
```

- `--run` invokes `claude -p "/mortgage-file-audit <dir>" --output-format json`
  for each fixture when the `claude` binary is on PATH; otherwise the harness
  reports "claude CLI not available; compare-only mode" and keeps going.
  Claude stdout/stderr land in `<report>/claude_runs/`.
- Without `--run` it scores whatever the latest run directory under
  `<results>/<loan_id>/` already holds (latest by `run_manifest.json`
  timestamp — `completed_at`, `finished_at`, `generated_at`, `started_at`,
  `timestamp`, `created_at` — else directory mtime).
- A fixture is evaluated only when `<expected>/<loan_id>/expected_audit.json`
  exists. Fixtures whose `MANIFEST.yaml` sets `harness_self_test: true`
  (e.g. `LN-EXAMPLE-0001`) are skipped unless `--include-self-test`.
- The produced file compared is chosen by the answer key's `audit_type`:
  `SUBMISSION_READINESS` -> `submission_readiness.json`,
  `PREAPPROVAL` -> `preapproval_audit.json`.

## Outputs

`eval_report.md` and `eval_report.json` in the report directory (default
`output/eval/<timestamp>/`). The JSON carries every per-rule comparison; the
markdown carries the targets table, aggregates, and a per-fixture section with
the outcome matrix.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | every Section 10 target met (or not evaluable because no fixture exercised it) |
| 1 | at least one target missed, a produced file failed schema validation (fail closed), or an answer key is not a valid `audit_result` |
| 2 | harness error: missing fixtures/expected directory, unreadable answer key, report/proposals dir inside `--expected` |

Schema failures and the `claude` CLI being unavailable never abort the run;
they are reported.

## Metrics (see `compare.py` for the pure implementation)

| Metric | Definition | Target |
|---|---|---|
| Outcome matrix | expected result x actual result counts per fixture (PASS/FAIL/MISSING/REVIEW/NOT_APPLICABLE, plus ABSENT) | reported |
| False PASS on blocking rules (headline) | expected != PASS, actual == PASS, rule blocking on either side | 0 |
| Missed blocking issues | expected FAIL/MISSING on a blocking rule; actual PASS, NOT_APPLICABLE, or absent | reported |
| Unsupported claims | actual PASS or NOT_APPLICABLE with empty `evidence_ids` (already schema-invalid; checked anyway) | reported |
| Evidence citation accuracy | over rules where the key lists evidence and the rule was produced: loose = at least one shared id, `--exact-evidence` = identical sets | >= 99% |
| Calculation agreement | expected `finding.calculation.output` equals actual, compared as `Decimal` (recursively for dict/list outputs; missing trail = mismatch) | 100% |
| Checklist coverage | expected rule_ids present in the produced audit / expected rule_ids | 100% |
| Extra findings | rule_ids produced but not in the answer key | reported |
| Overall status agreement | `overall_status` equal | reported |
| Document classification accuracy | `expected_documents.json` (filename -> document_type) vs `document_inventory.json` | >= 98% |
| Schema failures | every `*.json` in the run dir validated against its schema (`document_inventory`, `loan_file`, `audit_result`) plus `validate_schema.integrity_errors` | 0 (fail closed) |
| PII leakage | `contains_unmasked_pii` over every `.json/.md/.txt/.yaml/.yml/.log/.csv` file in the run dir; SHA-256 digests are blanked first; `MANIFEST.yaml: pii_pattern_allowlist:` entries (regex/literal to blank, or a detector name to skip) are excluded | 0 |
| Correct stop/escalation | for answer keys of the form `{"expected_stop_condition": "<REASON>"}`: no `submission_readiness.json`/`preapproval_audit.json` in the run dir AND the reason appears in `run_manifest.json` (`stop_condition`, `stop_reason`, `status`, nested `stop.reason`, or any string value) | 100% |
| Runtime / tokens | `run_manifest.json` `metrics.runtime_seconds` / `metrics.tokens`, else "not reported" | reported |

Percentages are Decimal strings with two places; a metric with no
observations is NOT_EVALUATED and does not fail the run.

## Pilot workflow (playbook section 11)

1. Have an experienced LO or processor prepare `tests/expected/<loan_id>/expected_audit.json`
   without seeing the system's output. Add `expected_documents.json` when
   classification should be scored.
2. Run `/mortgage-file-audit tests/fixtures/deidentified/<loan_id>` (or
   `run_eval.py --run`).
3. Run the harness and read the per-fixture sections. Score every checklist
   item, not only the readiness label: the outcome matrix and `rules` map in
   `eval_report.json` list each rule's expected vs produced result.
4. Classify every disagreement as one of: **extraction** (wrong value read
   from the page), **normalization** (catalog item misstates or mis-scopes
   the source wording), **calculation** (numeric mismatch with a correct
   input), **rule selection** (wrong applicability / NOT_APPLICABLE), **reasoning**
   (right facts, wrong conclusion or evidence), or **report generation**
   (JSON/markdown does not reflect the findings, schema or PII failures).
5. Fix the narrowest underlying cause (the script, catalog item, or skill
   step that produced the error — not the symptom in the report) and add a
   regression test under `tests/unit/`.
6. If the produced result looks right and the answer key looks wrong, do not
   edit the key. Run
   `run_eval.py ... --propose-answer-key-change <loan_id> <rule_id> "<reason>"`
   and hand `output/eval/answer_key_proposals/<loan_id>.md` to a licensed
   reviewer.
7. Repeat until the false-PASS count on blocking items is zero across the
   whole set, then expand the fixture set (25-50 files) before any production
   pilot, which stays read-only with human side-by-side review.

## Files

- `compare.py` — pure comparison of one produced `audit_result` with one expected one.
- `run_eval.py` — CLI: discovery, schema validation, PII scan, stop-condition check, aggregation, reports.
- Tests: `tests/unit/test_eval_compare.py`, `tests/unit/test_eval_run_eval.py`.
- Self-test pair: `tests/fixtures/deidentified/LN-EXAMPLE-0001/` + `tests/expected/LN-EXAMPLE-0001/` (not a real answer key).
