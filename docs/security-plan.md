# Security plan — MVP 1 (read-only auditor)

Status: PROPOSED. Written before the hooks (playbook §9). The hook scripts in
`.claude/hooks/` and the settings in `.claude/settings.proposed.json`
implement this plan and reference it. **Nothing is active until a human
completes the activation checklist (§8) and renames
`settings.proposed.json` to `settings.json`.**

## 1. Enforcement principle

From `CLAUDE.md` ("Development workflow") and playbook §9: **prompt
instructions are advisory; hooks and permissions are enforcement.** CLAUDE.md,
skills, and `.claude/rules/*.md` tell Claude what to do. Only
`permissions` and hooks in `.claude/settings.json` can stop a tool call. Every
rule in this plan therefore names the exact permission rule and/or hook check
that enforces it, and the fixture that proves the check works.

Two layers, always both:

| Layer | Where | Strength |
|---|---|---|
| Permissions | `settings.json → permissions.deny/allow/ask` | Evaluated by Claude Code before the hook. `Bash(...)` patterns are prefix matches and are defeated by chaining (`ls && rm -rf x`), quoting, or `bash -c`. Treat as defense in depth. Deny takes precedence over allow. |
| PreToolUse hook | `.claude/hooks/pre_tool_guard.py` | Parses the full command, checks every segment, resolves paths, fails closed. **This is the real control.** |

## 2. Scope and assumptions

- MVP 1 has **no write authority**: no LOS, email, SMS, AUS, TRID, pricing,
  lender selection, or submission writes in any form. The MCP allowlist is
  empty (`pre_tool_guard.MCP_ALLOWLIST = frozenset()`).
- No production credentials exist in the environment. If one is ever added,
  it goes in `.env` / `secrets/` / `credentials/` — locations this plan denies.
- Development inputs are only `tests/fixtures/deidentified/**` (see
  `config/approved_data_locations.yaml`); outputs only under `output/`.
- Hook scripts run with the project root as working directory and receive one
  JSON object on stdin. Python 3.11 is on `PATH` as `python`.

## 3. Hook contract (as implemented)

| Event | Matcher (`settings.proposed.json`) | Script | Allow | Block |
|---|---|---|---|---|
| PreToolUse | `Bash\|Read\|Write\|Edit\|MultiEdit\|NotebookEdit\|Grep\|Glob\|LS\|WebFetch\|WebSearch\|mcp__.*` | `pre_tool_guard.py` | exit 0 | exit 2 + reason on stderr |
| PostToolUse | `Write\|Edit\|MultiEdit\|NotebookEdit` | `post_write_validate.py` | exit 0 (+ note on stdout) | exit 2 + validator errors on stderr |
| Stop | `""` (all) | `stop_gate.py` | exit 0 | stdout `{"decision":"block","reason":…}` exit 0; or exit 2 on hook error |

Common to all scripts (`hook_common.py`):
- stdin JSON parsed strictly (must be an object) — anything else → exit 2.
- `run_fail_closed()` wraps `main()`: **any exception → exit 2** with a
  "fail closed" message. A broken hook never becomes a silent allow.
- Repository root is derived from the script's own location, never from an
  environment variable (so it cannot be redirected).
- `session_id` is validated against `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`
  before it is used as a file name under `output/.hook_state/`.

Pure, unit-testable entry points: `pre_tool_guard.decide(tool_name,
tool_input) -> (allow, reason)`, `post_write_validate.validate_written_file()`,
`stop_gate.evaluate()`.

## 4. Rules

Legend: **P** = permission rule, **H** = hook check, **F** = fixture in
`tests/fixtures/hook_inputs/` (all fixtures are harmless; the hook must refuse
them *before* anything runs).

### 4.1 Deny access to secrets, credential stores, and unapproved directories

