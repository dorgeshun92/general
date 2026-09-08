---
paths:
  - "scripts/integrations/**"
  - ".claude/settings*.json"
  - ".claude/hooks/**"
  - "docs/security-plan.md"
---
# Integrations (supplements CLAUDE.md rule 8; playbook §12; docs/security-plan.md §4.4)

## Deny by default
- Every external system is denied until its roadmap step opens: the MCP allowlist
  in `pre_tool_guard.py` is empty; WebFetch, WebSearch, network shell, all `mcp__*` blocked.
- Any tool whose name contains send / create / update / write / submit / push /
  post / delete / run_aus / lock / price stays denied in every release unless an
  authorized human approves that specific action for that specific loan.
- Opening a permission is change control (playbook §13-F): fixture + green
  `tests/unit/test_hooks_*.py` + row in `docs/hook-test-log.md` + named approver.
  Never edit `.claude/settings.json` (the hook denies it); propose changes in
  `.claude/settings.proposed.json` and `docs/security-plan.md` instead.

## Roadmap order and the first permission each step opens (nothing more)
| Step | Integration | First permission |
|---|---|---|
| 1 | Read-only LOS export | Read a manually exported JSON/CSV/PDF copy placed under an approved input root |
| 2 | Approved guideline repository | Search/read versioned internal references registered in `config/approved_sources.yaml` |
| 3 | ClickUp | Create a draft task payload under `output/drafts/`; a human approves creation |
| 4 | Arive/LendingPad | Read-only fields and document metadata (allowlist one named read tool) |
| 5 | Arive/LendingPad writes | Update only approved fields, with a before/after audit log and human approval |
| 6 | Email/SMS | Create drafts only; sending happens later and only after explicit approval |
| 7 | AUS/TRID/submission | Prepare the action packet; an authorized person executes or explicitly approves |

Sequential: step N+1 waits for step N to pass the evaluation suite (playbook §10).

## Design rules for any integration
- Prefer an official API; browser automation only after the read-only workflow
  is stable, designed for screen changes, timeouts, duplicates, and rollback.
- Log every external read in `run_manifest.json` (source, timestamp, hash).
- A proposed external write is a payload under `output/drafts/` plus an approval
  packet (playbook §13-E); the system never holds the credential to execute it.
