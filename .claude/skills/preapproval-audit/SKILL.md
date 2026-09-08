---
name: preapproval-audit
description: Evaluate every PREAPPROVAL-phase item of config/checklist_catalog.yaml against an existing, validated output/audits/<loan_id>/<run_id>/loan_file.json and produce preapproval_audit.json (schemas/audit_result.schema.json) plus a Markdown report — one finding per catalog item, PASS only with cited evidence. Use when the user asks for a preapproval audit, checklist review, "which pre-approval items pass/fail", missing-document analysis, or income/asset/liability/credit/REO/property findings on an already-intaken file. Do NOT use for intake/classification (loan-file-intake), the submission gate or READY/NOT_READY status (submission-readiness), borrower emails (borrower-communication-drafts), or the end-to-end run (/mortgage-file-audit). Do NOT use if loan_file.json does not exist yet.
argument-hint: <loan_id> <run_id>   (reads output/audits/<loan_id>/<run_id>/loan_file.json)
allowed-tools: Read, Glob, Grep, Bash(python scripts/audit/catalog.py*), Bash(python scripts/audit/readiness_gate.py*), Bash(python scripts/audit/render_report.py*), Bash(python scripts/audit/run_manifest.py*), Bash(python scripts/validate_schema.py*), Bash(python -m scripts.calculations *), Write(output/audits/**), Edit(output/audits/**)
---

# preapproval-audit

Apply the normalized preapproval checklist to a canonical loan file. Scripts compute; you map
evidence to checklist language and explain discrepancies. Output is decision support for a
licensed human, never a credit decision.

Governing rules: `CLAUDE.md` rules 1–7 and 10, "Result language", "Source precedence",
"Supported calculations (v1)". Schemas: `schemas/audit_result.schema.json` (finding shape at
`$defs/finding`), `schemas/checklist_catalog.schema.json`, `schemas/loan_file.schema.json`.

Domains covered (by catalog category): CREDIT, LIABILITIES, REO, EMPLOYMENT, INCOME_REGULAR,
INCOME_VARIABLE, INCOME_SELF_EMPLOYED, INCOME_OTHER, ASSETS, GIFT_FUNDS, RETIREMENT_FUNDS,
TITLE, PROPERTY (plus IDENTITY, APPLICATION_1003, OTHER if the catalog has them in phase
PREAPPROVAL).

## Inputs

`$ARGUMENTS` = `<loan_id> <run_id>`. Run dir `R = output/audits/<loan_id>/<run_id>/`.
Required: `R/loan_file.json` (validated), `config/checklist_catalog.yaml`,
`config/approved_sources.yaml`. Optional: `R/document_inventory.json`, `R/extracted_text/`.

## Procedure

1. **Validate the input.** `python scripts/validate_schema.py R/loan_file.json --schema loan_file`.
   Non-zero → STOP "schema validation failed". Missing file → STOP "required input missing:
   run loan-file-intake first".

2. **Load the catalog for this phase.** `python scripts/audit/catalog.py --phase PREAPPROVAL`.
   - Exit 2 → STOP "catalog invalid" (fail closed).
   - `PREAPPROVAL: 0 item(s)` (the catalog is currently `items: []`) → STOP and report exactly:
     **"catalog empty — run docs/checklist-normalization.md"**. Do NOT invent, recall, or
     paraphrase checklist rules from memory. Write the run manifest with
     `--stop-condition "catalog empty"` and finish with the completion report.
   - Note whether the output says "catalog not yet reviewed by a licensed reviewer"; that
     label must appear in your report.

3. **Produce exactly one finding per catalog item in phase PREAPPROVAL.** Read each item
   (`source_text`, `applies_when`, `required_evidence`, `fields_to_compare`,
   `evaluation_method`, `blocking_if_failed`, `proposed_action`, `required_human_role`,
   `ambiguous`). Write the findings array to `R/preapproval_findings.json`. Each finding has
   ALL of: `finding_id` (`F-001`… sequential), `rule_id`, `result`, `evidence_ids`,
   `explanation`, `discrepancy`, `proposed_action`, `blocking` (= `blocking_if_failed`),
   `reviewer_role`, `confidence`, `review_reason`, `calculation` (trail or null),
   `guideline_source` (or null). Result rules:
   - **PASS** only when `required_evidence` is present AND `fields_to_compare` agree AND every
     cited `evidence_ids` entry exists in `loan_file.evidence[]` with a page. A related document
     merely existing is never a PASS ("a W-2 is in the file" ≠ "income is verified").
   - **FAIL** when evidence exists and the comparison fails; `discrepancy` states both values
     and their sources; `proposed_action` is required.
   - **MISSING** when required evidence is absent; `proposed_action` names the document.
     Absence of evidence is never NOT_APPLICABLE.
   - **NOT_APPLICABLE** only when `applies_when` is demonstrably false AND you cite the evidence
     that shows it (e.g. purpose = REFINANCE with evidence id for a purchase-only item).
   - **REVIEW** with `review_reason` and `reviewer_role` (= `required_human_role`, else
     UNDERWRITER) when: `evaluation_method` is `guideline_lookup` and `config/approved_sources.yaml`
     has no source covering it (it is currently empty, so ALL guideline_lookup items are REVIEW);
     `ambiguous: true`; `evaluation_method` is `human_judgment`; the loan_file fact has
     status REVIEW/CONFLICT; or income type is SELF_EMPLOYED, COMMISSION, BONUS, OVERTIME,
     RENTAL, ASSET_DEPLETION (classify the documents, do not calculate).
   - Income with `used_to_qualify` false or null is described as **"potential additional
     income"** in the explanation and never counted toward anything.
   - `guideline_source` is non-null only when citing a source_id that exists in
     `config/approved_sources.yaml`, with its `effective_date`.
   - `confidence` reflects the evidence's `extraction_confidence`; LOW evidence cannot support
     a HIGH-confidence PASS.

4. **Supported calculations only, via the calculation package.** For items whose
   `fields_to_compare` need arithmetic, call one of:
   `salaried_monthly_base`, `hourly_monthly_base`, `ytd_pace_comparison`,
   `statement_balance_reconciliation`, `funds_to_close`, `liability_payment_comparison`,
   `date_expiration_check`:
   ```
   python -m scripts.calculations salaried_monthly_base --json '{"annual_salary": "60000.00"}'
   ```
   Inputs are decimal strings taken from evidenced loan_file facts. Embed the returned trail
   verbatim as `finding.calculation`. `UnsupportedCalculation` → the finding is REVIEW; never
   do the math yourself.

5. **Conflicts, missing documents, approvals.** Write `R/preapproval_partial.json`:
   ```json
   {"loan_id": "...", "run_id": "...", "audit_type": "PREAPPROVAL",
    "generated_by": {"skill": "preapproval-audit"},
    "missing_documents": [{"document_type": "PAYSTUB", "borrower_id": "B-1", "description": "...", "rule_ids": ["PRE-..."]}],
    "conflicts": [{"conflict_id": "CF-001", "field": "...", "values": [{"source": "URLA_1003", "value": "...", "evidence_ids": ["EV-..."]}, {"source": "CREDIT_REPORT", "value": "...", "evidence_ids": ["EV-..."]}], "explanation": "...", "rule_ids": []}],
    "approvals_required": [{"description": "...", "approver_role": "UNDERWRITER", "rule_ids": ["PRE-..."]}]}
   ```
   Flag every disagreement between 1003, credit, documents, contract, AUS, LOS (rule 6).

6. **Finalize and validate (script decides counts/coverage; you do not).**
   ```
   python scripts/audit/readiness_gate.py finalize --partial R/preapproval_partial.json \
     --findings R/preapproval_findings.json --phase PREAPPROVAL \
     --input R/loan_file.json --input config/approved_sources.yaml --out R/preapproval_audit.json
   python scripts/validate_schema.py R/preapproval_audit.json --schema audit_result
   ```
   Exit 1/2 → fix the findings (not the validator), rerun. Coverage must be 100.00%; if a
   rule id is listed as unevaluated, add its finding. `overall_status` is null for PREAPPROVAL.

7. **Render and manifest.**
   ```
   python scripts/audit/render_report.py R/preapproval_audit.json --out R/preapproval_report.md --loan-file R/loan_file.json
   python scripts/audit/run_manifest.py write --run-dir R --skill preapproval-audit --run-id <run_id> --loan-id <loan_id> \
     --input R/loan_file.json --input config/checklist_catalog.yaml --input config/approved_sources.yaml \
     --output R/preapproval_findings.json --output R/preapproval_partial.json --output R/preapproval_audit.json --output R/preapproval_report.md
   ```

## Stop conditions

catalog empty or invalid; loan_file.json missing or invalid; unreadable/encrypted document
needed by a blocking item (that item is MISSING, and you say so); conflicting identity
information in loan_file.review_items (audit continues but every affected item is REVIEW and
the report says so); validator exit 2.

## Completion report (exact format)

```
preapproval-audit — <loan_id> / <run_id>
Status: COMPLETE | STOPPED (<reason>)
Catalog: v<version>, <n> PREAPPROVAL items, <reviewed by ... | catalog not yet reviewed by a licensed reviewer>
Coverage: <items_evaluated>/<catalog_items_in_phase> = <coverage_percent>%
Counts: PASS <n> FAIL <n> MISSING <n> REVIEW <n> NOT_APPLICABLE <n>; blocking open <n>
Blocking FAIL/MISSING (first): <finding_id rule_id — one line each>
REVIEW items and reviewer role: <list>
Conflicts: <list or none>   Missing documents: <list or none>
Calculations run: <method — output, per finding>
Known limitations: <copied from the audit>
Outputs: R/preapproval_audit.json, R/preapproval_report.md, R/run_manifest.json (sha256 in manifest)
DECISION SUPPORT ONLY — not a credit or compliance decision.
```
