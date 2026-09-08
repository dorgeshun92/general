# `api/` — Mpire Mortgage Ops REST API

FastAPI service over `services/` that lets the dashboard (and curl) read masked,
de-identified audit outputs and record append-only human decisions. The contract is
`docs/api-contract.md`; this README covers running it, the auth model, and examples.

## What this API cannot do

- It **does not** write to any LOS (Arive, LendingPad), send email or SMS, run AUS,
  trigger TRID, price, select a lender, or submit a loan. There is no code path for any
  of those; a `run request` is a queue row that a human picks up in Claude Code.
- It **does not** serve source documents, credit reports, statements, or extracted text.
  Only derived JSON (findings, inventory metadata, hashes) and masked Markdown reports.
- It **does not** decide anything. Results are decision support (`PASS`/`FAIL`/`MISSING`/
  `REVIEW`/`NOT_APPLICABLE`, `READY`/`NOT_READY`/`HUMAN_REVIEW`) for licensed staff.
- It never modifies `output/` or fixtures; `POST /api/sync/runs` only *reads* a run
  directory and upserts it into the datastore.

## Run

```bash
pip install -r requirements.txt
MPIRE_REPO_BACKEND=memory   python -m api     # http://127.0.0.1:8080, seeded demo data (LN-EXAMPLE-*)
MPIRE_REPO_BACKEND=supabase python -m api     # needs .env (see .env.example)
```

`MPIRE_API_HOST` / `MPIRE_API_PORT` set the bind address. OpenAPI: `/api/docs`, `/api/openapi.json`.
`/` serves `dashboard/index.html` (or a placeholder while the dashboard is being built),
`/dashboard/*` serves the static tree, and `/config.js` hands the browser
`window.MPIRE_CONFIG = {backend, demo, supabaseUrl, supabaseAnonKey, apiBase}` — never the service key.

Startup logs `Settings.redacted()` (keys shown as `set`/`null`, never values). The access log is one
JSON line per request with method, path, status, duration, request id and caller id — no headers
and no query strings, so tokens never reach the log.

## Auth model

| Backend | User routes (`/api/*`) | Service routes (`POST /api/eval`, `POST /api/sync/runs`) |
|---|---|---|
| `memory` | No auth. Caller id is `local-dev`. | `X-Service-Key: local-dev` |
| `supabase` | `Authorization: Bearer <Supabase user JWT>` required. | `X-Service-Key: <SUPABASE_SERVICE_ROLE_KEY>` |

Supabase details:

- The user's JWT is forwarded to PostgREST on every query, so **row-level security applies per
  user**. The user-route client uses the `anon` key as `apikey`; the service-route client uses the
  service-role key and bypasses RLS (server-side only, never from a browser).
- If `SUPABASE_JWT_SECRET` is set, the API verifies the JWT locally (HS256, audience
  `authenticated`) and uses its `sub` (the user uuid) as the caller id, which is stamped into
  `decided_by` / `requested_by` server-side. Client-supplied values for those fields are ignored.
- If it is **not** set, the token is forwarded unverified (Supabase still verifies it and RLS still
  applies) and the caller id is the constant `jwt`. Because `decided_by` / `requested_by` are `uuid`
  columns defaulting to `auth.uid()`, the API then omits them and lets the database stamp the real
  user. Set the secret for local verification and faster 401s; Supabase projects that have moved
  to asymmetric (ES256/RS256) signing keys must leave it unset.
- `X-Service-Key` is compared with `hmac.compare_digest`. The literal `local-dev` key is accepted
  only on the `memory` backend, or on `supabase` when `MPIRE_ALLOW_LOCAL_SERVICE=1` is set
  explicitly (a startup warning is logged). Missing key → `401`, wrong key → `403`.
- No token or key is ever echoed in a response body, exception message, or log line.

Errors: `404` unknown loan/run/report/request, `409` decision on an unknown target, `400`
run directory failed schema or PII checks, `401`/`403` auth, `422` validation, `502` datastore
failure (PostgREST message is not forwarded). Every error body is `{"detail": "..."}`.

Security headers on every response: `Content-Security-Policy` (`script-src 'self'
https://cdn.jsdelivr.net`, `connect-src 'self' https://*.supabase.co`), `X-Content-Type-Options:
nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cache-Control: no-store`,
`X-Request-ID` (echoed if the client sends a safe one, otherwise generated).

## curl examples

Memory backend (no auth):

