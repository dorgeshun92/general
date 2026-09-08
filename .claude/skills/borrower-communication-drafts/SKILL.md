---
name: borrower-communication-drafts
description: Turn EXPLICITLY APPROVED finding ids from an existing audit_result.json into four DRAFT files (document-request email, borrower call agenda, post-call LOS note, agent/title follow-up) under output/drafts/<loan_id>/<run_id>/ using scripts/drafts/build_drafts.py. Use only when a human names the finding ids they approved (e.g. "draft the request email for F-001 and F-004"). Do NOT use to decide which findings matter, to run an audit, to send, schedule, or reply to any email/SMS/call, to write to the LOS, or when no audit_result.json exists or no finding ids were approved — ask instead.
argument-hint: <audit_result.json> --approved F-001,F-004 --approver "<name>" --approved-on YYYY-MM-DD   |   <audit_result.json> --approved-file <file>
allowed-tools: Read, Glob, Grep, Bash(python scripts/drafts/build_drafts.py*), Bash(python scripts/validate_schema.py*), Bash(python scripts/audit/run_manifest.py*), Write(output/drafts/**), Edit(output/drafts/**)
---

# borrower-communication-drafts

Drafts only. The deterministic builder turns approved findings into plain-language drafts;
you may polish wording, but every safety rule is re-checked by the script before you finish.
Nothing here sends, schedules, or records anything anywhere.

Governing rules: `CLAUDE.md` rules 7 and 8; `config/roles.yaml` (LOAN_OFFICER approves
borrower communication drafts). Builder and checker: `scripts/drafts/build_drafts.py`
(docstring lists exit codes and the approved-file format).

## Inputs — approved finding ids only

`$ARGUMENTS` must contain a path to a validated `audit_result.json` (usually
`output/audits/<loan_id>/<run_id>/preapproval_audit.json` or `submission_readiness.json`) and
the approval, in one of two forms:

- `--approved F-001,F-004 --approver "<name, role>" --approved-on <YYYY-MM-DD>`
- `--approved-file <path>` whose first lines are `approver: <name>` and `date: <YYYY-MM-DD>`
  followed by one finding id per line.

The approval must come from the human in this conversation, in their own words. If the
request does not name finding ids ("email the borrower about whatever is missing"), do not
guess: list the FAIL/MISSING/REVIEW findings from the audit with their explanations and ask
which ones are approved and by whom. Never approve on the user's behalf.

## Procedure

1. **Validate the audit.** `python scripts/validate_schema.py <audit_result.json> --schema audit_result`.
   Non-zero → STOP; drafts are built only from validated audits.

2. **Confirm the ids.** Read the audit; for each approved id state its `rule_id`, `result`,
   and `proposed_action` back to the user in one line each. Ids that are PASS or
   NOT_APPLICABLE have nothing to request — the builder will refuse them; say so rather than
   substituting a different finding.

3. **Build.**
   ```
   python scripts/drafts/build_drafts.py <audit_result.json> --approved F-001,F-004 \
     --approver "<name>" --approved-on <date> --out output/drafts
   ```
   Exit `1` = refused (unknown id, non-requestable result, missing approver/date header):
   report the message verbatim and stop. Exit `2` = safety failure (audit invalid, banned
   phrase in generated text, unmasked PII): report and stop; nothing was written. Exit `0`
   writes, under `output/drafts/<loan_id>/<run_id>/`:
   `document_request_email.md`, `borrower_call_agenda.md`, `post_call_los_note.md`,
   `agent_title_follow_up.md` (the last is a one-line "not applicable" notice unless an
   approved finding's proposed action involves the agent, title, contract, or escrow).

4. **Polish (optional, bounded).** You may edit the generated files for tone, ordering, and
   plain language. You must keep:
   - the first line exactly `DRAFT — HUMAN APPROVAL REQUIRED`;
   - the `<!-- Internal, remove before sending … -->` block (it records the approval basis);
   - the "why we need it" sentence for every item, derived from the finding's explanation;
   - grouping by category; the closing disclaimer sentence in the email.
   You must not add: any finding id, rule id, confidence, "blocking", reviewer role, or other
   internal scoring to borrower/agent-facing text; any borrower fact not in the audit; any
   statement about the outcome, timing, or likelihood of a loan decision; the phrases
   `approved`, `guaranteed`, `will close`, `pre-approved` (case-insensitive) in the email, call
   agenda, or agent/title follow-up; any unmasked SSN or account number anywhere.

5. **Re-run the checker after any edit** (also run it if you made no edits, to prove it):
   ```
   python scripts/drafts/build_drafts.py --check output/drafts/<loan_id>/<run_id>/document_request_email.md \
     --check output/drafts/<loan_id>/<run_id>/borrower_call_agenda.md \
     --check output/drafts/<loan_id>/<run_id>/post_call_los_note.md \
     --check output/drafts/<loan_id>/<run_id>/agent_title_follow_up.md
   ```
   Exit 1 → fix the flagged file and re-check; do not report completion with a failing check.

6. **Manifest.**
   ```
   python scripts/audit/run_manifest.py write --run-dir output/drafts/<loan_id>/<run_id> \
     --skill borrower-communication-drafts --run-id <run_id> --loan-id <loan_id> \
     --input <audit_result.json> [--input <approved-file>] \
     --output output/drafts/<loan_id>/<run_id>/document_request_email.md \
     --output output/drafts/<loan_id>/<run_id>/borrower_call_agenda.md \
     --output output/drafts/<loan_id>/<run_id>/post_call_los_note.md \
     --output output/drafts/<loan_id>/<run_id>/agent_title_follow_up.md
   ```

## Never

Never send an email or SMS, create a calendar event, place or log a call, post to Slack,
write to Arive/LendingPad, or use any MCP/connector tool. If asked to "send it", answer that
sending is outside this release and requires a human to copy the approved draft into their
own mail client after review.

## Completion report (exact format)

```
borrower-communication-drafts — <loan_id> / <run_id>
Basis: findings <ids> approved by <approver> on <date> (results: <id result>, …)
Drafts written (all begin "DRAFT — HUMAN APPROVAL REQUIRED"; checker exit 0):
  output/drafts/<loan_id>/<run_id>/document_request_email.md
  output/drafts/<loan_id>/<run_id>/borrower_call_agenda.md
  output/drafts/<loan_id>/<run_id>/post_call_los_note.md
  output/drafts/<loan_id>/<run_id>/agent_title_follow_up.md  (<applicable | not applicable>)
Edits made after generation: <none | list>
Refused ids: <list with reason, or none>
Nothing has been sent, scheduled, or entered anywhere. A LOAN_OFFICER must review and approve each file before use.
```