| Target | P (deny) | H (`pre_tool_guard`) | F |
|---|---|---|---|
| `.env` | `Read(./.env)`, `Read(./**/.env)`, `Write(./.env)`, `Edit(./.env)` | secret component pattern `.env` on Read/Grep/Glob/LS/Write/Edit paths and on every Bash path argument, input redirect, and glob expansion | `deny_secrets_read_env`, `deny_secrets_bash_cat_env`, `deny_write_env` |
| `.env.*` | `Read(./.env.*)`, `Read(./**/.env.*)`, `Write(./.env.*)`, `Edit(./.env.*)` | pattern `.env.*`, `*.env` | `deny_secrets_read_env_dotted` |
| `secrets/` | `Read(./secrets/**)`, `Write(./secrets/**)`, `Edit(./secrets/**)` | component `secrets` | `deny_secrets_bash_cat_secrets_dir` |
| `credentials/` | `Read(./credentials/**)`, `Write(./credentials/**)`, `Edit(./credentials/**)` | component `credentials`, `*.credentials.json` | `deny_secrets_read_credentials_dir` |
| `*.pem *.key *.p12 *.pfx` | `Read(./**/*.pem)` etc. | patterns `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`, `id_ed25519*` | `deny_secrets_read_pem/_key/_p12/_pfx` |
| `~/.ssh` | `Read(~/.ssh/**)` | `~` expanded, component `.ssh` | `deny_secrets_bash_ssh_dir` |
| `~/.aws` | `Read(~/.aws/**)` | component `.aws` | `deny_secrets_bash_aws` |
| `~/.config/gcloud` | `Read(~/.config/gcloud/**)` | `gcloud` directly under `.config` | `deny_secrets_bash_gcloud` |
| `.claude/settings.local.json` | `Read(./.claude/settings.local.json)`, `Write(...)`, `Edit(...)` | pattern `settings.local.json` | `deny_secrets_read_settings_local`, `deny_write_settings_local` |
| Any path outside the repo | `Read(//etc/**)`, `Read(//tmp/**)`, `Read(//var/**)`, `Read(//root/**)`, `Read(//home/**)` | every path normalized (`..`, `~`), must be inside repo unless under `READ_ONLY_TOOL_PATHS` (`/usr/bin`, `/usr/local/bin`, `/bin`, `/usr/lib/python3*`, `/usr/local/lib/python3*`, `/usr/share/doc`, `/dev/null`); symlink escapes resolved with `realpath` and denied; `cd` outside the repo denied and `cd` inside tracked so relative paths resolve correctly | `deny_outside_repo_read_etc_passwd`, `deny_outside_repo_bash_cp_passwd`, `deny_outside_repo_bash_cat_tmp`, `test_symlink_escape_is_denied_for_read_and_write` |
| Environment dumps (`printenv`, bare `env`/`set`, `export -p`, `$VAR`) | `Bash(printenv:*)`, `Bash(env)` | ENV_DUMP_COMMANDS; any `$` expansion → "cannot verify" | `deny_uncertain_printenv`, `deny_uncertain_variable_expansion` |

### 4.2 Deny deletion and destructive shell