```bash
B=http://127.0.0.1:8080/api
curl -s $B/health                                                   # {"status":"ok","backend":"memory","version":"1.0.0","demo":true}
curl -s $B/summary
curl -s $B/loans                                                    # each loan + latest_run
curl -s $B/loans/LN-EXAMPLE-0001
curl -s $B/loans/LN-EXAMPLE-0001/runs
curl -s "$B/runs?limit=10"
curl -s $B/runs/latest
curl -s $B/runs/LN-EXAMPLE-0001/RUN-DEMO-0001                       # RunDetail: bundle + decisions
curl -s "$B/runs/LN-EXAMPLE-0001/RUN-DEMO-0001/findings?result=REVIEW&blocking=true"
curl -s "$B/runs/LN-EXAMPLE-0001/RUN-DEMO-0001/findings?audit_type=PREAPPROVAL&rule_id=PRE-INCOME-001"
curl -s $B/runs/LN-EXAMPLE-0001/RUN-DEMO-0001/documents
curl -s $B/runs/LN-EXAMPLE-0001/RUN-DEMO-0001/reports               # [{name, sha256}]
curl -s $B/runs/LN-EXAMPLE-0001/RUN-DEMO-0001/reports/report.md     # text/markdown
curl -s "$B/findings/search?q=paystub&limit=20"
curl -s "$B/review-queue?reviewer_role=UNDERWRITER&loan_id=LN-EXAMPLE-0001"

curl -s -X POST $B/review-decisions -H 'content-type: application/json' \
  -d '{"loan_id":"LN-EXAMPLE-0001","run_id":"RUN-DEMO-0001","audit_type":"PREAPPROVAL","target_id":"F-004","decision":"CONFIRMED","note":"verified against DOC-003 p2"}'
curl -s -X POST $B/review-decisions -H 'content-type: application/json' \
  -d '{"loan_id":"LN-EXAMPLE-0001","run_id":"RUN-DEMO-0001","audit_type":null,"target_id":"RV-001","decision":"NEEDS_INFO"}'
curl -s -X POST $B/action-decisions -H 'content-type: application/json' \
  -d '{"loan_id":"LN-EXAMPLE-0001","run_id":"RUN-DEMO-0001","audit_type":"SUBMISSION_READINESS","action_id":"A-001","decision":"ACCEPTED"}'

curl -s "$B/run-requests?status=QUEUED"
curl -s -X POST $B/run-requests -H 'content-type: application/json' -d '{"loan_id":"LN-EXAMPLE-0002","note":"new paystub received"}'
curl -s -X PATCH $B/run-requests/<request_id> -H 'content-type: application/json' -d '{"status":"COMPLETED","run_id":"RUN-2026-09-08-01"}'

curl -s $B/eval/latest                                              # EvalReport or null
curl -s -X POST $B/eval -H 'X-Service-Key: local-dev' -H 'content-type: application/json' --data-binary @output/eval/<stamp>/eval_report.json
curl -s -X POST $B/sync/runs -H 'X-Service-Key: local-dev' -H 'content-type: application/json' -d '{"loan_id":"LN-EDGE-CLEAN","run_id":"t1"}'

curl -s http://127.0.0.1:8080/config.js
```

Supabase backend: add `-H "Authorization: Bearer $USER_JWT"` to every `/api` call above (except
`/api/health`), and use `-H "X-Service-Key: $SUPABASE_SERVICE_ROLE_KEY"` for `/api/eval` and
`/api/sync/runs`. Keep the service key on the server; never put it in a browser or a chat.

`POST /api/sync/runs` loads `MPIRE_OUTPUT_DIR/<loan_id>/<run_id>` through
`services.run_loader.load_run_dir` (schema + PII gate). If
`tests/fixtures/deidentified/<loan_id>/MANIFEST.yaml` exists, its `pii_pattern_allowlist` and
`description` are passed along. Ids are pattern-checked and the resolved path must stay inside
`MPIRE_OUTPUT_DIR`; `..`, absolute paths, and symlink escapes are refused.

## Tests

```bash
python -m pytest tests/unit/test_api_*.py -q
```

`test_api_routes.py` (every route, filters, error mapping), `test_api_sync.py` (a real intake run
directory from `scripts/intake/inventory.py`, confinement), `test_api_security.py` (headers,
`/config.js`, access-log hygiene, OpenAPI coverage, static mount), `test_api_auth_supabase.py`
(bearer/JWT rules and header forwarding against an `httpx.MockTransport` PostgREST).
