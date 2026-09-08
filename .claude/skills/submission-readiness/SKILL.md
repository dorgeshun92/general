---
name: submission-readiness
description: Compute the final read-only submission gate — READY | NOT_READY | HUMAN_REVIEW — for one loan by reconciling output/audits/<loan_id>/<run_id>/loan_file.json, preapproval_audit.json, the SUBMISSION-phase items of config/checklist_catalog.yaml, and an optional read-only LOS export; writes submission_readiness.json with proposed LOS corrections, client needs, and Arive notes as DRAFT proposed_action objects. Use when the user asks "is this file ready to submit", for a readiness/submission gate, checklist coverage percentage, or proposed LOS corrections. Do NOT use for intake, preapproval findings, borrower emails, or the full run (/mortgage-file-audit); do NOT use to update Arive/LendingPad, run AUS, trigger TRID, price, pick a lender, contact anyone, or submit — none of that is possible here.
argument-hint: <loan_id> <run_id>   (reads output/audits/<loan_id>/<run_id>/loan_file.json and preapproval_audit.json)
allowed-tools: Read, Glob, Grep, Bash(python scripts/audit/catalog.py*), Bash(python scripts/audit/readiness_gate.py*), Bash(python scripts/audit/render_report.py*), Bash(python scripts/audit/run_manifest.py*), Bash(python scripts/validate_schema.py*), Bash(python -m scripts.calculations *), Write(output/audits/**), Edit(output/audits/**)
---

# submission-readiness

The last gate of the read-only MVP. It combines the preapproval audit, contract/property
facts, and the SUBMISSION-phase checklist into one `audit_result` whose `overall_status` is
computed by `scripts/audit/readiness_gate.py` — never by you. It proposes corrections; it
executes nothing.

Governing rules: `CLAUDE.md` rules 1–8 and 10; "Result language" (READY/NOT_READY/HUMAN_REVIEW
only). Schemas: `schemas/audit_result.schema.json` (`$defs/proposed_action`,
`los_export_present`), `schemas/checklist_catalog.schema.json`.

## Inputs

`$ARGUMENTS` = `<loan_id> <run_id>`; `R = output/audits/<loan_id>/<run_id>/`.
Required: `R/loan_file.json`, `R/preapproval_audit.json`, `config/checklist_catalog.yaml`.
Optional: LOS export — present only if `loan_file.los_export` is non-null or a document of
type `LOS_EXPORT` exists in `loan_file.documents[]`. It is read-only data, never a connection.

## Gate rules (implemented in `scripts/audit/readiness_gate.py`; quoted here so you can explain them)

- **READY** only if: every applicable blocking finding is PASS or NOT_APPLICABLE; every PASS and
  NOT_APPLICABLE cites evidence; no blocking finding is REVIEW; `los_export_present` is true;
  every SUBMISSION catalog item has exactly one finding (coverage 100.00%); the catalog has been
  reviewed by a licensed reviewer.
- **NOT_READY** if any blocking finding is FAIL or MISSING, or there is no LOS export.
- **HUMAN_REVIEW** otherwise when: a blocking finding is REVIEW, coverage < 100%, a catalog item
  has duplicate findings, a finding cites an off-catalog rule, the catalog is unreviewed, or a
  PASS lacks evidence.
- `scripts/validate_schema.py` independently rejects READY with open blocking items,
  unevidenced PASS, or `los_export_present: false`. You cannot override either.

## Procedure

1. **Validate inputs.**
   ```
   python scripts/validate_schema.py R/loan_file.json --schema loan_file
   python scripts/validate_schema.py R/preapproval_audit.json --schema audit_result
   ```
   Any non-zero → STOP "schema validation failed" / "required input missing".

2. **Load the SUBMISSION catalog.** `python scripts/audit/catalog.py --phase SUBMISSION`.
   Exit 2 → STOP. `0 item(s)` → STOP with **"catalog empty — run docs/checklist-normalization.md"**
   (write the manifest with `--stop-condition`, report, do not invent rules). Record the
   reviewed/unreviewed label for the report.

3. **Determine `los_export_present`.** True only when an LOS export document is in the file
   (see Inputs). When false: every LOS-category item is MISSING or REVIEW, and you must not
   state or imply that Arive/LendingPad data is complete, entered, or matches. Write the
   sentence "No LOS export was provided; no claim is made about LOS completeness" in the report.

4. **Carry forward the preapproval findings.** For each preapproval finding that is FAIL,
   MISSING, or REVIEW and blocking, the corresponding SUBMISSION item (if the catalog maps one)
   cannot be PASS. Do not re-decide preapproval items here; reference them by `rule_id` in
   `explanation`.

5. **Produce exactly one finding per SUBMISSION catalog item** into `R/submission_findings.json`,
   using the same result rules as `preapproval-audit` step 3 (PASS needs cited evidence with
   page; absence is MISSING; guideline_lookup without an approved source is REVIEW; no PASS
   because a document merely exists). Contract items compare `loan_file.contract` facts
   (price, earnest money, dates, signatures, addenda, parties) with the 1003 and LOS export;
   date items use `python -m scripts.calculations date_expiration_check --json '{...}'` and
   funds items use `funds_to_close`; embed each trail in `finding.calculation`.

6. **Proposed actions (DRAFT only).** In `R/submission_partial.json` add:
   - `proposed_los_corrections`: `action_type: LOS_FIELD_CORRECTION`, `target` = LOS field,
     `before_value` = LOS export value, `after_value` = evidenced document value, `evidence_ids`,
     `approver_role: PROCESSOR`. Only when an LOS export exists.
   - `proposed_client_needs`: `action_type: CLIENT_NEED`, one per MISSING/FAIL item,
     `approver_role: LOAN_OFFICER`.
   - `proposed_los_notes`: `action_type: LOS_NOTE` (Arive note text), `approver_role: PROCESSOR`.
   Every object: `action_id` `PA-001`…, `rule_ids`, `status: "DRAFT_HUMAN_APPROVAL_REQUIRED"`.
   Also fill `missing_documents`, `conflicts` (CF-001…, every 1003/credit/document/contract/
   AUS/LOS disagreement), `approvals_required` (guideline questions → UNDERWRITER, TRID-related
   → COMPLIANCE, see `config/roles.yaml`), `audit_type: "SUBMISSION_READINESS"`,
   `generated_by.skill: "submission-readiness"`, `los_export_present`.

7. **Finalize, validate, render, manifest.**
   ```
   python scripts/audit/readiness_gate.py finalize --partial R/submission_partial.json \
     --findings R/submission_findings.json --phase SUBMISSION \
     --input R/loan_file.json --input R/preapproval_audit.json --input config/approved_sources.yaml \
     --out R/submission_readiness.json
   python scripts/validate_schema.py R/submission_readiness.json --schema audit_result
   python scripts/audit/render_report.py R/submission_readiness.json --out R/submission_report.md --loan-file R/loan_file.json
   python scripts/audit/run_manifest.py write --run-dir R --skill submission-readiness --run-id <run_id> --loan-id <loan_id> \
     --input R/loan_file.json --input R/preapproval_audit.json --input config/checklist_catalog.yaml \
     --output R/submission_findings.json --output R/submission_partial.json --output R/submission_readiness.json --output R/submission_report.md
   ```
   The `gate:` lines printed by `finalize` are the reasons for the status; copy them into the
   report. Exit 1 → fix findings and rerun; never edit `overall_status` by hand.

## Hard prohibitions

This skill must not update Arive/LendingPad, run AUS, trigger TRID, price, select a lender,
contact the borrower/agent/title, or submit the loan. It has no tool that can, and it must
not ask for one. Proposed actions are text for a human.

## Stop conditions

required input missing; schema validation fails; catalog empty/invalid; conflicting identity
information in `loan_file.review_items` (status becomes HUMAN_REVIEW via a REVIEW finding on
the identity item; say so); guideline support unavailable for a blocking item (REVIEW, not a
guess).

## Completion report (exact format)

```
submission-readiness — <loan_id> / <run_id>
Overall status: READY | NOT_READY | HUMAN_REVIEW   (reasons: <gate lines>)
LOS export present: yes | NO — no claim is made about LOS completeness
Blocking findings (FAIL/MISSING/REVIEW first): <finding_id rule_id result — one line each>
Non-blocking findings: <count, notable ones>
Missing documents: <list>      Conflicts: <list>
Proposed LOS corrections: <n> (DRAFT)   Proposed client needs: <n> (DRAFT)   Proposed Arive notes: <n> (DRAFT)
Approvals still required: <description — role>
Checklist coverage: <items_evaluated>/<catalog_items_in_phase> = <coverage_percent>%; catalog <reviewed by … | not yet reviewed by a licensed reviewer>
Outputs: R/submission_readiness.json, R/submission_report.md, R/run_manifest.json
Nothing was written to any LOS, no AUS/TRID/pricing/submission occurred, nobody was contacted.
DECISION SUPPORT ONLY — not a credit or compliance decision.
```
