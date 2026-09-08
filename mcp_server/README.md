# `mcp_server/` — the `mpire-audit` MCP server

Exposes the derived, masked audit data (the same data as `docs/api-contract.md`) to Claude
Code and any other MCP client. Built on `mcp>=2.0` (`mcp.server.mcpserver.MCPServer`).
It is decision support for licensed mortgage personnel; it makes no credit or compliance
decision and has no external effect.

```bash
python -m mcp_server --selftest        # build a memory server, list tools, call mortgage_summary, exit 0
python -m mcp_server                   # stdio (what .mcp.json launches)
python -m mcp_server --transport streamable-http --host 127.0.0.1 --port 8765   # http://127.0.0.1:8765/mcp
```

## Tools

Names parallel the REST routes. Every tool returns a JSON object (also sent as
`structuredContent`) with an explicit `count` on lists; list tools take a `limit` with a
hard ceiling (500; 200 for search and runs) and report `truncated`. Errors come back as
`isError` results with a short message, never a traceback or a credential.

| Tool | Read/Write | Arguments | Returns |
|---|---|---|---|
| `mortgage_summary` | read | — | `DashboardSummary` fields + `server` |
| `mortgage_list_loans` | read | — | `{count, loans:[{...Loan, latest_run}]}` |
| `mortgage_list_runs` | read | `loan_id?`, `limit=20` (≤200) | run summaries, newest first |
| `mortgage_get_run` | read | `loan_id`, `run_id` | full `Run` + `child_counts` + `decision_counts` + `report_names` (no bundle) |
| `mortgage_list_findings` | read | `loan_id`, `run_id`, `audit_type?`, `result?`, `blocking?`, `rule_id?`, `limit=100` (≤500) | compact findings, blocking first (no calculation body) |
| `mortgage_get_finding` | read | `loan_id`, `run_id`, `audit_type`, `finding_id` | full finding incl. `calculation`, `guideline_source`, `evidence_ids`, `review_decisions` |
| `mortgage_search_findings` | read | `query`, `limit=25` (≤200) | compact findings across all loans |
| `mortgage_list_documents` | read | `loan_id`, `run_id` | document inventory metadata (never file contents) |
| `mortgage_review_queue` | read | `reviewer_role?`, `loan_id?`, `limit=100` (≤500) | open review entries, blocking first, decided targets excluded |
| `mortgage_get_report` | read | `loan_id`, `run_id`, `name="report.md"` | `{content_md, sha256, chars, truncated}`; capped at 60,000 chars; re-checked for unmasked PII |
| `mortgage_list_missing_documents` | read | `loan_id`, `run_id` | `{count, missing_documents}` |
| `mortgage_list_conflicts` | read | `loan_id`, `run_id` | `{count, conflicts}` |
| `mortgage_list_proposed_actions` | read | `loan_id`, `run_id` | `{count, pending, proposed_actions:[{..., decisions:[...]}]}` |
| `mortgage_list_approvals_required` | read | `loan_id`, `run_id` | `{count, approvals_required}` |
| `mortgage_eval_latest` | read | — | `{available, all_targets_met, targets, report_keys}` |
| `mortgage_list_run_requests` | read | `status?`, `limit=100` (≤500) | `{count, run_requests}` |
| `mortgage_create_run_request` | **write** (append-only) | `loan_id`, `note?` | creates a `QUEUED` row only. Nothing runs; a human still runs `/mortgage-file-audit` |
| `mortgage_record_review_decision` | **write** (append-only) | `loan_id`, `run_id`, `target_id`, `decision` (CONFIRMED/OVERRIDDEN/NEEDS_INFO), `audit_type?` (null for review items), `note?` | records a decision a **named human** stated; `decided_by` = configured identity |
| `mortgage_record_action_decision` | **write** (append-only) | `loan_id`, `run_id`, `audit_type`, `action_id`, `decision` (ACCEPTED/REJECTED/DEFERRED), `note?` | records a human's decision on a DRAFT action; nothing is executed |

Annotations: every read tool is `readOnlyHint=true, destructiveHint=false, idempotentHint=true,
openWorldHint=false`; the three write tools are `readOnlyHint=false, destructiveHint=false,
idempotentHint=false, openWorldHint=false` (append-only rows in our own database).

## Resources and prompt