| Command | P (deny) | H | F |
|---|---|---|---|
| `rm` (any form, incl. `r\m`, `'rm'`, `/bin/rm`, `command rm`, `FOO=1 rm`, `bash -c 'rm …'`) | `Bash(rm:*)` | DESTRUCTIVE_COMMANDS after shlex normalization of every segment; `bash/sh -c` strings re-parsed | `deny_destructive_rm_obfuscated`, `deny_destructive_rm_quoted`, `deny_uncertain_bash_c_rm` |
| `rmdir`, `shred`, `mkfs*`, `dd`, `truncate`, `unlink`, `wipefs` | `Bash(rmdir:*)`, `Bash(shred:*)`, `Bash(mkfs:*)`, `Bash(mkfs.*:*)`, `Bash(dd:*)`, `Bash(truncate:*)` | DESTRUCTIVE_COMMANDS | `deny_destructive_rmdir/_shred/_mkfs/_dd` |
| `git clean` | `Bash(git clean:*)` | `_check_git` | `deny_destructive_git_clean` |
| `git reset --hard` | `Bash(git reset --hard:*)` | `--hard`/`--merge` anywhere in args | `deny_destructive_git_reset_hard` |
| `git push --force` / `-f` / `--force-with-lease` / `+ref` / `--delete` | `Bash(git push:*)` | `_check_git` (all `git push` denied; force named explicitly) | `deny_destructive_git_push_force`, `deny_destructive_git_push_f` |
| `git branch -D` (and `-d`, `--delete`) | `Bash(git branch -D:*)`, `Bash(git branch -d:*)` | `_check_git` | `deny_destructive_git_branch_D` |
| `git restore`, `git checkout -- / . / -f`, `git rm`, `git stash drop/clear/pop`, `git filter-branch`, `git worktree remove` | `Bash(git restore:*)`, `Bash(git rm:*)`; `git checkout/stash` in **ask** | `_check_git` | covered by `test_every_segment…` style unit tests |
| Truncation/append redirects `>`, `>>`, `>\|`, `&>`, `N>` and `tee` onto anything outside `output/` | — (not expressible) | `_check_bash_write_target`: target must be inside repo and under `output/` (not `output/.hook_state/`); `/dev/null` allowed; target from `$(…)` denied | `deny_destructive_redirect_claude_md`, `deny_destructive_redirect_append_schema`, `test_redirects_allowed_only_under_output` |
| `chmod -R`, `chown -R`, `chgrp -R` | `Bash(chmod -R:*)`, `Bash(chown -R:*)` | flag scan incl. combined flags (`-Rf`) | `deny_destructive_chmod_R` |
| `find … -delete`, `find … -exec rm` | `Bash(find * -delete:*)` | `_check_find`; `-exec` payload re-checked as a command | `deny_destructive_find_delete`, `deny_destructive_find_exec_rm` |
| `mv` of source documents (any arg under `tests/fixtures/deidentified/` or `docs/source-checklists/`, or any `.pdf`); `cp/ln/install/touch/mkdir/sed -i` **onto** those roots, `.claude/hooks/`, or `.claude/settings*.json` | `Bash(mv tests/fixtures/deidentified:*)`, `Bash(mv docs/source-checklists:*)` | `_check_protected_destination` | `deny_destructive_mv_source_pdf`, `deny_destructive_cp_into_source_docs` |
| `sudo/doas/su`, `eval`, `xargs` without a command, `node/perl/ruby -e` | `Bash(sudo:*)`, `Bash(su:*)`, `Bash(eval:*)` | PRIVILEGE/UNCERTAIN/INLINE_CODE sets | `deny_uncertain_sudo`, `deny_uncertain_eval`, `deny_uncertain_node_e` |

### 4.3 Deny outbound network during the read-only MVP

| Vector | P (deny) | H | F |
|---|---|---|---|
| `curl wget nc ncat netcat ssh scp sftp ftp telnet socat` (+ `gh aws gcloud az docker`, `ping dig nslookup`, PowerShell `Invoke-WebRequest/iwr/irm`) | `Bash(curl:*)`, `Bash(wget:*)`, `Bash(nc:*)`, `Bash(ssh:*)`, `Bash(scp:*)`, `Bash(sftp:*)`, `Bash(ftp:*)`, … | NETWORK_COMMANDS in **every** segment, including inside `$(…)`, backticks, `( … )`, `bash -c`, `find -exec`, `xargs` | `deny_network_curl_subst`, `deny_network_wget_backtick`, `deny_network_curl_after_and`, `deny_network_nc/_ssh/_scp/_sftp/_ftp` |
| `pip install` / `python -m pip` / `npm install` / `npx` / `yarn` / `pnpm` / `apt` / `brew` | `Bash(pip install:*)`, `Bash(pip3 install:*)`, `Bash(python -m pip:*)`, `Bash(npm install:*)`, `Bash(npx:*)`, … | PIP_LIKE (only `list/show/freeze/check` allowed), NETWORK_COMMANDS | `deny_network_pip_install`, `deny_network_python_m_pip`, `deny_network_npm_install` |
| `python -m http.server` (and `-m urllib/smtplib/ftplib/socket/…`); `python -m` anything not `pytest`, `scripts.*`, `json.tool`, `unittest` | `Bash(python -m http.server:*)` | `_check_python` module allowlist | `deny_network_http_server` |
| Inline `python -c` / heredoc scripts using `urllib requests socket httpx aiohttp ftplib smtplib boto3 paramiko …` or `shutil.rmtree os.remove subprocess os.system …` or `os.environ` | — | INLINE_NETWORK_RE / INLINE_DESTRUCTIVE_RE / INLINE_SECRET_RE on `-c` strings and heredoc bodies | `deny_network_python_c_urllib`, `deny_network_python_c_requests`, `deny_network_python_heredoc_requests` |
| `git push/fetch/pull/clone/ls-remote/submodule` | `Bash(git push:*)`, `Bash(git fetch:*)`, `Bash(git pull:*)`, `Bash(git clone:*)` | `_check_git` | `deny_network_git_push` |
| `WebFetch`, `WebSearch` | `WebFetch`, `WebSearch` | denied by tool name | `deny_network_webfetch`, `deny_network_websearch` |
| Any `mcp__*` tool not on the (empty) allowlist | server-level denies for every connected/known server (`mcp__github`, `mcp__Gmail`, `mcp__Slack`, `mcp__modelmatch`, `mcp__Google_Drive`, `mcp__Google_Calendar`, `mcp__Higgsfield`, …) | `_mcp_decision` | `deny_mcp_readonly_not_allowlisted` |
| Process substitution `<( … )`, `>( … )` | — | parse → "cannot verify" | `deny_uncertain_process_substitution` |

