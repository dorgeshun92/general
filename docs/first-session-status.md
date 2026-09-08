# First build session — status against the playbook

Playbook section 15 lists what the first session should complete. Status as of
2026-09-08:

| Playbook item | Status | Notes |
|---|---|---|
| Install Claude Code, `claude --version`, `claude doctor` | Not applicable here | This scaffold was built in a remote Claude Code session, not on the Windows workstation. Run these on the company machine. |
| Private repository and folder skeleton | Done | Skeleton per section 3, `.gitignore` excludes borrower data, secrets, raw outputs, OCR temps, logs. |
| Add copies of the three checklists | **Blocked** | The three PDFs were not supplied. `docs/source-checklists/` holds only a README. |
| Add the recommended CLAUDE.md | Done | 83 lines; procedures live in skills. |
| Run the checklist-normalization prompt | **Blocked** on the PDFs | Procedure and validation tooling are ready: `docs/checklist-normalization.md`, `schemas/checklist_catalog.schema.json`, `scripts/catalog/`. The catalog is an empty template; nothing was invented. |
| Review the first 10 normalized rules manually | Blocked on the above | |
| Commit the approved baseline without borrower data | Done | All fixtures are synthetic and labeled. |

## Beyond section 15: MVP 1 components built ahead

| Section 14 definition-of-done item | Status |
|---|---|
| Checklist statements have stable IDs and page traceability | Schema, id convention, and coverage tooling ready; catalog empty until PDFs arrive |
| Canonical loan-file and audit-result schemas exist and validate | Done, with referential-integrity checks and example fixtures |
| Four component skills and master orchestrator run on de-identified fixtures | Skills written; intake runs end to end on the edge-case fixtures; audit skills stop with "catalog empty" by design |
| Every PASS contains evidence; every unknown remains unknown | Enforced by schema and validator |
| Supported calculations deterministic and reproducible | Done, Decimal with full trails and tests |
| System cannot access production credentials or perform external writes | No credentials exist; `docs/security-plan.md`, `.claude/settings.proposed.json` (142 deny rules), and three hook scripts written and covered by 311 tests, **not yet activated**; see plan section 10 for items to verify against the official hooks docs first |
| Test harness reports false PASS, evidence, calculation, schema, privacy failures | Done, compare-only until the `claude` CLI is on the pilot machine |
| Unit test suite | 1,042 tests passing (`python -m pytest`) across schemas, calculations, intake, audit tooling, hooks, and the harness |
| Licensed reviewer signs off on rules and answer keys | Open |
| Retrospective test set meets section 10 targets | Open; needs 10 de-identified real files and answer keys |
| Known limitations visible in every final report | Enforced by schema (`known_limitations` minItems 1) |

## Dashboard tier (added after the first session)

| Component | Status |
|---|---|
| Supabase schema with RLS, views, search RPC | Written; apply `supabase/migrations/0001_init.sql` to your project |
| Service layer (models, memory + Supabase repositories, run loader with PII gate) | Done, unit-tested against a mock PostgREST transport |
| REST API (`api/`, `docs/api-contract.md`) | Done; memory mode serves demo data, Supabase mode forwards user JWTs |
| MCP server (`mcp_server/`, `.mcp.json`) | Done; read tools plus append-only decision and run-request writes |
| Dashboard (`dashboard/`) | Done; preview mode with sample data when not connected |
| Sync script (`scripts/sync/push_run.py`) | Done; the only outbound path; validates and PII-scans first |
| Hook allowlist for `python -m api` / `python -m mcp_server` / `mcp__mpire-audit` | Proposed only (`docs/security-plan.md` §4.7) |
| Supabase project credentials | Not provided; fill `.env` from `.env.example` |

## Next actions, in order

1. Copy the three checklist PDFs into `docs/source-checklists/`.
2. Follow `docs/checklist-normalization.md`; review the first 10 items before the full write.
3. Answer `docs/decisions-needed.md` questions 4, 5, 7, and 8; fill `config/roles.yaml`.
4. Review `docs/security-plan.md`, run the hook tests, fill `docs/hook-test-log.md`, then activate `.claude/settings.proposed.json` as `.claude/settings.json`.
5. Prepare 10 de-identified closed files and answer keys per playbook section 11.