| URI | MIME | Content |
|---|---|---|
| `mortgage://summary` | `application/json` | same as `mortgage_summary` |
| `mortgage://loans` | `application/json` | same as `mortgage_list_loans` |
| `mortgage://runs/{loan_id}/{run_id}` | `application/json` | same as `mortgage_get_run` |
| `mortgage://reports/{loan_id}/{run_id}/{name}` | `text/markdown` | the masked report text (same cap as the tool) |

Prompt `mortgage_review_queue_briefing(reviewer_role)` (LOAN_OFFICER, PROCESSOR, UNDERWRITER,
COMPLIANCE, MANAGEMENT) returns an instruction to summarize that role's open queue, blocking
first, citing ids as returned, without inventing facts and without recording any decision.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MPIRE_REPO_BACKEND` | `memory` | `memory` = in-process demo data seeded from `tests/fixtures/examples` (nothing leaves the machine); `supabase` = `services.factory.build_repository` |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_ANON_KEY` | — | read from `.env` by `services.config`; only used with the `supabase` backend |
| `MPIRE_MCP_BEARER` | unset | a Supabase **user JWT**; when set it is forwarded so row-level security applies to that user |
| `MPIRE_MCP_IDENTITY` | `mcp-server` | value written to `requested_by` / `decided_by` by the write tools |

**Service key warning.** With `MPIRE_REPO_BACKEND=supabase` and no `MPIRE_MCP_BEARER`, the
server uses `SUPABASE_SERVICE_ROLE_KEY`, which bypasses RLS. Run it that way only on a
trusted machine that already holds the key (the same one that runs the sync script). For a
per-user view, set `MPIRE_MCP_BEARER` to that user's JWT. Credentials always live in `.env`
(git-ignored), never in `.mcp.json` or a client config.

## Client configuration

Claude Code (project-scoped, already checked in as `.mcp.json` at the repo root):

```json
{
  "mcpServers": {
    "mpire-audit": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "env": { "MPIRE_REPO_BACKEND": "memory" }
    }
  }
}
```

To point it at Supabase, change `MPIRE_REPO_BACKEND` to `supabase` in `.mcp.json` (or in your
shell) and put the URL and key in `.env`; do not paste keys into `.mcp.json`. Note that the
proposed PreToolUse hook (`.claude/hooks/pre_tool_guard.py`) only allowlists `python -m`
for `pytest`, `scripts.*`, `json.tool` and `unittest`: the MCP client launches
`python -m mcp_server` itself, so that is unaffected, but Claude cannot start the server from
its own Bash tool while the hook is active.

Claude Desktop (`claude_desktop_config.json`; use an absolute `cwd`):

```json
{
  "mcpServers": {
    "mpire-audit": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "cwd": "/absolute/path/to/general",
      "env": { "MPIRE_REPO_BACKEND": "memory", "MPIRE_MCP_IDENTITY": "jane.doe" }
    }
  }
}
```

Remote clients: `python -m mcp_server --transport streamable-http --port 8765` and connect to
`http://127.0.0.1:8765/mcp`. Put it behind an authenticating proxy before exposing it beyond
localhost; the server itself performs no client authentication.

## Security model

- **What it cannot do.** There is no tool that sends email or SMS, writes to an LOS (Arive,
  LendingPad, or any other), runs AUS, triggers TRID, prices or locks, selects a lender,
  submits a loan, reads a source document, or starts an audit run. The server instructions
  say so, and `tests/unit/test_mcp_server.py` fails if a tool name ever matches
  `send|email|sms|los|aus|trid|submit|price|lock|intake|audit_run`.
- **Writes are append-only rows in our own datastore** (run requests, review decisions,
  action decisions). They record what a human decided; they never perform the action. The
  Supabase schema's constraints and RLS still apply.
- **Data rules from `CLAUDE.md` hold.** Rows come from validated, masked run bundles; report
  text is re-scanned with `scripts.common.masking.contains_unmasked_pii` and withheld if it
  fails. Money is a decimal string, dates ISO 8601, account numbers masked.
- **No secrets in responses or logs.** Backend failures are reported as the exception class
  and HTTP status only. The startup line on stderr says which auth mode is in use without
  printing a key. On stdio, nothing is ever written to stdout except protocol frames.
- **Identity.** `decided_by` / `requested_by` is the configured identity, not the human whose
  decision is being relayed; the tool descriptions require the human's name in `note`.

## Tests

```bash
python -m pytest tests/unit/test_mcp_*.py -q
```

The tests drive the server through the SDK's in-process client (`mcp.Client(server)`, the
in-memory transport) against a seeded `InMemoryRepository`, and run `--selftest` as a
subprocess.