### 4.4 Deny all LOS / email / SMS / AUS / TRID / pricing / submission writes

| Rule | P (deny) | H | F |
|---|---|---|---|
| Any `mcp__` tool whose name contains `send create update write submit push post delete run_aus lock price` (also `upload trash forward reply merge publish schedule share grant remove`) — denied even if a server is later allowlisted | explicit future tools: `mcp__arive__{update_loan,create_loan,upload_document,submit_loan,run_aus,lock_rate,price_loan,send_disclosures}`, the same for `mcp__lendingpad__*`, `mcp__clickup__{create_task,update_task,delete_task,post_comment}`, `mcp__gmail__{send_message,create_draft,reply,forward}` (+ `mcp__Gmail__…`), `mcp__slack__send_message`, `mcp__Slack__slack_send_message`, `mcp__Slack__slack_schedule_message`; server-level `mcp__arive`, `mcp__lendingpad`, `mcp__clickup`, `mcp__gmail`, `mcp__slack`, `mcp__twilio`, `mcp__sms`, `mcp__aus`, `mcp__trid`, `mcp__pricing` | MCP_WRITE_VERBS substring check, then allowlist check | `deny_mcp_arive_update_loan`, `deny_mcp_lendingpad_submit_loan`, `deny_mcp_arive_run_aus`, `deny_mcp_pricing_lock`, `deny_mcp_gmail_send_message`, `deny_mcp_slack_send_message`, `deny_mcp_clickup_create_task` |

Approval-gated forever (CLAUDE.md rule 8): adding any of these to the
allowlist is a change-control event (playbook §13-F) and requires the
integration step's "first permission" in `.claude/rules/integrations.md`.

### 4.5 Writes: allowed roots and protected files

| Rule | P | H | F |
|---|---|---|---|
| Write/Edit/MultiEdit/NotebookEdit only under `output/ tests/ scripts/ docs/ config/ schemas/ .claude/` | (allow-by-omission; deny list below) | `ALLOWED_WRITE_ROOTS` top-level check after normalization | `deny_write_outside_roots_claude_md`, `deny_write_outside_repo_tmp`, `deny_write_parent_escape`, `allow_write_*` |
| Never `tests/fixtures/deidentified/**/*.pdf` or `docs/source-checklists/*.pdf` (also png/jpg/tiff/xlsx/docx there) | `Write(./tests/fixtures/deidentified/**/*.pdf)`, `Write(./docs/source-checklists/*.pdf)`, same for `Edit` | `SOURCE_DOCUMENT_ROOTS` × `SOURCE_DOCUMENT_EXTENSIONS` | `deny_write_pdf_deidentified`, `deny_write_pdf_source_checklists`, `deny_edit_pdf_deidentified` |
| Never `.claude/settings.json` / `.claude/settings.local.json` (Claude must not edit its own enforcement); `.claude/hooks/**` and `.claude/rules/**` require **ask** | `Write/Edit(./.claude/settings.json)`, `Write/Edit(./.claude/settings.local.json)`; ask: `Write/Edit(./.claude/hooks/**)`, `Write/Edit(./.claude/rules/**)` | `ENFORCEMENT_FILES`; shell writes onto `.claude/hooks/` denied | `deny_write_settings_json`, `deny_write_settings_local` |
| Never `output/.hook_state/**` (hook-owned) | `Write(./output/.hook_state/**)` | explicit | `test_redirects_allowed_only_under_output` |
| No unmasked SSN (`###-##-####`) in anything written under `output/` (CLAUDE.md rule 7) | — | `UNMASKED_SSN_RE` on `content` / `new_string` / `edits[].new_string` | `deny_write_unmasked_ssn_output` |

