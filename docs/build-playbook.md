# Claude Code Mortgage Workflow System — Implementation Playbook (v1.0)

Source: `Claude_Code_Mortgage_Workflow_Build_Playbook.docx`, owner James Gomez, Mpire Financial.
Converted to Markdown for repository traceability. The .docx is authoritative.

IMPLEMENTATION PLAYBOOK

Claude Code Mortgage Workflow System

A step-by-step build outline and prompt pack for preapproval, loan-file auditing, and submission readiness

| Recommended starting point: Build a read-only loan auditor first. It should inspect a copied, de-identified test file and produce evidence-backed findings. Do not connect it to live LOS write actions until it passes formal accuracy testing. |
|---|

Prepared from:

Mako Mortgage Processing Pre-approval Checklist

Mako Mortgage Processing Loan Partner Checklist for Submission to Processing

Submission to Processing Checklist

Version 1.0  |  Owner: James Gomez  |  Business: Mpire Financial

## 1. The decision: what to build first

Build a mortgage operations copilot, not an autonomous underwriter. The first release should read documents, reconcile facts, calculate supported values, identify missing evidence, and prepare proposed corrections. It should never represent that a condition passed unless it can cite the supporting file, page, and extracted value.

| Release | Capabilities | Write authority | Go-live rule |
|---|---|---|---|
| MVP 1: Read-only auditor | Classify documents; audit 1003, income, assets, credit, property, contract; produce reports | None | Pass retrospective test set |
| MVP 2: Drafting assistant | Create proposed LOS notes, client needs, borrower emails, task lists | Drafts only | Human accepts/rejects every action |
| MVP 3: Controlled LOS assistant | Write approved fields and notes through an API or browser workflow | Approved actions only | Audit log + rollback + permissions |
| MVP 4: Submission coordinator | Run final gate, assemble package, prepare AUS/submission actions | Explicit authorized approval | Compliance and operational sign-off |

| Hard boundary: Pricing, lender choice, guideline exceptions, adverse-action implications, AUS submission, TRID triggering, and final submission must remain approval-gated. |
|---|

## 2. What “intuitive” should mean

Claude should select the right skill from the user’s intent, but deterministic gates—not intuition—must control sensitive actions. Use explicit skill descriptions for routing, structured JSON for handoffs, scripts for calculations, and hooks/permissions for enforcement.

| Claude Code feature | Use in this project |
|---|---|
| CLAUDE.md | Short, always-on operating rules, architecture, privacy rules, and non-negotiable approval gates. |
| Skills | Reusable mortgage procedures such as intake, income review, contract review, and submission readiness. |
| Supporting scripts | Deterministic extraction, arithmetic, schema validation, comparison, hashing, and report generation. |
| Subagents | Isolated, read-only reviewers for document-heavy analysis; optional after the MVP works. |
| Hooks | Block prohibited tools or writes and automatically validate outputs before completion. |
| MCP | Connect Arive/LendingPad, ClickUp, approved guideline sources, and other systems through narrowly scoped tools. |

## 3. Step-by-step: create the project

### Step 1 — Prepare a safe environment

Use a company-controlled computer and a private Git repository. Do not begin with a public repository.

Create a separate test workspace that contains no live borrower PII.

Create 10–20 de-identified historical loan files representing straightforward and difficult cases.

Create an answer key for each test file: verified income, assets, liabilities, missing documents, material discrepancies, and final readiness outcome.

Decide who can approve mortgage-rule conclusions and who can approve system writes.

| Data rule: Never commit borrower files, credit reports, tax returns, bank statements, tokens, passwords, or API keys to Git. |
|---|

### Step 2 — Install and verify Claude Code on Windows

Open PowerShell and run:

PowerShell commands

```
irm https://claude.ai/install.ps1 | iex
claude --version
claude doctor
mkdir C:\MpireAI\mortgage-ops
cd C:\MpireAI\mortgage-ops
git init
claude
```

When Claude opens, authenticate through the browser. If you prefer sandboxed Linux execution, use WSL 2 and install Claude Code inside WSL instead.

### Step 3 — Create the repository skeleton

Prompt to give Claude Code

```
Create the initial repository structure for a mortgage operations copilot. Do not implement business logic yet.

Create:
.claude/skills/
.claude/rules/
.claude/hooks/
agents/
config/
docs/source-checklists/
docs/guidelines/
schemas/
scripts/
tests/fixtures/deidentified/
tests/expected/
output/audits/
output/drafts/

Also create .gitignore that excludes borrower-data/, secrets, .env files, raw outputs, temporary OCR files, logs containing PII, and common OS/editor files. Add placeholder README files where Git would otherwise ignore an empty folder. Show me the proposed tree before writing, then create it after I approve.
```

