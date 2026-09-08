# tests/fixtures/hook_inputs/

Harmless JSON payloads that drive the Claude Code hooks in
`.claude/hooks/` exactly as Claude Code would (`python .claude/hooks/<hook>.py < fixture.json`).
Every file is safe to feed to a hook: none of them touch real secrets, real
borrower data, or the network — the hooks are expected to *refuse* the
`deny_*` ones before anything runs.

| Prefix | Meaning | Expected result |
|---|---|---|
| `deny_secrets_*`, `deny_outside_repo_*` | reads of .env / secrets / credential stores / paths outside the repo | exit 2 |
| `deny_destructive_*` | deletion, destructive git, truncation redirects, source-document moves | exit 2 |
| `deny_network_*` | curl/wget/nc/ssh/pip/npm/http.server/inline python network, WebFetch, WebSearch, git push | exit 2 |
| `deny_mcp_*` | any `mcp__*` tool (LOS/email/SMS/AUS/pricing writes and non-allowlisted reads) | exit 2 |
| `deny_write_*`, `deny_edit_*` | writes outside allowed roots, onto source PDFs, enforcement files, unmasked SSN into output/ | exit 2 |
| `deny_uncertain_*`, `deny_unknown_tool` | variable expansion, process substitution, sudo, eval, env dumps, unknown tools | exit 2 |
| `allow_*` | the everyday read-only workflow (pytest, validator, git status/diff/log/add/commit, ls, reads of repo files, writes under output/tests/docs/scripts) | exit 0 |
| `failclosed_*`, `malformed_stdin.json` | payloads the hook cannot interpret | exit 2 (fail closed) |
| `post_write_*` | PostToolUse payloads for `post_write_validate.py` | exit 0 (ignored paths) |
| `stop_no_state` | Stop payload for a session that never ran an audit | exit 0 |
| `doc_*` | schema documents copied into temporary run directories by the post-write / stop-gate tests | n/a |

Paths inside the fixtures are repository-relative; the hooks anchor them at
the repository root, which is what Claude Code's absolute paths normalize to.
Fixtures deliberately omit `session_id` so end-to-end runs do not create
`output/.hook_state/` files; the session-tracking tests build their own
payloads with unique ids and clean up.

The activation log in `docs/hook-test-log.md` references these filenames.
