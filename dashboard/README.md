# Dashboard

Static front end for the Mpire audit API. Plain HTML, CSS, and JavaScript — no build step,
no npm, no framework. It shows derived, masked audit output only and never renders or stores a
source document.

## Files

| File | Purpose |
|---|---|
| `index.html` | Shell: sidebar nav, decision-support banner, preview banner, `<main>`, footer, script tags |
| `styles.css` | Theme tokens (light, dark via `prefers-color-scheme`, and `data-theme` on `<html>`), components |
| `app.js` | API clients, auth, hash router, views, stacked-bar chart, minimal Markdown renderer |
| `demo-data.js` | Sample data for preview mode, mirroring the API shapes for the three `LN-EXAMPLE-*` loans |

External resources: fonts from `fonts.googleapis.com` and the supabase-js UMD build from
`cdn.jsdelivr.net/npm/`. Everything else is inline. No other hosts are referenced.

## How it is served

The API (`python -m api`) serves `dashboard/index.html` at `/`, mounts the directory at `/dashboard`,
and serves `/config.js`. Open `http://127.0.0.1:8080/`.

For a static preview without the API, serve the repo root with any file server, e.g.
`python -m http.server 8000` and open `http://127.0.0.1:8000/dashboard/`. `/config.js` will 404
and the page drops into preview mode (see below).

## Config contract

`/config.js` must define:

```js
window.MPIRE_CONFIG = { backend: "memory" | "supabase", demo: true|false,
                        supabaseUrl: "...", supabaseAnonKey: "...", apiBase: "/api" };
```

The service key is never part of this object and the page never reads or stores one.

## How preview vs connected mode is decided

1. If `window.MPIRE_CONFIG` is missing (the `/config.js` script tag 404'd — file://, static
   preview) → **preview mode**.
2. Otherwise the page calls `GET {apiBase}/health` (no auth). If that fails → **preview mode**.
3. If `demo` is true or `backend` is `memory` → connected, no auth; the API is called directly.
4. If `backend` is `supabase` → connected with auth (see below). If supabase-js did not load or
   `supabaseUrl`/`supabaseAnonKey` are missing → preview mode.

Preview mode serves `window.MPIRE_DEMO_DATA` through the same route table as the HTTP client,
shows the yellow "PREVIEW — sample data, not connected" banner, and disables every write control
(decision buttons, run-request form, PATCH controls) with a tooltip. The sample data is synthetic
and obviously so (`LN-EXAMPLE-*`, `RUN-DEMO-*`, "demo data" in every description).

## Auth flow (supabase backend)

- `supabase.createClient(supabaseUrl, supabaseAnonKey)`; the session is kept by supabase-js in
  browser storage and picked up on reload via `auth.getSession()` / `onAuthStateChange`.
- Sign-in view: email + password (`signInWithPassword`) or "Send magic link" (`signInWithOtp`
  with `emailRedirectTo` = the dashboard URL).
- Every `/api` request carries `Authorization: Bearer <access_token>`.
- A `401` from the API clears the session and returns to the sign-in view.
- The sidebar "Sign out" button calls `auth.signOut()`.

## Views (hash routes)

| Route | View |
|---|---|
| `#/overview` | KPI tiles (`/api/summary`), stacked bar of finding results per latest run (`/api/runs/latest`), Section 10 eval targets (`/api/eval/latest`), known limitations |
| `#/loans` | Loan table (`/api/loans`) |
| `#/runs/<loan>/<run>?tab=` | Run detail: findings, missing documents, conflicts, proposed actions, documents, reports, manifest |
| `#/review?role=` | Review queue grouped by reviewer role; CONFIRMED / OVERRIDDEN / NEEDS_INFO → `POST /api/review-decisions` |
| `#/requests?status=` | Run requests list, create form (`POST`), status PATCH control |
| `#/search?q=` | `GET /api/findings/search` |

Every write requires a note and is disabled in preview mode. API errors (`{"detail": ...}`) are
shown inline next to the control that triggered them.

## Safety rules the code follows

- No `innerHTML`. Every string from the API reaches the DOM through `textContent` (the `el()`
  helper throws if asked to set `html`). The Markdown renderer builds DOM nodes and never passes
  HTML through.
- No inline event handlers in `index.html`; all listeners are attached in `app.js`.
- Values are shown as the API returns them (decimal strings, masked accounts, short sha256).
- The page never reads, stores, or sends a service key.

## Adding a view

1. Write `function viewThing(root, params, query)` in `app.js` next to the other views. It
   receives an empty `<div class="stack">`, the regex captures from the route, and the query
   object. Return a Promise so the router can surface errors; append `loading()`, then replace
   it with content, `emptyState(...)`, or `errorBox(err)`.
2. Add `{ re: /^\/thing$/, view: viewThing, nav: "thing" }` to `ROUTES`.
3. Add `<a href="#/thing" data-nav="thing">Thing</a>` to the nav in `index.html`.
4. Build DOM with `el(tag, attrs, ...children)` — never `innerHTML`. Reuse `table()`, `dl()`,
   `statusPill()`, `resultChip()`, `decisionForm()`, and `writeGuard()` for write controls.
5. If the view calls a new API route, add it to `docs/api-contract.md` first and to
   `tests/unit/test_dashboard_static.py` (`USED_ROUTES`).
6. If it needs a fixture in preview mode, extend `demo-data.js` and the `DemoApi` route table.