### Step 4 — Add the three source checklists

Copy the three supplied PDFs into docs/source-checklists/. Work only with copies.

Create a text extraction for traceability, but retain each original PDF as the authoritative source.

Give each checklist item a stable ID such as PRE-CREDIT-001 or SUB-PROPERTY-004.

Never merge similar rules silently. Preserve the source wording and record consolidation decisions separately.

Checklist-normalization prompt

```
Read every PDF in docs/source-checklists/. Convert every operative checklist statement into config/checklist_catalog.yaml.

For each item include:
- id
- source_document
- source_page
- source_text
- phase
- category
- applies_when
- required_evidence
- fields_to_compare
- evaluation_method: deterministic | guideline_lookup | human_judgment
- result_values: PASS | FAIL | MISSING | REVIEW | NOT_APPLICABLE
- blocking_if_failed
- proposed_action
- required_human_role

Rules:
1. Do not invent mortgage guidelines.
2. Preserve ambiguous language and mark it REVIEW.
3. Do not treat absence of evidence as NOT_APPLICABLE.
4. If two documents conflict, retain both and create a conflict record.
5. Output a coverage report showing each source item and its catalog ID.

Before editing files, show me 10 representative normalized items and ask for approval.
```

## 4. Define the canonical loan-file data model

Every skill should exchange one canonical JSON object. This prevents one skill from “remembering” facts differently from another.

| Object | Minimum contents |
|---|---|
| loan | loan_id, purpose, occupancy, product family, lender, closing date, status |
| borrowers | names/aliases, citizenship/residency status, residence history, employment history |
| income | type, employer/source, period, documents, calculated monthly amount, method, confidence |
| assets | institution, masked account number, owner, statement period, balances, deposits, usable amount |
| liabilities | creditor, type, balance, payment, source, exclusion, payoff/omit status |
| property/REO | address, type, taxes, insurance, HOA, mortgages, rent, solar, condo status |
| contract | price, EMD, concessions, dates, parties, signatures, addenda, personal property |
| evidence | document ID, type, filename, page, date, extracted value, extraction confidence |
| findings | rule ID, status, evidence IDs, explanation, blocking flag, proposed action, approver |

Schema-builder prompt

```
Create schemas/loan_file.schema.json using JSON Schema Draft 2020-12 and schemas/audit_result.schema.json.

Requirements:
- Currency values must be decimal strings, never binary floating-point numbers.
- Dates must use ISO 8601.
- Account numbers must be masked in outputs.
- Every extracted fact must link to at least one evidence record.
- Every PASS finding must link to evidence.
- FAIL and MISSING findings must include a proposed action.
- REVIEW findings must include a reason and required reviewer role.
- Unknown values must remain null; never infer them.

Create scripts/validate_schema.py and unit tests for valid and invalid examples. Do not build any LOS integration.
```

## 5. Create the always-on CLAUDE.md

Keep this under roughly 200 lines. Procedures belong in skills; non-negotiable operating rules belong here.

Recommended CLAUDE.md content

```
# Mpire Mortgage Operations Copilot

## Mission
Help licensed mortgage personnel audit and prepare loan files. This system provides operational decision support; it does not make final credit or compliance decisions.

## Non-negotiable rules
1. Never invent a borrower fact, document, guideline, calculation input, or completed action.
2. A PASS requires cited evidence: document ID, filename, page, and relevant value.
3. Missing evidence is MISSING or REVIEW, never PASS or NOT_APPLICABLE.
4. Use Decimal arithmetic for money and retain the calculation trail.
5. Quote or cite only approved guideline sources and record the effective date.
6. Flag conflicts between the 1003, credit, documents, contract, AUS, and LOS.
7. Mask SSNs and account numbers in all reports and logs.
8. Never send communications, write to the LOS, run AUS, trigger TRID, select a lender, or submit a loan without explicit approval from an authorized human.
9. Never change source documents. Write derived data only to output/.
10. Validate every JSON output against its schema before reporting completion.

## Result language
Use only PASS, FAIL, MISSING, REVIEW, and NOT_APPLICABLE.
Distinguish blocking findings from advisory findings.

## Source precedence
Source document > verified external source > LOS export > user-provided note > model inference.
Model inference can never be treated as a verified fact.

## Development workflow
Plan first. Make small changes. Run relevant tests. Report files changed, tests run, unresolved risks, and requested approvals.
```

