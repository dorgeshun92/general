# .claude/skills/ — routing guide

One skill per mortgage procedure (MVP 1, read-only). Claude routes on each skill's frontmatter
`description`; the descriptions are written to be non-overlapping. The orchestrator is
`disable-model-invocation: true` and runs only as `/mortgage-file-audit <directory>`.

## Routing table

| User intent | Skill | Does NOT run for |
|---|---|---|
| Inventory / hash / classify / dedupe / extract facts from one fixture directory; "what documents are in this file" | `loan-file-intake` | checklist evaluation, PASS/FAIL, readiness status, drafts, end-to-end run, any directory outside `tests/fixtures/deidentified/` |
| Evaluate PREAPPROVAL checklist items against an existing `loan_file.json`; findings on credit, liabilities, REO, employment, income, assets, gifts, retirement, title, property; missing-document analysis | `preapproval-audit` | intake/classification, the READY/NOT_READY gate, borrower emails, end-to-end run, files that have not been intaken yet |
| "Is this file ready to submit?"; submission gate; coverage percentage; proposed LOS corrections / client needs / Arive notes | `submission-readiness` | intake, preapproval findings, drafts, end-to-end run; anything that writes to Arive/LendingPad, runs AUS, triggers TRID, prices, picks a lender, contacts anyone, or submits |
| "Draft the request email / call agenda / LOS note / agent follow-up for findings F-001 and F-004" with named approved ids | `borrower-communication-drafts` | choosing which findings matter, running an audit, sending/scheduling/replying to anything, LOS writes, requests with no approved finding ids (ask) |
| `/mortgage-file-audit tests/fixtures/deidentified/<loan-id>` — the complete read-only pilot run | `mortgage-file-audit` | automatic routing (explicit invocation only), single stages, drafts, non-fixture directories |
| Anything else (guideline questions, pricing, sending, LOS updates, live files, general chat) | none — answer or ask | — |

## Shared safety envelope

All five skills: read-only inputs, no network, no MCP/connector tools, writes only under
`output/`, schema validation before completion, run manifest with sha256 for every input and
output, PII masking in everything printed. See `CLAUDE.md` for the non-negotiable rules.

## Routing test matrix

Expected routing for representative requests. "none (ask)" means no skill should fire; Claude
answers directly or asks a clarifying question. Use this matrix when reviewing description
changes (playbook prompt 13.D).

| # | User request | Expected skill |
|---|---|---|
| 1 | "Inventory the documents in tests/fixtures/deidentified/LN-TEST-0001" | loan-file-intake |
| 2 | "Classify the PDFs in the LN-TEST-0002 fixture and tell me which are duplicates" | loan-file-intake |
| 3 | "Extract the borrower names, statement periods and masked account numbers from LN-TEST-0003" | loan-file-intake |
| 4 | "Which files in LN-TEST-0001 are unreadable or password-protected?" | loan-file-intake |
| 5 | "Hash every file in the LN-TEST-0004 package and build loan_file.json" | loan-file-intake |
| 6 | "Run the preapproval checklist on LN-TEST-0001 run RUN-20260908-120000" | preapproval-audit |
| 7 | "Which pre-approval items fail for LN-TEST-0002?" | preapproval-audit |
| 8 | "Audit the income and asset documentation in the LN-TEST-0003 loan file" | preapproval-audit |
| 9 | "What documents are missing for the LN-TEST-0001 preapproval?" | preapproval-audit |
| 10 | "Compare the liabilities on the credit report to the 1003 for LN-TEST-0005 and flag conflicts" | preapproval-audit |
| 11 | "Is LN-TEST-0001 ready to submit?" | submission-readiness |
| 12 | "Give me the submission readiness gate and coverage percentage for LN-TEST-0002 RUN-1" | submission-readiness |
| 13 | "What LOS corrections would you propose for LN-TEST-0003 based on the audit?" | submission-readiness |
| 14 | "Reconcile the purchase contract against the 1003 and tell me if we can submit" | submission-readiness |
| 15 | "Draft the document request email for findings F-001 and F-004 in output/audits/LN-TEST-0001/RUN-1/preapproval_audit.json; I (Jane, LO) approve them today" | borrower-communication-drafts |
| 16 | "Write a call agenda and post-call Arive note for approved findings F-002, F-003" | borrower-communication-drafts |
| 17 | "Prepare an agent/title follow-up draft for F-007 (approved by Sam, processor, 2026-09-08)" | borrower-communication-drafts |
| 18 | "/mortgage-file-audit tests/fixtures/deidentified/LN-TEST-0001" | mortgage-file-audit |
| 19 | "Run the complete read-only audit on LN-TEST-0002" (without the slash command) | none (ask) — tell the user to invoke `/mortgage-file-audit <dir>` explicitly |
| 20 | "Email the borrower about whatever is missing on LN-TEST-0001" | none (ask) — no approved finding ids; list candidates and ask for approval |
| 21 | "Send the request email we drafted yesterday" | none (ask) — sending is outside the read-only MVP; a human sends |
| 22 | "Update the loan amount in Arive to match the contract" | none (ask) — LOS writes are prohibited; offer a proposed_action via submission-readiness only |
| 23 | "Run AUS on LN-TEST-0003" / "Issue the initial disclosures" | none (ask) — AUS/TRID are prohibited in every release without human execution |
| 24 | "What is the maximum DTI for a conventional loan?" | none (ask) — guideline question with no approved source registered |
| 25 | "Audit the loan package in C:\Users\me\Downloads\smith-loan" | none (ask) — outside the approved fixture root; refuse and explain |
| 26 | "Intake this file: /var/loans/live/12345" | none (ask) — not de-identified fixture data; refuse |
| 27 | "Which lender should we send this file to?" / "Price this loan" | none (ask) — lender selection and pricing are approval-gated and out of scope |
| 28 | "Calculate the borrower's self-employed income from the tax returns" | none (ask) — not supported in v1; explain it is classified and routed to REVIEW by preapproval-audit |
| 29 | "Validate output/audits/LN-TEST-0001/RUN-1/loan_file.json against the schema" | none — run `python scripts/validate_schema.py … --schema loan_file` directly |
| 30 | "Re-check the polished email draft for banned phrases" | borrower-communication-drafts (its `--check` step) |
| 31 | "Summarize what the last audit found for LN-TEST-0001" (outputs already exist) | none — read `submission_report.md` / `preapproval_report.md` and summarize; no skill needed |
| 32 | "Reclassify DOC-004 as a bank statement" | none (ask) — reclassification is a human action; point to `classification_review.json` from loan-file-intake |

Overlap checks: requests 6–10 must not trigger `submission-readiness` (they ask about
findings, not the gate); 11–14 must not trigger `preapproval-audit`; 15–17 require named ids
and an approver, otherwise they become case 20; 18 is the only route into the orchestrator.
