# Mpire Mortgage Operations Copilot

A Claude Code project that helps licensed mortgage personnel audit and prepare
loan files. **Current release: MVP 1, a read-only auditor.** It classifies
documents, reconciles facts, runs supported deterministic calculations,
identifies missing evidence, and prepares proposed corrections. It has no
write authority: no LOS, email, SMS, AUS, TRID, pricing, or submission access.

Built from `docs/build-playbook.md` (Implementation Playbook v1.0, owner
James Gomez, Mpire Financial). Decision support only; not a credit or
compliance decision.

## Layout

| Path | What it is |
|------|------------|
| `CLAUDE.md` | Always-on operating rules and approval gates. Read this first. |
| `.claude/skills/` | Mortgage procedures as Claude Code skills. Entry point: `/mortgage-file-audit <fixture-dir>`. |
| `.claude/hooks/`, `.claude/settings.proposed.json` | Proposed enforcement (deny rules, validators). Inactive until reviewed. |
| `.claude/rules/` | Short path-scoped rules that supplement `CLAUDE.md`. |
| `schemas/` | JSON Schemas for `loan_file`, `audit_result`, `document_inventory`, `checklist_catalog`. Every skill exchanges these. |
| `config/` | Checklist catalog (empty until the source PDFs are normalized), approved sources, approved data roots, roles. |
| `scripts/common/` | Schema registry, hashing, masking. |
| `scripts/validate_schema.py` | Validator CLI with referential-integrity checks. |
| `scripts/intake/` | Inventory, hashing, page counts, text extraction, classification. |
| `scripts/calculations/` | Decimal-only calculation engine with full trails. |
| `scripts/audit/` | Readiness gate, report renderer, run manifest. |
| `scripts/drafts/` | Borrower-facing DRAFT builder (never sends). |
| `scripts/eval/`, `scripts/catalog/` | Evaluation harness and catalog coverage tools. |
| `docs/` | Playbook, security plan, normalization procedure, decisions needed. |
| `tests/` | Unit tests, de-identified fixtures, human answer keys. |
| `output/` | Derived artifacts only. Ignored by Git. |

## Quick start

```bash
pip install -r requirements.txt
python -m pytest                      # everything must be green
python scripts/validate_schema.py config/checklist_catalog.yaml --schema checklist_catalog
python scripts/intake/inventory.py tests/fixtures/deidentified/LN-EDGE-CLEAN --run-id demo
```

Then, inside Claude Code in this directory:

```
/mortgage-file-audit tests/fixtures/deidentified/LN-EDGE-CLEAN
```

The audit skills stop immediately while `config/checklist_catalog.yaml` has no
items. Populate it from the three source checklists using
`docs/checklist-normalization.md` before expecting findings.

## Non-negotiables (see `CLAUDE.md` for the full list)

- A PASS requires cited evidence: document, filename, page, value.
- Missing evidence is MISSING or REVIEW, never PASS or NOT_APPLICABLE.
- Money is Decimal; dates are ISO 8601; account numbers and SSNs are masked.
- Skills read only from `tests/fixtures/deidentified/` and write only to `output/`.
- Never commit borrower files, credit reports, statements, tokens, or keys.

## Where this build stands

See `docs/first-session-status.md` for what the first build session
completed, what is blocked on the source PDFs, and the definition of done for
MVP 1.
