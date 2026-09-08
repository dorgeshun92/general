# Checklist normalization procedure

Turns the three source PDFs into `config/checklist_catalog.yaml`. Run this
only after the PDFs are in `docs/source-checklists/`. The catalog is the
contract that `preapproval-audit` and `submission-readiness` evaluate against,
so nothing here may be invented or paraphrased.

## Step 1 — register the sources

For each PDF, record its filename, SHA-256, and page count under `sources:`
with a `source_id` (`SRC-PREAPPROVAL`, `SRC-LOANPARTNER`, `SRC-SUBMISSION`).

```bash
sha256sum docs/source-checklists/*.pdf
python scripts/intake/pdf_pages.py docs/source-checklists/*.pdf
```

Store a text extraction next to each PDF (`<name>.extracted.txt`) for
traceability. The PDF remains authoritative.

## Step 2 — run the normalization prompt in Claude Code

Give Claude Code the prompt below verbatim. It asks for 10 representative
items first; review those manually before allowing the full catalog to be
written.

```
Read every PDF in docs/source-checklists/. Convert every operative checklist
statement into config/checklist_catalog.yaml.
For each item include:
- id
- source_document
- source_page
- source_text
- phase
- category
- applies_when
- required_evidence
- fields_to_compare
- evaluation_method: deterministic | guideline_lookup | human_judgment
- result_values: PASS | FAIL | MISSING | REVIEW | NOT_APPLICABLE
- blocking_if_failed
- proposed_action
- required_human_role
Rules:
1. Do not invent mortgage guidelines.
2. Preserve ambiguous language and mark it REVIEW.
3. Do not treat absence of evidence as NOT_APPLICABLE.
4. If two documents conflict, retain both and create a conflict record.
5. Output a coverage report showing each source item and its catalog ID.
Before editing files, show me 10 representative normalized items and ask for approval.
```

## Step 3 — validate and report coverage

```bash
python scripts/validate_schema.py config/checklist_catalog.yaml --schema checklist_catalog
python scripts/catalog/coverage_report.py --catalog config/checklist_catalog.yaml \
    --extracted docs/source-checklists/ --out output/catalog/coverage_report.md
```

The coverage report lists every extracted source line and the catalog id
that covers it, plus lines with no id. Lines with no id are either
non-operative (headings, instructions) or missed items. Both must be
labeled explicitly.

## Step 4 — licensed sign-off

A licensed reviewer sets `reviewed_by` and `reviewed_at` in the catalog and
resolves or explicitly defers every entry in `conflicts:`. Until then the
audit skills run but label every report
"catalog not yet reviewed by a licensed reviewer".

## Rules that never change

- Similar rules are never merged silently. Keep both items; record the
  reasoning in `consolidation_decisions:` with an approver.
- Ambiguous wording keeps `ambiguous: true` and evaluates to REVIEW.
- Absence of evidence is MISSING, never NOT_APPLICABLE.
- Ids are stable forever. A withdrawn item keeps its id and gets
  `notes: "WITHDRAWN <date> <reason>"`; it is never reused.