### 4.6 Workflow gates

| Requirement | Mechanism | F |
|---|---|---|
| Schema validation before a workflow reports completion (CLAUDE.md rule 10) | PostToolUse `post_write_validate.py`: any JSON written under `output/audits/` whose basename maps — `loan_file.json → loan_file`, `document_inventory.json → document_inventory`, `preapproval_audit.json` / `submission_readiness.json → audit_result` — is validated by running `python scripts/validate_schema.py <file> --schema <name>` in a subprocess; exit 1 → block with errors; exit 2 or any other failure → block ("validator errored"). Other `*.json` there must parse as an object. | `test_hooks_post_write_validate.py` (pass, fail, every mapped name, validator error, e2e) |
| Audit manifest per run | Stop `stop_gate.py`: every run dir touched this session must contain `run_manifest.json` (a JSON object) and every JSON in it must validate as above. Scope = run dirs recorded in `output/.hook_state/<session_id>.json` by the PreToolUse guard ∪ run dirs whose files changed after the session's recorded start. Sessions with no state file are never blocked. | `test_hooks_stop_gate.py` (block on missing manifest, block on invalid JSON, allow when fixed, scope isolation, mtime discovery, e2e) |
| Fail closed | `run_fail_closed` in every script; validator exit ≠ 0/1 → block; malformed stdin → exit 2; wrong payload shapes → exit 2 | `test_hooks_common.py`, `failclosed_*` fixtures, `malformed_stdin.json` |
| Loop guard (reviewer decision) | After `MAX_CONSECUTIVE_BLOCKS = 3` consecutive Stop blocks in one session the gate lets the session stop and emits a `systemMessage` stating the run is NOT complete. Set to `None` for strict mode. | `test_loop_guard_releases_with_system_message` |

## 5. Threat model

| # | Threat | Control | How tested |
|---|---|---|---|
| T1 | Claude reads a credential file (`.env`, `secrets/`, key material, cloud CLI stores) and it lands in a transcript, log, or output | §4.1 permission denies + hook secret patterns on every path-bearing tool input, Bash argument, glob expansion, and input redirect; `~` and `..` resolved; symlinks resolved | 13 `deny_secrets_*` fixtures, symlink and glob unit tests, e2e exit-2 checks |
| T2 | Claude reads live borrower PII outside the de-identified tree | Any path outside the repo denied; inside the repo only `tests/fixtures/deidentified/` is an approved input (`config/approved_data_locations.yaml`, enforced by intake scripts); `.gitignore` refuses PDFs elsewhere | `deny_outside_repo_*`; intake tests (other work-stream) |
| T3 | Destructive shell wipes work or fixtures (`rm -rf`, `git clean`, `reset --hard`, `find -delete`, `dd`, `mkfs`) | §4.2 command classification after normalization of quoting/escaping/wrappers; every chained segment checked | 18 `deny_destructive_*` fixtures + obfuscation unit test |
| T4 | Source document altered or moved (CLAUDE.md rule 9) | Write/Edit denied on document extensions under source roots; `mv` of source paths/PDFs denied; `cp/ln/touch/sed -i` onto source roots denied; shell redirects only under `output/` | `deny_write_pdf_*`, `deny_edit_pdf_*`, `deny_destructive_mv_source_pdf`, `deny_destructive_cp_into_source_docs` |
| T5 | Tracked file truncated by redirect (`echo x > CLAUDE.md`) | Redirect/tee targets restricted to `output/` | `deny_destructive_redirect_*`, `test_redirects_allowed_only_under_output` |
| T6 | Data exfiltration or untrusted code download over the network | Network command set, installer set, inline-python module scan, `python -m` allowlist, `git push/fetch/pull/clone`, WebFetch/WebSearch, all MCP | 18 `deny_network_*` fixtures |
| T7 | External write (LOS update, email send, AUS run, rate lock, submission) | Every `mcp__*` denied; write-verb names denied regardless of allowlist; explicit future tool names in permissions deny | 8 `deny_mcp_*` fixtures, `test_mcp_allowlist_is_empty_and_write_verbs_always_denied` |
| T8 | Command obfuscation bypasses pattern rules (`r\m`, `'rm'`, `$(curl)`, backticks, `bash -c`, `$VAR`, `<(…)`, heredoc smuggling) | Full tokenizer with quote tracking; substitutions flattened and checked; variable expansion / process substitution / arithmetic / unbalanced quotes → deny; heredoc bodies stripped from command parsing but scanned for network/destructive code | `test_parse_uncertainty_is_denied`, `test_split_commands_flattens…`, `test_every_segment_is_checked…`, `test_obfuscated_command_names…`, `test_heredoc_body…` |
| T9 | Claude edits its own enforcement (settings, hooks) | `settings.json`/`settings.local.json` write-denied in both layers; hooks/rules writes require **ask**; shell writes onto `.claude/hooks/` denied | `deny_write_settings_json`, `deny_write_settings_local`, redirect test |
| T10 | Invalid or unvalidated JSON reported as a finished audit | PostToolUse validation; Stop gate requires manifest + valid JSON; validator errors block | post-write and stop-gate suites |
| T11 | A hook bug silently allows everything | `run_fail_closed`; malformed stdin/payload → exit 2; validator crash → exit 2 | `test_run_fail_closed_*`, `test_every_hook_script_is_runnable_and_fails_closed…`, `failclosed_*` fixtures |
| T12 | Unmasked SSN written to derived output | Content scan on writes under `output/` | `deny_write_unmasked_ssn_output`, `test_write_content_checks_cover_edit_and_multiedit` |
| T13 | Stop gate blocks unrelated sessions or loops forever | Scope limited to session state + mtime; loop guard with visible warning | `test_scope_is_limited…`, `test_no_session_or_no_state_never_blocks`, `test_loop_guard…` |

