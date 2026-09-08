---
name: mortgage-file-audit
description: Master read-only orchestrator for the MVP 1 pilot — runs loan-file-intake, preapproval-audit, and submission-readiness in sequence on ONE de-identified fixture directory, validates every output, and returns the executive summary and human review queue. Runs ONLY when invoked explicitly as /mortgage-file-audit <directory>; it is never selected automatically. Do NOT use for a single stage (use the component skill), for borrower drafts, or for any directory outside tests/fixtures/deidentified/.
argument-hint: tests/fixtures/deidentified/<loan-id> [--run-id <id>]
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Skill(loan-file-intake), Skill(preapproval-audit), Skill(submission-readiness), Bash(python scripts/intake/*), Bash(python scripts/audit/*), Bash(python scripts/validate_schema.py*), Bash(python -m scripts.calculations *), Bash(sha256sum *), Bash(ls *), Write(output/audits/**), Edit(output/audits/**)
---

# mortgage-file-audit (read-only orchestrator)

Entry point for the pilot (`docs/build-playbook.md` sections 7 and 11). It runs the three
component skills in order, refuses to continue past any stop condition, and never touches a
write-capable integration. Every rule in `CLAUDE.md` applies; each component skill's own
SKILL.md governs its stage.

`$ARGUMENTS` = `<loan-dir> [--run-id <id>]`. `loan_id` = directory basename; `run_id` =
`RUN-<YYYYMMDD>-<HHMMSS>` (UTC) unless given. `R = output/audits/<loan_id>/<run_id>/`.

## Sequence

1. **Verify the directory.** `ls <loan-dir>` exists; its path is under an
   `approved_input_roots` entry in `config/approved_data_locations.yaml`; `MANIFEST.yaml`
   exists with `deidentified: true`, `deidentified_by`, `deidentified_at`. Any failure → STOP
   (no outputs). Any sign of live PII → STOP "source directory appears to contain live
   borrower PII".
2. **Run intake.** Invoke `loan-file-intake <loan-dir> --run-id <run_id>` (its procedure,
   including `scripts/intake/inventory.py`). Its STOPPED status is your STOP.
3. **Validate loan_file.**
   `python scripts/validate_schema.py R/loan_file.json --schema loan_file` and
   `python scripts/validate_schema.py R/document_inventory.json --schema document_inventory`.
   Non-zero → STOP "schema validation fails".
4. **Run preapproval-audit** `<loan_id> <run_id>`. If it reports "catalog empty — run
   docs/checklist-normalization.md" → STOP "guideline support is unavailable (catalog empty)";
   do not invent rules; go to step 8 with what exists.
5. **Run submission-readiness** `<loan_id> <run_id>`.
6. **Validate all outputs.**
   ```
   python scripts/validate_schema.py R/preapproval_audit.json --schema audit_result
   python scripts/validate_schema.py R/submission_readiness.json --schema audit_result
   python scripts/audit/run_manifest.py verify R
   ```
   Any failure → STOP "schema validation fails" (report which file and errors).
7. **Executive summary + human review queue** in the output format below.
8. **Final run manifest** (always, even after a stop):
   ```
   python scripts/audit/run_manifest.py write --run-dir R --skill mortgage-file-audit \
     --run-id <run_id> --loan-id <loan_id> [--stop-condition "<reason>"] \
     --input <loan-dir>/MANIFEST.yaml --input <every source file> \
     --input config/checklist_catalog.yaml --input config/approved_sources.yaml \
     --output R/document_inventory.json --output R/loan_file.json \
     [--output R/classification_review.json] [--output R/preapproval_audit.json --output R/preapproval_report.md] \
     [--output R/submission_readiness.json --output R/submission_report.md]
   python scripts/audit/run_manifest.py verify R
   ```
   List only files that exist. The manifest holds the exact output paths and sha256 checksums;
   quote them in the report.

## Stop conditions (verbatim from the playbook)

- source directory appears to contain live borrower PII during development
- schema validation fails
- document is unreadable or encrypted
- a required input is missing
- conflicting identity information exists
- guideline support is unavailable

Never continue past a stop condition by guessing. Never invoke a write-capable integration.
On a stop: record it in the manifest (`--stop-condition`), report which step stopped and why,
list the outputs that do exist, and end. Do not retry with altered inputs, skip a stage, or
hand-edit an output so a validator passes. A stop is a correct result, not a failure to fix.

## What this skill never does

Send email/SMS, write to Arive/LendingPad, run AUS, trigger TRID, price, select a lender,
submit, create tasks, or call any MCP/connector tool. It reads fixtures and writes only under
`output/audits/`. If the user asks for any of those, say it is outside the read-only MVP.

## Daily pilot-use output format (playbook section 11 — use these headings, in this order)

```
/mortgage-file-audit — <loan_id> / <run_id>   (DECISION SUPPORT ONLY — not a credit or compliance decision)
Run status: COMPLETE | STOPPED at step <n>: <stop condition>

1. Overall readiness status: READY | NOT_READY | HUMAN_REVIEW | not computed (stopped before submission-readiness)
   Reasons: <gate lines from readiness_gate>   LOS export present: yes | NO — no claim about LOS completeness
2. Blocking findings first: <finding_id rule_id RESULT — explanation — evidence DOC-xxx <file> p.<n> — proposed action>
   (FAIL, then MISSING, then REVIEW; then non-blocking summary count)
3. Missing documents: <document_type — borrower — description — rule ids>
4. Conflicting facts: <CF-xxx field: source=value (EV-xxx) vs source=value (EV-xxx)>
5. Calculation summary: <finding — method vX — inputs — output — warnings>
6. Checklist coverage: PREAPPROVAL <evaluated>/<items> = <pct>%; SUBMISSION <evaluated>/<items> = <pct>%; catalog v<version> <reviewed by … | not yet reviewed by a licensed reviewer>
7. Items requiring licensed review (human review queue): <finding_id — rule_id — reviewer_role — review_reason>, plus classification proposals and unevidenced values from intake, plus approvals_required
8. Exact output files created (path — sha256 from run_manifest.json):
   R/document_inventory.json — <sha>
   R/loan_file.json — <sha>
   R/classification_review.json — <sha>
   R/preapproval_audit.json — <sha>      R/preapproval_report.md — <sha>
   R/submission_readiness.json — <sha>   R/submission_report.md — <sha>
   R/run_manifest.json

Known limitations: <copied from submission_readiness.json or preapproval_audit.json known_limitations>
For every conclusion above the source file and page are cited; where evidence is unavailable the result is MISSING or REVIEW.
```

## Notes for the current repository state

`config/checklist_catalog.yaml` is `items: []` and `config/approved_sources.yaml` is
`sources: []`. Until the normalization procedure is run and a licensed reviewer signs off,
every run of this skill will stop at step 4 with "guideline support is unavailable (catalog
empty)" after intake completes. That is the intended behavior; report it plainly.

## After a completed run: publishing to the dashboard (manual, separate step)

This skill never pushes anything anywhere. When a run has completed and its outputs
validate, a person may publish the derived, masked outputs to the dashboard datastore:

```
python scripts/sync/push_run.py <loan_id> <run_id> --dry-run   # validates, PII-scans, prints what would go
python scripts/sync/push_run.py <loan_id> <run_id>             # requires .env with Supabase keys
```

If the run was started from a dashboard run request, note the request id in the
completion report so the requester can mark it COMPLETED.

