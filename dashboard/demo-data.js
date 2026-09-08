/* Sample data for PREVIEW mode only. Mirrors what api/ returns for the three
 * synthetic loans seeded by services/demo.py (LN-EXAMPLE-*). Every value here
 * comes from tests/fixtures/examples — nothing is a real borrower, document,
 * or decision. The dashboard uses this only when /config.js or /api/health
 * cannot be reached. */
(function () {
  "use strict";

  var GENERATED = "2026-09-08T12:00:00Z";
  var LIMITS = [
    "EXAMPLE FIXTURE: rule ids reference placeholder catalog items, not real checklist rules.",
    "Commission, bonus, overtime, rental, self-employed, and asset-depletion income are not evaluated in this release.",
    "No guideline lookups were performed; nothing here is a credit or compliance decision."
  ];
  var INPUT_HASHES = {
    loan_file: "5ee1bf99167a25356c890d03533ff16c60f495718b0f00539377999b0bf4f456",
    inventory: "71a3e4bd3921d73f7420a1e05acf0749553210adca8edfd44027f5a4ed9550ed",
    catalog: "6b790d0f4b901f2e308c9150eb18572dd89494c9dc83974dcd2f5627aa63a3ea"
  };

  function finding(base) {
    return Object.assign({
      evidence_ids: [], discrepancy: null, proposed_action: null, reviewer_role: null,
      confidence: "HIGH", review_reason: null, calculation: null, guideline_source: null
    }, base);
  }

  var PRE_FINDINGS = [
    finding({ finding_id: "F-001", rule_id: "PRE-EXAMPLE-001", result: "PASS", blocking: true, evidence_ids: ["EV-005"],
      explanation: "Paystub DOC-002 page 1 supports a salaried base of 6500.00 per month (3250.00 semi-monthly).",
      calculation: { method: "salaried_monthly_base", method_version: "1.0",
        inputs: { gross_pay_period: "3250.00", pay_frequency: "SEMI_MONTHLY", periods_per_year: 24 },
        formula: "gross_pay_period * periods_per_year / 12",
        intermediate_values: { annual_base: "78000.00" }, output: "6500.00", warnings: [] } }),
    finding({ finding_id: "F-002", rule_id: "PRE-EXAMPLE-002", result: "FAIL", blocking: true, evidence_ids: ["EV-002", "EV-008"],
      explanation: "Placeholder failing item: the 1003 loan amount and contract purchase price were compared and did not satisfy the example rule.",
      discrepancy: "1003 loan amount 400000.00 vs contract purchase price 500000.00 (example only).",
      proposed_action: "Loan officer to confirm the intended loan amount with the borrower and correct the 1003.",
      reviewer_role: "LOAN_OFFICER" }),
    finding({ finding_id: "F-003", rule_id: "PRE-EXAMPLE-003", result: "MISSING", blocking: true,
      explanation: "No W-2 for Test Borrower Alpha was found in the fixture directory.",
      proposed_action: "Request the most recent W-2 for Test Borrower Alpha.", reviewer_role: "PROCESSOR" }),
    finding({ finding_id: "F-004", rule_id: "PRE-EXAMPLE-004", result: "REVIEW", blocking: false, evidence_ids: ["EV-007"],
      explanation: "A 12000.00 deposit on the 2026-07 statement is unsourced and its description was extracted at LOW confidence.",
      reviewer_role: "PROCESSOR", confidence: "LOW",
      review_reason: "Deposit source cannot be determined from the statement; a human must decide whether documentation is required." }),
    finding({ finding_id: "F-005", rule_id: "PRE-EXAMPLE-005", result: "NOT_APPLICABLE", blocking: false, evidence_ids: ["EV-001"],
      explanation: "Placeholder item applies only to refinance transactions; the 1003 shows purpose Purchase." })
  ];
  var READY_FINDINGS = [
    finding({ finding_id: "F-001", rule_id: "SUB-EXAMPLE-001", result: "PASS", blocking: true, evidence_ids: ["EV-002"],
      explanation: "1003 loan amount 400000.00 is present and cited." }),
    finding({ finding_id: "F-002", rule_id: "SUB-EXAMPLE-002", result: "PASS", blocking: true, evidence_ids: ["EV-006"],
      explanation: "Bank statement ending balance 48210.55 is present and cited." }),
    finding({ finding_id: "F-003", rule_id: "SUB-EXAMPLE-003", result: "NOT_APPLICABLE", blocking: true, evidence_ids: ["EV-001"],
      explanation: "Placeholder refinance-only item; purpose is Purchase." }),
    finding({ finding_id: "F-004", rule_id: "SUB-EXAMPLE-004", result: "REVIEW", blocking: false, evidence_ids: ["EV-007"],
      explanation: "Advisory placeholder: deposit description extracted at LOW confidence.", reviewer_role: "PROCESSOR",
      confidence: "LOW", review_reason: "Human confirmation of the OCR-read deposit description." })
  ];
  var NOT_READY_FINDINGS = [
    finding({ finding_id: "F-001", rule_id: "SUB-EXAMPLE-001", result: "PASS", blocking: true, evidence_ids: ["EV-002"],
      explanation: "1003 loan amount 400000.00 is present and cited." }),
    finding({ finding_id: "F-002", rule_id: "SUB-EXAMPLE-002", result: "MISSING", blocking: true,
      explanation: "No W-2 for Test Borrower Alpha in the fixture directory.",
      proposed_action: "Request the most recent W-2 for Test Borrower Alpha before submission.", reviewer_role: "PROCESSOR" })
  ];

  var DOCS = [
    { document_id: "DOC-001", filename: "urla_1003_example.pdf", sha256: "f2cfba5f0f0fa32caf3ea404cc98a7f99590b359ebf7b7dd68c3424077340352", size_bytes: 184320, document_type: "URLA_1003", classification_confidence: "HIGH", page_count: 9, status: "OK", duplicate_of: null, document_date: "2026-08-20" },
    { document_id: "DOC-002", filename: "paystub_alpha_2026-08-15.pdf", sha256: "5d901a5db7a335449cfc4b255cefffaacb38115f8a9cc7428942556cd7acf1d0", size_bytes: 40960, document_type: "PAYSTUB", classification_confidence: "HIGH", page_count: 1, status: "OK", duplicate_of: null, document_date: "2026-08-15" },
    { document_id: "DOC-003", filename: "bank_statement_example_2026-07.pdf", sha256: "09be7236ffa829bc200e658e0e4fa9dd5bd54f3f6b2c527728317c509e63edcd", size_bytes: 122880, document_type: "BANK_STATEMENT", classification_confidence: "MEDIUM", page_count: 4, status: "OK", duplicate_of: null, document_date: "2026-07-31" },
    { document_id: "DOC-004", filename: "purchase_contract_example.pdf", sha256: "713dcf64aa070869725340b562e7350fb8c007d4acec574fa87fa57a964bb72d", size_bytes: 512000, document_type: "PURCHASE_CONTRACT", classification_confidence: "HIGH", page_count: 12, status: "OK", duplicate_of: null, document_date: "2026-09-01" },
    { document_id: "DOC-005", filename: "credit_report_example.pdf", sha256: "24ab7c9779d7ba43e7fd3d46d03fa11851662d7db232ae10a661be621bdb3b06", size_bytes: 98304, document_type: "CREDIT_REPORT", classification_confidence: "HIGH", page_count: 6, status: "OK", duplicate_of: null, document_date: "2026-08-18" },
    { document_id: "DOC-006", filename: "bank_statement_example_2026-07 (1).pdf", sha256: "09be7236ffa829bc200e658e0e4fa9dd5bd54f3f6b2c527728317c509e63edcd", size_bytes: 122880, document_type: "BANK_STATEMENT", classification_confidence: "MEDIUM", page_count: 4, status: "DUPLICATE", duplicate_of: "DOC-003", document_date: "2026-07-31" },
    { document_id: "DOC-007", filename: "encrypted_upload_example.pdf", sha256: "8d29b4b700cbfd42a93a078c85e7b8f22714b85b4a76d484368b8d97778872cc", size_bytes: 20480, document_type: "UNKNOWN", classification_confidence: "LOW", page_count: null, status: "ENCRYPTED", duplicate_of: null, document_date: null }
  ];

  var PRE_MISSING = [{ document_type: "W2", borrower_id: "B-1", description: "Most recent W-2 for Test Borrower Alpha.", rule_ids: ["PRE-EXAMPLE-003"] }];
  var SUB_MISSING = [{ document_type: "W2", borrower_id: "B-1", description: "Most recent W-2 for Test Borrower Alpha.", rule_ids: ["SUB-EXAMPLE-002"] }];
  var PRE_CONFLICTS = [{
    conflict_id: "CF-001", field: "contract.purchase_price",
    values: [
      { source: "USER_NOTE", value: "480000.00", evidence_ids: [] },
      { source: "PURCHASE_CONTRACT", value: "500000.00", evidence_ids: ["EV-008"] }
    ],
    explanation: "The user note states 480000.00 but the signed contract shows 500000.00. Source document outranks user note; the contract value is used and the note is flagged.",
    rule_ids: ["PRE-EXAMPLE-002"]
  }];
  var PRE_ACTIONS = [{
    action_id: "PA-001", action_type: "CLIENT_NEED", target: "B-1",
    description: "Provide most recent W-2 and a source explanation for the 12000.00 deposit on 2026-07-18.",
    before_value: null, after_value: null, rule_ids: ["PRE-EXAMPLE-003", "PRE-EXAMPLE-004"], evidence_ids: ["EV-007"],
    approver_role: "LOAN_OFFICER", status: "DRAFT_HUMAN_APPROVAL_REQUIRED"
  }];
  var PRE_APPROVALS = [{ description: "Decide whether the unsourced 12000.00 deposit must be documented before preapproval.", approver_role: "UNDERWRITER", rule_ids: ["PRE-EXAMPLE-004"] }];
  var REVIEW_ITEM = { review_id: "RV-001", category: "LOW_CONFIDENCE_EXTRACTION",
    description: "Large deposit description on DOC-003 page 2 was OCR-extracted with LOW confidence; confirm the 12000.00 amount and its source.",
    document_ids: ["DOC-003"], evidence_ids: ["EV-007"], reviewer_role: "PROCESSOR" };

  function tagged(loanId, runId, rows, extra) {
    return rows.map(function (r, i) {
      var o = Object.assign({ loan_id: loanId, run_id: runId }, extra || {}, r);
      if (extra && extra.__seq) { o.seq = i + 1; delete o.__seq; }
      return o;
    });
  }

  function buildRun(spec) {
    var loanId = spec.loan_id, runId = spec.run_id;
    var audits = spec.audits; // [{type, findings, missing, conflicts, actions, approvals, counts, blocking_open, coverage, overall_status, los}]
    var gate = audits[audits.length - 1];
    var findings = [], missing = [], conflicts = [], actions = [], approvals = [];
    audits.forEach(function (a) {
      var ex = { audit_type: a.type };
      findings = findings.concat(tagged(loanId, runId, a.findings, ex));
      missing = missing.concat(tagged(loanId, runId, a.missing || [], { audit_type: a.type, __seq: true }));
      conflicts = conflicts.concat(tagged(loanId, runId, a.conflicts || [], ex));
      actions = actions.concat(tagged(loanId, runId, a.actions || [], ex));
      approvals = approvals.concat(tagged(loanId, runId, a.approvals || [], { audit_type: a.type, __seq: true }));
    });
    var manifest = {
      schema_version: "1.0", loan_id: loanId, run_id: runId, skill: "mortgage-file-audit",
      started_at: GENERATED, completed_at: GENERATED, stop_condition: null, completed_normally: true,
      tool_versions: { python: "3.11", "mortgage-file-audit": "0.1.0", "checklist_catalog": "0.1.0" },
      inputs: [
        { path: "tests/fixtures/deidentified/" + loanId + "/MANIFEST.yaml", sha256: "c0f1b839df46ebbff27030e73b3a27e83585dea9831c4562b62b5b4c90587fe8", role: "fixture_manifest" },
        { path: "config/checklist_catalog.yaml", sha256: INPUT_HASHES.catalog, role: "checklist_catalog" }
      ],
      outputs: [
        { path: "output/audits/" + loanId + "/" + runId + "/loan_file.json", sha256: INPUT_HASHES.loan_file, role: "loan_file" },
        { path: "output/audits/" + loanId + "/" + runId + "/document_inventory.json", sha256: INPUT_HASHES.inventory, role: "document_inventory" }
      ],
      totals: { inputs: 2, outputs: 2, input_bytes: 4096, output_bytes: 12288 }
    };
    var reportMd = "# DEMO REPORT — " + loanId + " / " + runId + "\n\n" +
      "DECISION SUPPORT ONLY — not a credit or compliance decision.\n\n" +
      "Synthetic example data from tests/fixtures/examples.\n\n" +
      "## Summary\n\n| Result | Count |\n|---|---|\n" +
      Object.keys(gate.counts).map(function (k) { return "| " + k + " | " + gate.counts[k] + " |"; }).join("\n") +
      "\n\n## Known limitations\n\n" + LIMITS.map(function (l) { return "- " + l; }).join("\n") +
      "\n\n```\ncoverage_percent = " + gate.coverage + "\nblocking_open = " + gate.blocking_open + "\n```\n";
    var run = {
      loan_id: loanId, run_id: runId, skill: "mortgage-file-audit", started_at: GENERATED, completed_at: GENERATED,
      completed_normally: true, stop_condition: null,
      overall_status: gate.type === "SUBMISSION_READINESS" ? gate.overall_status : null,
      preapproval_present: audits.some(function (a) { return a.type === "PREAPPROVAL"; }),
      submission_present: audits.some(function (a) { return a.type === "SUBMISSION_READINESS"; }),
      los_export_present: gate.los, catalog_version: "0.1.0", catalog_reviewed: null,
      counts: gate.counts, blocking_open: gate.blocking_open, coverage_percent: gate.coverage,
      known_limitations: LIMITS.slice(), tool_versions: manifest.tool_versions, manifest: manifest,
      totals: { files: 7, pages: 36, bytes: 1101824 }, synced_at: GENERATED
    };
    var loan = { loan_id: loanId, description: spec.description, deidentified: true,
      source_root: "tests/fixtures/deidentified/" + loanId, created_at: GENERATED, updated_at: GENERATED };
    return {
      bundle: {
        loan: loan, run: run,
        documents: tagged(loanId, runId, DOCS),
        findings: findings,
        review_items: tagged(loanId, runId, [REVIEW_ITEM]),
        missing_documents: missing, conflicts: conflicts, proposed_actions: actions, approvals_required: approvals,
        reports: [{ loan_id: loanId, run_id: runId, name: "report.md", content_md: reportMd,
          sha256: "a1c5e0d4b8f27a6c93d1e4f5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5" }]
      },
      review_decisions: spec.review_decisions || [],
      action_decisions: spec.action_decisions || []
    };
  }

  var PRE_AUDIT = { type: "PREAPPROVAL", findings: PRE_FINDINGS, missing: PRE_MISSING, conflicts: PRE_CONFLICTS,
    actions: PRE_ACTIONS, approvals: PRE_APPROVALS,
    counts: { PASS: 1, FAIL: 1, MISSING: 1, REVIEW: 1, NOT_APPLICABLE: 1 }, blocking_open: 2, coverage: "83.3", overall_status: null, los: false };
  var READY_AUDIT = { type: "SUBMISSION_READINESS", findings: READY_FINDINGS, missing: [], conflicts: [], actions: [], approvals: [],
    counts: { PASS: 2, FAIL: 0, MISSING: 0, REVIEW: 1, NOT_APPLICABLE: 1 }, blocking_open: 0, coverage: "100.0", overall_status: "READY", los: true };
  var NOT_READY_AUDIT = { type: "SUBMISSION_READINESS", findings: NOT_READY_FINDINGS, missing: SUB_MISSING, conflicts: [], actions: [], approvals: [],
    counts: { PASS: 1, FAIL: 0, MISSING: 1, REVIEW: 0, NOT_APPLICABLE: 0 }, blocking_open: 1, coverage: "100.0", overall_status: "NOT_READY", los: true };

  var RUNS = [
    buildRun({ loan_id: "LN-EXAMPLE-0001", run_id: "RUN-DEMO-0001", audits: [PRE_AUDIT, READY_AUDIT],
      description: "Synthetic purchase file, submission gate READY (demo data)",
      action_decisions: [{ decision_id: "AD-DEMO-0001", loan_id: "LN-EXAMPLE-0001", run_id: "RUN-DEMO-0001", audit_type: "PREAPPROVAL",
        action_id: "PA-001", decision: "ACCEPTED", note: "Sample decision (demo data): request sent for the W-2.", decided_by: "demo-user", decided_at: GENERATED }] }),
    buildRun({ loan_id: "LN-EXAMPLE-0002", run_id: "RUN-DEMO-0002", audits: [NOT_READY_AUDIT],
      description: "Synthetic purchase file with an open blocking MISSING item (demo data)" }),
    buildRun({ loan_id: "LN-EXAMPLE-0003", run_id: "RUN-DEMO-0003", audits: [PRE_AUDIT],
      description: "Synthetic file with a preapproval audit only, no submission gate yet (demo data)" })
  ];

  function reviewQueue() {
    var out = [];
    RUNS.forEach(function (d) {
      var b = d.bundle;
      var decided = {};
      d.review_decisions.forEach(function (rd) { decided[(rd.audit_type || "") + "|" + rd.target_id] = true; });
      b.findings.forEach(function (f) {
        if (!f.reviewer_role) return;
        if (decided[f.audit_type + "|" + f.finding_id]) return;
        out.push({ loan_id: f.loan_id, run_id: f.run_id, audit_type: f.audit_type, target_id: f.finding_id, kind: "FINDING",
          rule_id: f.rule_id, reviewer_role: f.reviewer_role, reason: f.review_reason || f.proposed_action || f.discrepancy,
          blocking: f.blocking, explanation: f.explanation });
      });
      b.review_items.forEach(function (r) {
        if (decided["|" + r.review_id]) return;
        out.push({ loan_id: r.loan_id, run_id: r.run_id, audit_type: null, target_id: r.review_id, kind: "REVIEW_ITEM",
          rule_id: r.category, reviewer_role: r.reviewer_role, reason: null, blocking: false, explanation: r.description });
      });
    });
    out.sort(function (a, b) { return (b.blocking ? 1 : 0) - (a.blocking ? 1 : 0); });
    return out;
  }

  var RUN_REQUESTS = [
    { request_id: "RQ-DEMO-0001", loan_id: "LN-EXAMPLE-0002", requested_by: "demo-user", requested_at: GENERATED,
      note: "Re-run after the W-2 arrives (demo data).", status: "QUEUED", run_id: null, updated_at: GENERATED },
    { request_id: "RQ-DEMO-0002", loan_id: "LN-EXAMPLE-0001", requested_by: "demo-user", requested_at: "2026-09-07T15:30:00Z",
      note: "Initial audit (demo data).", status: "COMPLETED", run_id: "RUN-DEMO-0001", updated_at: GENERATED }
  ];

  var EVAL_LATEST = {
    eval_id: "EVAL-DEMO-0001", generated_at: GENERATED, all_targets_met: true,
    targets: {
      classification: { observed: "100% (7/7)", status: "MET" },
      coverage: { observed: "100% (6/6)", status: "MET" },
      false_pass: { observed: "0 false PASS on 4 blocking rules", status: "MET" },
      calculation: { observed: "100% (1/1)", status: "MET" },
      evidence: { observed: "not evaluated", status: "NOT_EVALUATED" },
      pii: { observed: "0 occurrence(s)", status: "MET" },
      stop: { observed: "not evaluated", status: "NOT_EVALUATED" },
      schema: { observed: "0 failing file(s)", status: "MET" }
    },
    report: { mode: "retrospective", fixtures_evaluated: 1, note: "Synthetic evaluation report (demo data)." }
  };

  function summary() {
    var s = { loans: RUNS.length, runs: RUNS.length, ready: 0, not_ready: 0, human_review: 0, no_gate: 0,
      blocking_open: 0, review_queue: reviewQueue().length,
      queued_requests: RUN_REQUESTS.filter(function (r) { return r.status === "QUEUED"; }).length, pending_actions: 0 };
    RUNS.forEach(function (d) {
      var r = d.bundle.run;
      if (r.overall_status === "READY") s.ready += 1;
      else if (r.overall_status === "NOT_READY") s.not_ready += 1;
      else if (r.overall_status === "HUMAN_REVIEW") s.human_review += 1;
      else s.no_gate += 1;
      s.blocking_open += r.blocking_open || 0;
      var decided = {};
      d.action_decisions.forEach(function (ad) { decided[ad.audit_type + "|" + ad.action_id] = true; });
      d.bundle.proposed_actions.forEach(function (a) { if (!decided[a.audit_type + "|" + a.action_id]) s.pending_actions += 1; });
    });
    return s;
  }

  window.MPIRE_DEMO_DATA = {
    health: { status: "ok", backend: "preview", version: "0.1.0-preview", demo: true },
    runs: RUNS,
    run_requests: RUN_REQUESTS,
    eval_latest: EVAL_LATEST,
    summary: summary,
    review_queue: reviewQueue
  };
})();