## 6. Build the first four skills

Do not build all twelve skills at once. The first vertical slice should prove that one loan package can move from intake to a defensible readiness report.

### Skill 1 — loan-file-intake

Purpose: inventory and classify files, extract facts, detect duplicates, and create the canonical loan_file.json.

Skill-creation prompt

```
Create .claude/skills/loan-file-intake/SKILL.md plus any supporting scripts and references.

The skill must:
1. Accept a loan-package directory as $ARGUMENTS.
2. Inventory files and calculate SHA-256 hashes.
3. Classify document types without renaming or modifying sources.
4. Extract text with page boundaries preserved.
5. Record document dates, statement periods, borrower names, and masked account identifiers.
6. Write output/audits/<loan-id>/document_inventory.json and loan_file.json.
7. Mark low-confidence OCR/extraction as REVIEW.
8. Validate outputs against schemas.
9. Produce a concise completion report.

Safety: read-only access to the input directory; no network, email, LOS, deletion, or source-file edits.
Add fixtures and tests for duplicate documents, unreadable PDFs, password-protected PDFs, missing pages, and conflicting names.

Write a precise description in frontmatter so Claude invokes this skill for intake, document inventory, classification, and initial extraction—but not for underwriting conclusions.
```

### Skill 2 — preapproval-audit

Purpose: evaluate the normalized preapproval checklist against the canonical loan file.

Skill-creation prompt

```
Create .claude/skills/preapproval-audit/SKILL.md.

The skill must apply the checklist catalog to these domains: credit, liabilities, REO, employment, regular income, variable income, self-employment, other income, assets, gift funds, retirement funds, title, and property.

For every rule return: rule_id, result, evidence_ids, explanation, discrepancy, proposed_action, blocking, reviewer_role, confidence.

Constraints:
- Do not decide a guideline question unless an approved reference contains the rule.
- Do not use income not required to qualify unless clearly labeled “potential additional income.”
- Do not calculate self-employed income in version 1; classify documents and route it to REVIEW.
- Do not pass an item merely because a related document exists.
- Validate the complete result against audit_result.schema.json.

Create golden tests from de-identified fixtures and generate both JSON and a human-readable Markdown report.
```

### Skill 3 — submission-readiness

Purpose: reconcile the preapproval audit, purchase/contract facts, and submission requirements into a final gate.

Skill-creation prompt

```
Create .claude/skills/submission-readiness/SKILL.md.

Inputs: loan_file.json, preapproval_audit.json, checklist_catalog.yaml, and an optional read-only LOS export.

Output:
- overall status: READY | NOT_READY | HUMAN_REVIEW
- blocking findings
- nonblocking findings
- missing documents
- proposed LOS corrections
- proposed client needs
- proposed Arive notes
- approvals still required
- checklist coverage percentage

READY is allowed only when every applicable blocking rule is PASS and every PASS has evidence. A missing LOS export must prevent claims that Arive is complete. This skill must not update Arive, run AUS, trigger TRID, contact anyone, or submit the loan.
```

### Skill 4 — borrower-communication-drafts

Purpose: convert approved findings into borrower-facing drafts while avoiding unsupported claims.

Skill-creation prompt

```
Create .claude/skills/borrower-communication-drafts/SKILL.md.

Generate drafts only. Inputs must be approved finding IDs—not free-form assumptions.
Produce:
- concise document-request email
- borrower call agenda
- post-call Arive note
- agent/title follow-up draft when applicable

Use plain language, group requests by category, explain why each item is needed without making legal promises, avoid exposing internal risk scoring, and never claim approval or guaranteed closing. Label every output DRAFT — HUMAN APPROVAL REQUIRED.
```

## 7. Create the master orchestrator skill

After the four component skills pass their own tests, create one explicit entry point. During the pilot, invoke it manually with `/mortgage-file-audit <directory>` rather than relying exclusively on automatic routing.

Orchestrator prompt

```
Create .claude/skills/mortgage-file-audit/SKILL.md as the master read-only workflow.

Sequence:
1. Verify the supplied directory exists and is an approved test-data location.
2. Run loan-file-intake.
3. Validate loan_file.json.
4. Run preapproval-audit.
5. Run submission-readiness.
6. Validate all outputs.
7. Produce an executive summary and human review queue.

Stop conditions:
- source directory appears to contain live borrower PII during development
- schema validation fails
- document is unreadable or encrypted
- a required input is missing
- conflicting identity information exists
- guideline support is unavailable

Never continue past a stop condition by guessing. Never invoke a write-capable integration. Include exact output paths and checksums in the run manifest.
```

