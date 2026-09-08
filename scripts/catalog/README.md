# scripts/catalog — checklist catalog tools

Deterministic helpers for `config/checklist_catalog.yaml` (see
`docs/checklist-normalization.md`). Neither tool edits the catalog.

## coverage_report.py

```bash
python scripts/catalog/coverage_report.py --catalog config/checklist_catalog.yaml \
    --extracted docs/source-checklists/ --out output/catalog/coverage_report.md
```

Reads every `<name>.extracted.txt` in the extracted directory (pages separated
by `=== PAGE n ===` lines) and, for every non-empty line, lists the catalog
item ids whose `source_text` matches it. Matching normalizes whitespace and
case; an item covers a line when its `source_text` is contained in the line or
the line (3+ characters) is contained in the `source_text`.

Per source file: a table of page, line number, text, and ids (or `NO ID`),
plus counts of covered and uncovered lines. Summary labels:

- `NO ID` — a source line no item covers: a heading/instruction or a missed item. Label each explicitly during review.
- `ORPHAN ITEM` — an item whose `source_text` was found on no source line (paraphrased or invented wording).
- `PAGE MISMATCH` — an item found only on pages other than its `source_page` (checked within the file named by `sources[].filename` when it resolves, else in any file).

Exit 0 when there are no orphans or page mismatches, 1 otherwise, 2 when the
catalog or directory cannot be read. Works with the empty template catalog
(every line is `NO ID`, exit 0).

## stats.py

```bash
python scripts/catalog/stats.py [--catalog config/checklist_catalog.yaml] [--json]
```

Validates the catalog (schema + `validate_schema.integrity_errors`) and prints
counts by phase, category, evaluation_method, blocking_if_failed and
ambiguous; withdrawn items; unresolved conflicts; consolidation decisions
awaiting approval; normalized/reviewed status. Exit 0 valid, 1 invalid (stats
still printed), 2 unreadable.

Tests: `tests/unit/test_catalog_coverage.py`, `tests/unit/test_catalog_stats.py`.
