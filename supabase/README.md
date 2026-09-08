# Supabase backend

Supabase holds **derived, masked audit outputs** so the dashboard, the REST API,
and the MCP server can read them from anywhere. It is this system's own
datastore, not an LOS: writing to it is not one of the approval-gated external
writes in `CLAUDE.md`, but the same data rules apply. Source documents, credit
reports, statements, and unmasked identifiers never go here.

## One-time setup

1. Create a project at https://supabase.com (or reuse one). Note the project URL,
   the `anon` key, and the `service_role` key from Project Settings → API.
2. Apply `migrations/0001_init.sql`: either paste it into the SQL editor, or
   with the Supabase CLI:
   ```bash
   supabase link --project-ref <ref>
   supabase db push
   ```
3. Copy `.env.example` to `.env` and fill in the values. `.env` is ignored by Git
   and denied to Claude by the proposed hooks.
4. Enable Email auth (Authentication → Providers) and invite the dashboard users.
   Every signed-in user can read; nobody can edit audit content from the
   dashboard. Decisions and run requests are append-only rows stamped with the
   user's id.

## Who uses which key

| Component | Key | Why |
|---|---|---|
| `scripts/sync/push_run.py` | `service_role` (server-side only) | Inserts runs and findings; bypasses RLS |
| REST API (`api/`) | Forwards the caller's Supabase JWT | RLS applies per user |
| MCP server (`mcp_server/`) | `service_role` or a user JWT via env | Read tools; the only writes are run requests and decisions |
| Dashboard (browser) | `anon` key + the user's session | Talks to the API, not to Supabase directly |

## Schema at a glance

`loans` → `runs` → (`documents`, `findings`, `review_items`, `missing_documents`,
`conflicts`, `proposed_actions`, `approvals_required`, `reports`). Dashboard
state lives in `run_requests`, `review_decisions`, and `action_decisions`, all
append-only. `eval_reports` stores harness summaries. Views `v_latest_runs`,
`v_review_queue`, and `v_dashboard_summary` feed the dashboard; RPC
`search_findings(q)` powers search.

Database constraints mirror the JSON Schema invariants (PASS needs evidence,
FAIL and MISSING need a proposed action, REVIEW needs a reason and reviewer),
so nothing unevidenced can be stored even with the service key.

## Local development without Supabase

Set `MPIRE_REPO_BACKEND=memory`. The API and MCP server then run on an
in-memory repository seeded from `output/audits/` (or the bundled example),
which is what the test suite uses.