## 8. Use scripts for facts and models for judgment

| Use deterministic code for | Use Claude reasoning for |
|---|---|
| Date arithmetic, monthly averages, YTD comparisons, decimal math, duplicate detection, schema validation, page counting, hash verification | Document classification, discrepancy explanation, identifying likely relationships, drafting questions, mapping evidence to checklist language |
| Masking, file allowlists, prohibited-path checks, output completeness, validation gates | Choosing which approved rule may apply—followed by human review when ambiguity remains |

Calculation-engine prompt

```
Build a deterministic Python calculation package under scripts/calculations/. Use Decimal and explicit rounding policies. Every function must return inputs, formula, intermediate values, output, warnings, and method version.

Implement only:
- salaried monthly base
- hourly base using verified hours and frequency
- YTD pace comparison
- statement balance reconciliation
- funds-to-close arithmetic
- liability payment comparison
- date expiration checks

Do not implement commission, bonus, overtime, rental, self-employed, asset depletion, or guideline-dependent income until a licensed reviewer supplies written calculation specifications. Add unit tests, boundary tests, and negative tests.
```

## 9. Enforce safety with permissions and hooks

Prompt instructions are not enforcement. Claude’s official guidance distinguishes advisory context from hooks and permissions that can block actions. Begin with no production credentials and no write-capable MCP tools.

Safety implementation prompt

```
Create a security plan before writing any hooks.

The plan must:
- deny access to .env, secrets, credential stores, and unapproved directories
- deny deletion and destructive shell commands
- deny outbound network access during the read-only MVP unless explicitly approved
- deny all LOS, email, SMS, AUS, TRID, pricing, and submission write operations
- require schema validation before a workflow can report completion
- require an audit manifest for each run
- fail closed if a hook or validator errors

Then propose .claude/settings.json and hook scripts. Do not activate them until I review the exact matchers, commands, and failure behavior.
```

| Important: Test every deny rule with a harmless fixture. A security control that has never been tested is only an assumption. |
|---|

## 10. Build the evaluation suite before integrations

The system is not ready because a demo looked correct. It is ready only when it consistently matches a human-reviewed answer key and fails safely on ambiguous inputs.

| Metric | Initial target |
|---|---|
| Document classification accuracy | ≥ 98% |
| Checklist coverage | 100% of normalized source items evaluated or explicitly N/A with evidence |
| False PASS rate on blocking items | 0% |
| Calculation agreement | 100% on supported deterministic calculations |
| Evidence citation accuracy | ≥ 99% |
| PII leakage in reports/logs | 0 known occurrences |
| Correct stop/escalation behavior | 100% on designed edge cases |

Evaluation-builder prompt

```
Create a test harness that runs the master audit skill against every directory under tests/fixtures/deidentified/ and compares results with tests/expected/.

Report:
- true/false PASS, FAIL, MISSING, REVIEW, and N/A outcomes
- false PASS rate for blocking rules
- missed blocking issues
- unsupported claims
- missing or incorrect evidence citations
- numeric mismatches
- schema failures
- PII leakage checks
- runtime and token use

Never modify expected answers automatically. Proposed answer-key changes must be written to a separate review file and approved by a licensed reviewer.
```

## 11. Pilot operating procedure

Select 10 de-identified closed files: five clean, three moderately complex, and two deliberately problematic.

Have an experienced LO or processor prepare the answer key without seeing Claude’s output.

Run `/mortgage-file-audit <test-directory>` on each file.

Score every checklist item—not only the final readiness label.

Classify every error: extraction, normalization, calculation, rule selection, reasoning, or report-generation error.

Fix the narrowest underlying cause and add a regression test.

Repeat until the false-PASS rate on blocking items is zero across the test set.

Expand to 25–50 de-identified files before any production pilot.

For the production pilot, remain read-only and require a human side-by-side review.

Daily pilot-use prompt

```
/mortgage-file-audit tests/fixtures/deidentified/<loan-id>

Run the complete read-only audit. Do not use external systems or modify source files. Return:
1. overall readiness status
2. blocking findings first
3. missing documents
4. conflicting facts
5. calculation summary
6. checklist coverage
7. items requiring licensed review
8. exact output files created

For every conclusion, cite the source file and page. If evidence is unavailable, say MISSING or REVIEW.
```

## 12. Integration roadmap

