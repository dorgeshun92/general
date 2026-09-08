# scripts/sync — push derived audit data to the dashboard repository

Two small CLIs move what the skills already wrote under `output/` into the
repository the dashboard, REST API, and MCP server read from
(`services/`, Supabase-backed in production, in-memory for tests).

```bash
# one run
python scripts/sync/push_run.py LN-EDGE-CLEAN RUN-2026-09-08-001
# every <loan_id>/<run_id> under MPIRE_OUTPUT_DIR with a run manifest or an audit file
python scripts/sync/push_run.py --all
# an explicit directory (must still resolve to <MPIRE_OUTPUT_DIR>/<loan_id>/<run_id>)
python scripts/sync/push_run.py --run-dir output/audits/LN-EDGE-CLEAN/RUN-2026-09-08-001
# validate and print counts, reports, and sha256s without writing anything
python scripts/sync/push_run.py --all --dry-run
# evaluation harness summary
python scripts/sync/push_eval.py output/eval/<timestamp>/eval_report.json
```

Configuration comes from the environment or a local `.env` (see `.env.example`):
`MPIRE_REPO_BACKEND`, `MPIRE_OUTPUT_DIR` (default `output/audits`),
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`. `--backend memory|supabase`
overrides the backend for one invocation; `memory` discards everything when the
process exits, so it is only useful together with `--dry-run` or inside tests.

## What is sent

Only derived, masked data that a skill wrote under `output/audits/<loan_id>/<run_id>/`:

| File | Becomes |
|---|---|
| `run_manifest.json` | the `runs` row (skill, timestamps, stop condition, tool versions, manifest paths + hashes) |
| `document_inventory.json` / `loan_file.json` | `documents` (ids, filenames, sha256, type, status, page counts) and `review_items` |
| `preapproval_audit.json`, `submission_readiness.json` | `findings`, `missing_documents`, `conflicts`, `proposed_actions`, `approvals_required`; the submission gate defines `overall_status` |
| `*.md` | `reports` (masked Markdown plus its sha256) |
| `eval_report.json` (push_eval) | one `eval_reports` row: `all_targets_met`, the target table, the full report |

When `tests/fixtures/deidentified/<loan_id>/MANIFEST.yaml` exists, its
`description` becomes the loan description and its `pii_pattern_allowlist`
entries (fake numbers a human vouched for) are ignored by the PII gate.

## What is never sent

- Source documents, extracted text (`extracted_text/`), credit reports, bank
  statements, tax returns, or anything outside the run directory.
- Any file that fails its JSON Schema or integrity checks.
- Any file that still contains an SSN-shaped value or an 8+ digit run. The
  gate runs on every byte that would leave the machine; on a hit the CLI names
  the file and the pattern kind, never the value, and exits 2 with nothing written.
- A directory outside `MPIRE_OUTPUT_DIR` (real paths are compared; symlinks and
  `..` are refused).
- Keys. Settings are printed through `Settings.redacted()` only.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | every requested run was synced (or validated, with `--dry-run`) |
| 1 | nothing to sync: no run directories, or the loan/run/file was not found |
| 2 | schema, integrity, or PII gate failed, or the location was refused — fail closed, nothing written for that run |
| 3 | Supabase, network, or backend configuration error |

With `--all`, valid runs are still pushed when another run fails validation;
the exit code is the worst failure. A backend error stops the loop.

## The service-key rule

`push_run.py` and `push_eval.py` are the only components that write audit
content, and they need `SUPABASE_SERVICE_ROLE_KEY`, which bypasses Row-Level
Security. That key stays server-side: in `.env` on the machine that runs the
skills, never in the dashboard, a browser, a commit, a log, or chat. The API and
dashboard use the anon key plus a user session and can only append decisions
and run requests.

## Re-sync semantics

Pushing the same `loan_id/run_id` again **replaces** that run's audit rows: the
`runs` row is deleted (children — documents, findings, review items, missing
documents, conflicts, proposed actions, approvals, reports — cascade) and
re-inserted from the local files, so nothing stale survives. Dashboard state is
**never touched** by a re-sync: `review_decisions`, `action_decisions`, and
`run_requests` reference runs by id without a foreign key and keep their history.
Supabase is this system's own datastore, not an LOS; syncing to it is not one
of the approval-gated external writes in `CLAUDE.md`, but the same masking and
no-source-documents rules apply.
