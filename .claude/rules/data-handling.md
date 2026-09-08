---
paths:
  - "scripts/intake/**"
  - "scripts/audit/**"
  - "scripts/common/masking.py"
  - "output/**"
  - "tests/fixtures/**"
---
# Data handling (supplements CLAUDE.md rules 7 and 9; enforced by .claude/hooks)

## De-identified roots only
- Read loan packages only from the roots in `config/approved_data_locations.yaml`
  (today: `tests/fixtures/deidentified/`). Every loan directory needs a
  `MANIFEST.yaml` with `deidentified: true`, `deidentified_by`, `deidentified_at`.
- If a directory looks like live PII (real names, unmasked SSNs, no manifest):
  stop, do not read further, report it. Never "fix" it by de-identifying in place.
- Source documents are never modified, moved, renamed, or copied into fixtures.
  Write derived data only under `output/`. The PreToolUse hook denies the rest.

## Masking
- SSNs appear only as `***-**-1234` (`scripts.common.masking.mask_ssn`);
  account numbers only as `****1234` (`mask_account`). Keep at most the last 4.
- Run `contains_unmasked_pii()` on any text before it goes into a JSON
  output, a report, a draft, a commit message, or a test assertion.
- The hook refuses writes under `output/` that contain an unmasked SSN pattern.

## No PII in logs
- Never print, log, or include in an exception message: full names with
  SSNs, account numbers, dates of birth, full addresses, document text.
  Log document IDs, page numbers, field names, and masked values instead.
- Do not write OCR/extraction temporaries outside `output/`; `*.ocr.*`,
  `*.log`, `tmp/` are git-ignored because they may carry PII.
- Reports and evidence citations use document ID + filename + page + masked value.

## Never commit
- Borrower files, credit reports, tax returns, bank statements, tokens,
  passwords, API keys. `.gitignore` is the backstop, not the control: check
  `git status` before `git add`, and never `git add -A` on `output/` or fixtures.
