---
name: loan-file-intake
description: Inventory, hash, classify, and extract facts from ONE de-identified loan-package directory into output/audits/<loan_id>/<run_id>/document_inventory.json and loan_file.json (read-only intake, MVP 1). Use when the user asks to inventory, intake, classify, hash, deduplicate, or extract facts from a loan file or fixture directory, or asks "what documents are in this file". Do NOT use for evaluating checklist rules, deciding PASS/FAIL, readiness status, borrower drafts, or the full end-to-end audit (that is /mortgage-file-audit). Do NOT use on any directory outside tests/fixtures/deidentified/.
argument-hint: <loan-directory under tests/fixtures/deidentified/> [--run-id <id>]
allowed-tools: Read, Glob, Grep, Bash(python scripts/intake/*), Bash(python scripts/validate_schema.py*), Bash(python scripts/audit/run_manifest.py*), Bash(sha256sum *), Bash(ls *), Write(output/audits/**), Edit(output/audits/**)
---

# loan-file-intake

Read-only intake for one loan package. Deterministic scripts do the inventory, hashing,
page counting, duplicate detection, and text extraction; you review classifications and
make sure every extracted fact carries evidence. You never modify a source document.

Rules that apply throughout: `CLAUDE.md` (non-negotiable rules 1, 3, 7, 9, 10; data rules).
Schemas: `schemas/document_inventory.schema.json`, `schemas/loan_file.schema.json`,
`schemas/common.defs.schema.json` (evidence record definition at `$defs/evidence`).

## Inputs

`$ARGUMENTS` = `<loan-dir> [--run-id <id>]`. `<loan-dir>` must be a directory under an
approved root in `config/approved_data_locations.yaml` (currently only
`tests/fixtures/deidentified/`). If no `--run-id` is given, use `RUN-<YYYYMMDD>-<HHMMSS>` (UTC).
`loan_id` is the directory basename.

## Procedure

1. **Preconditions (stop on any failure; write nothing).**
   - `ls <loan-dir>` — the directory exists and is under an approved root.
   - `Read <loan-dir>/MANIFEST.yaml` — it exists and declares `deidentified: true`,
     `deidentified_by`, `deidentified_at`. Missing or `false` → STOP: "not an approved
     de-identified fixture".
   - Skim filenames and, after step 2, the first page of extracted text. If anything looks like
     live borrower PII (real full SSNs, real account numbers unmasked, production LOS ids),
     STOP: "possible live PII" and report which file. Do not continue.

2. **Run the deterministic inventory.**
   ```
   python scripts/intake/inventory.py <loan-dir> --run-id <run_id> --out output/audits
   ```
   Writes `output/audits/<loan_id>/<run_id>/document_inventory.json`, `loan_file.json`
   (a skeleton: documents and review_items filled, every fact null until evidenced),
   `extracted_text/`, and `intake_report.json`. Exit codes: `0` ok; `1` internal error → STOP
   and report the traceback; `2` fail-closed (an output failed schema validation or the
   masking gate; only `intake_error.json` is written) → STOP and report it verbatim; `3` stop
   condition (manifest missing/invalid, directory outside approved roots, live-PII heuristic)
   → STOP and report the reason. Never rerun with different inputs to "get past" an exit 3.

3. **Validate both outputs before reading them further.**
   ```
   python scripts/validate_schema.py output/audits/<loan_id>/<run_id>/document_inventory.json --schema document_inventory
   python scripts/validate_schema.py output/audits/<loan_id>/<run_id>/loan_file.json --schema loan_file
   ```
   Any non-zero exit → STOP: "schema validation failed" (include the printed errors). Do not
   hand-edit an output to make it validate.

4. **Review classifications marked LOW or MEDIUM confidence.** Read `document_inventory.json`.
   For every document whose `classification_confidence` is `LOW` or `MEDIUM`, or whose
   `document_type` is `UNKNOWN`/`OTHER`, open the matching file under `extracted_text/` and
   judge the type from the text (letterhead, form numbers, column headings, statement periods).
   Do NOT change `document_inventory.json` or `loan_file.json`. Instead write your proposals to
   `output/audits/<loan_id>/<run_id>/classification_review.json`:
   ```json
   {"loan_id": "...", "run_id": "...", "status": "PROPOSED_HUMAN_APPROVAL_REQUIRED",
    "proposals": [{"document_id": "DOC-004", "filename": "...", "current_type": "UNKNOWN",
      "current_confidence": "LOW", "proposed_type": "BANK_STATEMENT",
      "basis": "page 1 shows 'Statement period' and a masked account ****1234",
      "page": 1, "approver_role": "PROCESSOR"}]}
   ```
   Every proposal cites a page and quotes the text that supports it. If you cannot tell, leave
   the document as classified and say so in `review_items`-style language in the completion
   report. Reclassification is applied only by a human (PROCESSOR role per `config/roles.yaml`).

5. **Check that every extracted fact carries evidence.** Read `loan_file.json`. For every fact
   with a non-null `value` (loan, borrowers, income, assets, liabilities, properties, contract),
   confirm `evidence_ids` is non-empty and every referenced record in `evidence[]` has
   `document_id`, `filename`, `page`, and `extracted_value`. If you find a value with no
   evidence: do not delete it and do not invent evidence — record it under `review_items` as
   `LOW_CONFIDENCE_EXTRACTION` in your completion report and in `classification_review.json`
   under `"unevidenced_facts"`. A value you cannot support stays `null` with status `UNKNOWN`.
   You may add a fact to `loan_file.json` ONLY if you also add its evidence record (document id,
   filename, page, verbatim extracted value, `extraction_method: MANUAL`, confidence) and re-run
   step 3 afterwards. Never add a fact from memory or inference (CLAUDE.md source precedence).

6. **Duplicates, unreadable files, identity conflicts.** From `document_inventory.json`
   (`duplicates`, `unreadable`) and `loan_file.json` (`review_items`, `borrower_names_found`),
   list: duplicate documents (keep both; note `duplicate_of`), unreadable or encrypted files,
   possible missing pages, and any document whose borrower names do not match the 1003. These
   are reported, never silently resolved.

7. **Write the run manifest.**
   ```
   python scripts/audit/run_manifest.py write --run-dir output/audits/<loan_id>/<run_id> \
     --skill loan-file-intake --run-id <run_id> --loan-id <loan_id> \
     --input <loan-dir>/MANIFEST.yaml --input <each source file> \
     --output output/audits/<loan_id>/<run_id>/document_inventory.json \
     --output output/audits/<loan_id>/<run_id>/loan_file.json \
     --output output/audits/<loan_id>/<run_id>/intake_report.json \
     --output output/audits/<loan_id>/<run_id>/classification_review.json
   python scripts/audit/run_manifest.py verify output/audits/<loan_id>/<run_id>
   ```
   If you stopped early, still write the manifest with `--stop-condition "<reason>"` listing
   whatever outputs exist.

## Stop conditions (stop, report, do not guess)

- directory missing, outside an approved root, or without a valid `MANIFEST.yaml`
- any sign of live borrower PII
- `inventory.py` exit 2 or 3
- schema validation fails for either output
- a document is unreadable, encrypted, or appears to be missing pages (report; the audit
  skills decide whether it blocks)
- conflicting identity information (names on documents do not match the 1003)

## Completion report (exact format)

```
loan-file-intake — <loan_id> / <run_id>
Status: COMPLETE | STOPPED (<stop condition>)
Files: <n> inventoried, <n> pages, <n> duplicates, <n> unreadable/encrypted
Classification: <n> HIGH, <n> MEDIUM, <n> LOW, <n> UNKNOWN; <n> reclassification proposals (classification_review.json, human approval required)
Facts extracted with evidence: <n>; unevidenced values found: <n> (listed below)
Review items: <list review_id, category, description>
Identity/duplicate/readability notes: <list or "none">
Outputs (sha256 in run_manifest.json):
  output/audits/<loan_id>/<run_id>/document_inventory.json
  output/audits/<loan_id>/<run_id>/loan_file.json
  output/audits/<loan_id>/<run_id>/classification_review.json
  output/audits/<loan_id>/<run_id>/run_manifest.json
Not done here: no checklist evaluation, no PASS/FAIL, no readiness status.
```

## Safety

- Input directory is read-only: never rename, move, edit, or delete a source file.
- No network, no email, no LOS, no MCP tools of any kind. Write only under `output/audits/`.
- Mask SSNs and account numbers in everything you print (`scripts/common/masking.py` is applied
  by the scripts; do not paste raw extracted text containing digits into the report).
- Model inference is never recorded as a fact; it is only ever a proposal for a human.