| Order | Integration | First permission |
|---|---|---|
| 1 | Read-only LOS export | Read a manually exported JSON/CSV/PDF copy |
| 2 | Approved guideline repository | Search/read versioned internal references |
| 3 | ClickUp | Create draft task payload; human approves creation |
| 4 | Arive/LendingPad | Read-only fields and document metadata |
| 5 | Arive/LendingPad writes | Update only approved fields with before/after audit log |
| 6 | Email/SMS | Create drafts; later send only after explicit approval |
| 7 | AUS/TRID/submission | Prepare action; authorized person executes or explicitly approves |

Prefer an official API. If the LOS has no suitable API, use browser automation only after the read-only workflow is stable, and design for screen changes, timeouts, duplicate actions, and rollback.

## 13. Prompt pack for managing the build

### A. Start-of-session prompt

Read CLAUDE.md and inspect the repository. Summarize the current architecture, implemented skills, test status, known risks, and the smallest safe next milestone. Do not modify files until you show a plan and I approve it.

### B. Implement-one-task prompt

Implement only [TASK]. Stay within the read-only MVP. Before editing, list the files you expect to change and acceptance tests. After editing, run the tests and report: changed files, test results, remaining risks, and anything requiring mortgage/compliance review.

### C. Adversarial review prompt

Act as a skeptical mortgage operations QA reviewer. Try to prove the latest workflow output is wrong. Look for unsupported PASS results, missing pages, stale documents, mismatched identities, incorrect periods, arithmetic errors, hidden assumptions, guideline ambiguity, and PII leakage. Do not edit the answer key. Produce reproducible failures.

### D. Skill-routing review prompt

Review every skill name and frontmatter description. Identify overlaps, gaps, and cases where Claude might invoke the wrong skill. Rewrite descriptions to state exactly when the skill should and should not run. Preserve the skill bodies. Return a routing test matrix with at least 25 user requests and the expected skill.

### E. Human-approval packet prompt

Using only validated audit findings, create a human approval packet containing: proposed action, supporting evidence, before/after values, rule or checklist ID, operational impact, risk if wrong, required approver, and rollback method. Do not execute any action.

### F. Change-control prompt

A policy or checklist has changed. Compare the new source against the current version. Produce a change log, affected rule IDs, affected skills/scripts/tests, migration risk, and proposed regression tests. Do not overwrite the existing rule catalog until a reviewer approves the mapping.

## 14. Definition of done for MVP 1

All checklist statements have stable IDs and source-page traceability.

A canonical loan-file schema and audit-result schema exist and validate.

The four component skills and master orchestrator run on de-identified fixtures.

Every PASS contains evidence; every unknown remains unknown.

Supported calculations are deterministic and fully reproducible.

The system cannot access production credentials or perform external writes.

The test harness reports false PASS, evidence, calculation, schema, and privacy failures.

A licensed reviewer signs off on the normalized rules and answer keys.

The retrospective test set meets the targets in Section 10.

Known limitations are visible in every final audit report.

## 15. Your first working session

Complete only these actions in the first session:

Install Claude Code and verify it with `claude --version` and `claude doctor`.

Create a private repository and the folder skeleton.

Add copies of the three checklists.

Add the recommended CLAUDE.md.

Run the checklist-normalization prompt.

Review the first 10 normalized rules manually before allowing the full catalog to be written.

Commit the approved baseline without any borrower data.

| Do not do yet: Do not connect Arive, LendingPad, email, ClickUp, pricing, AUS, or Mako’s submission portal during the first build session. |
|---|

## 16. Decisions needed before Phase 2

Primary LOS: Arive, LendingPad, or both?

Can the LOS export a complete file as JSON, XML, MISMO, CSV, or PDF?

Which loan products belong in the first release: conventional only, or FHA/VA/USDA/Non-QM too?

Which internal or investor guidelines are approved sources?

Who signs off on income calculations and policy interpretations?

Where may de-identified test files be stored?

What constitutes a blocking versus advisory finding?

Which actions require LO, processor, compliance, or management approval?

## Sources and technical references

Claude Code technical details in this playbook were checked against Anthropic’s official documentation on September 2, 2026:

Claude Code setup: https://code.claude.com/docs/en/setup

Skills: https://code.claude.com/docs/en/skills

Project memory and CLAUDE.md: https://code.claude.com/docs/en/memory

Hooks: https://code.claude.com/docs/en/hooks

Subagents: https://code.claude.com/docs/en/sub-agents

MCP: https://code.claude.com/docs/en/mcp

Plugins reference: https://code.claude.com/docs/en/plugins-reference

Mortgage operational source documents: the three attached Mako and submission checklists listed on the cover. This playbook does not replace agency, investor, lender, federal, state, or company compliance guidance.
