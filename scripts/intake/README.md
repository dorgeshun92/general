# scripts/intake — deterministic loan-file-intake tooling

Read-only, no network, no OCR. These scripts do the "facts" half of Skill 1
(`loan-file-intake`); the skill's SKILL.md calls them and the model does judgment on
their output. Nothing here modifies, renames, or deletes anything under the input
directory. Derived data goes only under the `--out` root (default `output/audits`).

## CLI

```
python scripts/intake/inventory.py <loan-dir> --run-id <id> [--out output/audits]
```

| exit | meaning |
|------|---------|
| 0 | outputs written and schema-valid |
| 1 | unexpected internal error (traceback on stderr) |
| 2 | fail closed: an output failed schema validation or the masking gate; only `intake_error.json` was written |
| 3 | stop condition: manifest missing/invalid, directory not under an approved root, symlink escape, output dir inside the input dir, or the live-PII heuristic fired on a token that is not allowlisted; nothing is written |

Outputs land in `<out>/<loan_id>/<run_id>/`:

- `document_inventory.json` — schema `document_inventory`
- `loan_file.json` — schema `loan_file`, a skeleton: `documents` and `review_items`
  filled, every fact `null` with `status: UNKNOWN`, arrays empty, `contract: null`,
  `evidence: []`
- `extracted_text/DOC-xxx.json` — masked per-page text for every readable PDF
  (`method` is `TEXT_LAYER` or `NO_TEXT_LAYER`; OCR is recorded as not attempted)
- `intake_report.json` — paths, sha256 of each output, counts, and the printed report

Both JSON documents are validated with `scripts.common.schema_registry.validate_document`
(plus the referential checks from `scripts/validate_schema.py`) and a masking gate before
anything is written. Helper CLIs:

```
python scripts/intake/pdf_pages.py <file-or-dir> [--json]   # page count + status per file
python scripts/intake/extract_text.py <file.pdf> [--json]   # masked per-page text
python scripts/intake/make_edge_fixtures.py [--root tests/fixtures/deidentified]
```

## MANIFEST.yaml contract

```yaml
loan_id: LN-EDGE-CLEAN            # ^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$
deidentified: true                # the YAML boolean true; "true", yes, 1 are rejected
deidentified_by: "fixture generator"
deidentified_at: "2026-09-08"     # ISO date
description: "optional"
pii_pattern_allowlist:            # optional, see below
  - "12345678"
```

The directory must also be under one of `approved_input_roots` in
`config/approved_data_locations.yaml`. Real paths are compared, so a symlink that
points outside the approved tree is refused, as is any file inside the loan directory
that resolves outside it and any symlinked subdirectory.

`MANIFEST.yaml` and `README.md` at the top level of the loan directory are control
files and are not inventoried as documents; the manifest is recorded via
`manifest_sha256`.

## Live-PII heuristic and `pii_pattern_allowlist`

During development the tooling stops (exit 3) when extracted page text looks like live
borrower data:

- any SSN-shaped value (`###-##-####`, `### ## ####`, or a bare 9-digit run), or
- two or more distinct 8+ digit runs on one page (account/routing-number shaped).

A de-identified fixture may carry obviously fake numbers so this check can be shown to
fire. List each exact token under `pii_pattern_allowlist` in `MANIFEST.yaml`; a finding
is suppressed only when the matched token equals an entry verbatim. The allowlist is
strict: if a page has two 8+ digit runs and only one is allowlisted, the run still
stops. Allowlisted hits are still surfaced as a `POSSIBLE_LIVE_PII` review item for
COMPLIANCE so a human confirms the values are synthetic.

## What each module does

- `manifest.py` — `check_manifest()`, `load_manifest()`, `check_approved_location()`,
  `compute_manifest_sha256()`, `looks_like_live_pii()`, `IntakeStopCondition`.
- `pdf_pages.py` — `probe_pdf()` returns `OK | ENCRYPTED | CORRUPT | EMPTY |
  UNSUPPORTED_FORMAT` and a page count. Encrypted files are never decrypted, not even
  with an empty password. Non-PDF extensions are listed, never opened as PDFs.
- `extract_text.py` — `extract_pages()` returns masked page text (`mask_text` is applied
  before anything leaves the function), a content fingerprint for duplicate detection,
  PII findings (masked tokens only), and `possible_missing_pages` from "Page X of Y"
  footers (Y greater than the page count, or a gap in the X sequence).
- `classify.py` — `KEYWORD_TABLE` (transparent dict of filename tokens and text markers),
  `classify()` returning `HIGH` (text marker), `MEDIUM` (filename only) or
  `UNKNOWN/LOW`; plus labeled heuristics for `document_date`, `statement_period`,
  borrower-name candidates (always LOW), and masked account tokens.
- `inventory.py` — orchestrates the above, assigns `DOC-001…` in sorted relative-path
  order, detects `IDENTICAL_HASH` and `SAME_CONTENT_DIFFERENT_FILE` duplicates, raises
  `CONFLICTING_IDENTITY` when name candidates differ across documents, validates, writes.

## Document status and review items

| status | when |
|--------|------|
| `ENCRYPTED` | pypdf reports the file encrypted |
| `UNREADABLE` | CORRUPT, EMPTY, or UNSUPPORTED_FORMAT |
| `DUPLICATE` | same bytes or same extracted text as an earlier document |
| `POSSIBLE_MISSING_PAGES` | footer claims more pages than exist, or a gap in page numbers |
| `REVIEW` | some or all pages have no text layer |
| `OK` | otherwise |

Review items use categories `ENCRYPTED_DOCUMENT`, `UNREADABLE_DOCUMENT`,
`LOW_CONFIDENCE_EXTRACTION`, `POSSIBLE_MISSING_PAGES`, `UNCLASSIFIED_DOCUMENT`,
`POSSIBLE_LIVE_PII`, `DUPLICATE_DOCUMENT`, and `CONFLICTING_IDENTITY`.

## Known limitations

- No OCR: pages without a text layer are recorded, not read. Scanned packages produce
  `REVIEW` documents and `LOW_CONFIDENCE_EXTRACTION` items.
- Classification is purely lexical (first page with text + filename). Cover pages, mixed
  bundles, and mislabeled files will be wrong or `UNKNOWN`; a human reviews.
- Missing-page detection needs "Page X of Y" footers; a bundle of several documents in
  one PDF is not flagged. Duplicate detection does not catch re-scans of the same paper.
- Name candidates come from `Borrower:`, `Employee:`, `Account holder:`, `Name:`,
  `Buyer:` style lines only and are always LOW confidence; legitimate co-borrowers also
  trigger `CONFLICTING_IDENTITY`.
- Dates assume US `m/d/yyyy`. `document_date` is the first plausible date (preferring a
  line that mentions "date"), which may be a period start rather than an issue date.
- The PII heuristic misses account numbers written with internal spaces and flags ZIP+4
  codes and 9-digit invoice numbers as SSN-shaped (conservative on purpose).
- Fixture PDFs are hand-built minimal PDF 1.4 with Helvetica; pypdf renders `'` as `’`.
