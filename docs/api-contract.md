# REST API contract (v1)

Served by `api/` (FastAPI). All paths are under `/api`. JSON bodies use the models in
`services/models.py`; money is a decimal string, dates are ISO 8601, identifiers are masked.
The API is read-mostly: the only writes are append-only dashboard rows (run requests, review
decisions, action decisions), service-only eval uploads, and a service-only local sync trigger.
Nothing here writes to an LOS, sends a message, or touches source documents.

## Auth

| Backend (`MPIRE_REPO_BACKEND`) | Rule |
|---|---|
| `memory` | No auth. Caller id is `local-dev`. Demo data is seeded at startup. |
| `supabase` | `Authorization: Bearer <Supabase user JWT>` on every `/api` route. The token is forwarded to PostgREST so RLS applies. If `SUPABASE_JWT_SECRET` is set the API also verifies the JWT (HS256, audience `authenticated`) and uses its `sub` as the caller id. |
| service routes | `X-Service-Key: <SUPABASE_SERVICE_ROLE_KEY>` (constant-time compare). Never accepted from a browser flow. |

Errors: `404 {"detail": ...}` for unknown loan/run/report, `409` for a decision on an unknown
target, `401`/`403` for auth, `422` for validation. All error bodies are `{"detail": "..."}`.

## Routes

| Method | Path | Returns | Notes |
|---|---|---|---|
| GET | `/api/health` | `{status, backend, version, demo}` | no auth |
| GET | `/api/summary` | `DashboardSummary` | |
| GET | `/api/loans` | `[{...Loan, latest_run: Run|null}]` | joins `latest_runs()` |
| GET | `/api/loans/{loan_id}` | `Loan` | |
| GET | `/api/loans/{loan_id}/runs` | `[Run]` newest first | |
| GET | `/api/runs?limit=` | `[Run]` newest first | default 50, max 500 |
| GET | `/api/runs/latest` | `[Run]` one per loan | |
| GET | `/api/runs/{loan_id}/{run_id}` | `RunDetail` | bundle + decisions |
| GET | `/api/runs/{loan_id}/{run_id}/findings?audit_type=&result=&blocking=&rule_id=` | `[Finding]` blocking first | |
| GET | `/api/runs/{loan_id}/{run_id}/documents` | `[Document]` | |
| GET | `/api/runs/{loan_id}/{run_id}/reports` | `[{name, sha256}]` | no content |
| GET | `/api/runs/{loan_id}/{run_id}/reports/{name}` | `text/markdown` | masked |
| GET | `/api/findings/search?q=&limit=` | `[Finding]` | rule id or text |
| GET | `/api/review-queue?reviewer_role=&loan_id=` | `[ReviewQueueEntry]` blocking first | excludes decided targets |
| POST | `/api/review-decisions` | `ReviewDecision` (201) | body `{loan_id, run_id, audit_type|null, target_id, decision, note?}`; `decided_by` = caller |
| POST | `/api/action-decisions` | `ActionDecision` (201) | body `{loan_id, run_id, audit_type, action_id, decision, note?}` |
| GET | `/api/run-requests?status=` | `[RunRequest]` | |
| POST | `/api/run-requests` | `RunRequest` (201) | body `{loan_id, note?}`; creates a QUEUED row only. Nothing runs automatically; a human runs `/mortgage-file-audit` in Claude Code. |
| PATCH | `/api/run-requests/{request_id}` | `RunRequest` | body `{status, run_id?}` |
| GET | `/api/eval/latest` | `EvalReport|null` | |
| POST | `/api/eval` | `EvalReport` (201) | **service only**; body is an `eval_report.json` |
| POST | `/api/sync/runs` | `Run` (201) | **service only**; body `{loan_id, run_id}`; loads `MPIRE_OUTPUT_DIR/<loan_id>/<run_id>` via `services.run_loader.load_run_dir` and upserts. `400` if the directory fails schema or PII checks. |
| GET | `/` and `/dashboard/*` | static dashboard | from `dashboard/` |
| GET | `/config.js` | `window.MPIRE_CONFIG = {...}` | `{backend, demo, supabaseUrl, supabaseAnonKey, apiBase: "/api"}`; never the service key |

OpenAPI is served at `/api/docs` and `/api/openapi.json`.

## Running

```bash
pip install -r requirements.txt
MPIRE_REPO_BACKEND=memory python -m api            # http://127.0.0.1:8080, demo data
MPIRE_REPO_BACKEND=supabase python -m api          # needs .env (see .env.example)
```