## 6. Residual risks (accepted for MVP 1, revisit before Phase 2)

1. **Code written then executed.** The hook inspects tool calls, not the
   content of `.py`/`.sh` files Claude writes under `scripts/` and then runs.
   Mitigation: no credentials in the environment, sandboxed runner with
   network disabled, human review of diffs before commit. Consider Claude
   Code's sandbox settings (see §10).
2. **Grep/Glob output.** Read-denies are expected to apply to Grep/Glob/LS
   (see §10); the hook additionally checks their `path` input but cannot see
   which files a repo-wide pattern would print.
3. **Variable expansion is denied outright** (`$HOME`, `"$f"` in loops).
   This is intentional fail-closed behaviour and will produce some false
   denials; the reason text tells Claude to use literal paths.
4. **Permission `Bash(...)` rules are prefix matches**; they are listed for
   defense in depth only.
5. **`run_manifest.json` has no schema yet.** The gate checks presence and
   JSON-object shape. Add a schema when `scripts/audit` stabilizes.

## 7. Activation checklist

Do these in order; record results in `docs/hook-test-log.md`.

1. Read this plan and `.claude/settings.proposed.json` side by side. Confirm
   every matcher string, every command (`python .claude/hooks/<name>.py`),
   and the failure behaviour (exit 2 = block) match §3 and the official docs
   (§10).
2. Run the hook tests: `python -m pytest tests/unit/test_hooks_*.py -q`.
   All must pass. Record the count and date.
3. Dry-run each deny rule with its harmless fixture, exactly as Claude Code
   would invoke it:
   `python .claude/hooks/pre_tool_guard.py < tests/fixtures/hook_inputs/<fixture>.json; echo exit=$?`
   Expect `exit=2` and a readable reason on stderr. Do the same for at least
   five `allow_*` fixtures (expect `exit=0`).
4. Dry-run the workflow gates: copy
   `tests/fixtures/hook_inputs/doc_document_inventory_invalid.json` to a
   scratch run dir under `output/audits/ACTIVATION-TEST/RUN-1/document_inventory.json`
   and feed a PostToolUse payload; expect exit 2 with the validator's errors.
   Delete the scratch dir afterwards.
5. Fill in the "observed" column and pass/fail for every row in
   `docs/hook-test-log.md`; sign and date it.
6. Rename `.claude/settings.proposed.json` → `.claude/settings.json`
   (a human does this by hand; the hook denies Claude writing that file).
7. Start a new Claude Code session, run `/hooks` to confirm the three hooks
   are registered, then run one deny probe live (e.g. ask Claude to
   `cat .env`) and one allow probe (`git status`). Record both.
8. Only then run the first `/mortgage-file-audit` on a de-identified fixture.

## 8. Rollback

- Immediate: rename `.claude/settings.json` → `.claude/settings.proposed.json`
  (or delete it). Hooks and permission rules are inert without that file.
  Restart the Claude Code session; hook configuration is captured at
  startup.
- Partial: remove a single entry from `hooks.PreToolUse[...]` /
  `PostToolUse` / `Stop`, or comment it out by renaming its `matcher`, and
  restart. Never edit the hook scripts as a rollback measure — tests would
  no longer describe reality.
- Stop-gate stuck: delete `output/.hook_state/<session_id>.json` (state is
  disposable) and stop the session. The loop guard also releases after 3
  blocks with a warning.
- Log every rollback in `docs/hook-test-log.md` with the reason and the
  commit hash of the settings that were active.

## 9. Change control

Any change to `MCP_ALLOWLIST`, `ALLOWED_WRITE_ROOTS`,
`READ_ONLY_TOOL_PATHS`, `SECRET_COMPONENT_PATTERNS`, the matchers, or
`MAX_CONSECUTIVE_BLOCKS` requires: a fixture proving the new behaviour, a
green `test_hooks_*` run, an updated row in `docs/hook-test-log.md`, and a
named approver (playbook §13-F). Integration steps follow the roadmap order
in `.claude/rules/integrations.md`; each step's first permission is the
*only* thing that opens.

## 10. Verify against docs before activation

This plan was written without network access from a description of the
Claude Code hook and permission contract. Before activation, confirm each
point against https://code.claude.com/docs/en/hooks and the permissions
documentation and note any discrepancy in `docs/hook-test-log.md`:

1. **Exit-code semantics**: exit 2 blocks and feeds stderr to Claude for
   PreToolUse and Stop; for PostToolUse the tool has already run and stderr
   is shown to Claude. Any other non-zero is a non-blocking error.
2. **Stop hook JSON**: `{"decision": "block", "reason": "..."}` on stdout
   with exit 0 blocks completion; `systemMessage` is shown to the user;
   `stop_hook_active: true` is present when continuing after a Stop block.
3. **Matcher syntax**: regex alternation (`Bash|Read|...`) and `mcp__.*`
   are valid matchers; the empty matcher on Stop matches everything; tool
   names are case-sensitive (`MultiEdit`, `NotebookEdit`, `LS`).
4. **Payload keys**: `session_id`, `hook_event_name`, `tool_name`,
   `tool_input` (`command` for Bash; `file_path`/`content`/`old_string`/
   `new_string`/`edits` for Write/Edit/MultiEdit; `notebook_path` for
   NotebookEdit; `path` for Grep/Glob/LS); `tool_response` on PostToolUse.
   File paths are absolute in real payloads (the hooks also accept
   relative ones anchored at the repo root).
5. **Working directory and `$CLAUDE_PROJECT_DIR`**: hooks run from the
   project root so `python .claude/hooks/...` resolves; if the docs
   recommend `"$CLAUDE_PROJECT_DIR"/.claude/hooks/...`, switch the
   commands to that form.
6. **Hook `timeout` field** (seconds) and default timeout.
7. **Permission path syntax**: `./` project-relative, `~/` home, `//`
   absolute; `**` glob support in `Read(...)`/`Write(...)`/`Edit(...)`;
   whether Read rules also govern Grep/Glob/LS.
8. **MCP permission syntax**: `mcp__server` (whole server) and
   `mcp__server__tool`; whether wildcards inside tool names are supported
   (this plan lists explicit names and relies on the hook for `mcp__.*`).
9. **Deny precedence** over allow and ask; the `ask` list semantics.
10. **Hook registration timing**: hooks are snapshotted at session start,
    so activation and rollback both require a restart.
11. **Sandbox / network isolation settings** for Bash, which would close
    residual risk §6.1 for code Claude writes and then runs.
