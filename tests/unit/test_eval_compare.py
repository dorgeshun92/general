"""Truth-table tests for scripts/eval/compare.py (pure functions, no I/O)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from scripts.eval.compare import ABSENT, CompareError, compare, values_equal


def finding(rule_id, result, evidence=None, blocking=True, output=None):
    f = {
        "finding_id": "F-" + rule_id[-3:],
        "rule_id": rule_id,
        "result": result,
        "evidence_ids": list(evidence or []),
        "explanation": "EXAMPLE — harness self-test, not a real checklist rule",
        "discrepancy": None,
        "proposed_action": "do something" if result in ("FAIL", "MISSING") else None,
        "blocking": blocking,
        "reviewer_role": "PROCESSOR" if result == "REVIEW" else None,
        "confidence": "HIGH",
        "review_reason": "needs a look" if result == "REVIEW" else None,
    }
    if output is not None:
        f["calculation"] = {"method": "m", "method_version": "1.0", "inputs": {}, "formula": "x",
                            "intermediate_values": {}, "output": output, "warnings": []}
    return f


def audit(findings, status="NOT_READY"):
    return {"loan_id": "LN-T-0001", "audit_type": "SUBMISSION_READINESS", "overall_status": status,
            "findings": findings}


def test_identical_audits_are_clean():
    exp = audit([finding("SUB-EXAMPLE-001", "PASS", ["EV-001"], output="1200.00"),
                 finding("SUB-EXAMPLE-002", "FAIL", ["EV-002"])])
    rep = compare(exp, exp)
    assert rep["false_pass_blocking"] == []
    assert rep["missed_blocking"] == []
    assert rep["unsupported_claims"] == []
    assert rep["coverage_gaps"] == [] and rep["extra_findings"] == []
    assert rep["numeric_mismatches"] == []
    assert rep["evidence"] == {"checked": 2, "accurate": 2, "inaccurate": [], "accuracy_percent": "100.00"}
    assert rep["calculations"]["agreement_percent"] == "100.00"
    assert rep["coverage"]["coverage_percent"] == "100.00"
    assert rep["overall_status"]["agree"] is True
    assert rep["outcome_matrix"]["PASS"]["PASS"] == 1 and rep["outcome_matrix"]["FAIL"]["FAIL"] == 1


def test_false_pass_on_blocking_rule_detected():
    exp = audit([finding("SUB-EXAMPLE-002", "FAIL", ["EV-002"], blocking=True)])
    act = audit([finding("SUB-EXAMPLE-002", "PASS", ["EV-002"], blocking=True)], status="READY")
    rep = compare(exp, act)
    assert rep["false_pass_blocking"] == [{"rule_id": "SUB-EXAMPLE-002", "expected": "FAIL", "actual": "PASS"}]
    assert rep["missed_blocking"] == [{"rule_id": "SUB-EXAMPLE-002", "expected": "FAIL", "actual": "PASS"}]
    assert rep["false_pass_rate_percent"] == "100.00"
    assert rep["outcome_matrix"]["FAIL"]["PASS"] == 1
    assert rep["overall_status"]["agree"] is False


def test_false_pass_uses_blocking_from_either_side():
    exp = audit([finding("SUB-EXAMPLE-002", "MISSING", blocking=False)])
    act = audit([finding("SUB-EXAMPLE-002", "PASS", ["EV-009"], blocking=True)])
    rep = compare(exp, act)
    assert [d["rule_id"] for d in rep["false_pass_blocking"]] == ["SUB-EXAMPLE-002"]
    assert rep["blocking_rules_expected"] == 0  # answer key did not flag it; rate is undefined
    assert rep["false_pass_rate_percent"] is None


def test_non_blocking_false_pass_is_not_headline():
    exp = audit([finding("SUB-EXAMPLE-004", "REVIEW", blocking=False)])
    act = audit([finding("SUB-EXAMPLE-004", "PASS", ["EV-001"], blocking=False)])
    rep = compare(exp, act)
    assert rep["false_pass_blocking"] == []
    assert rep["outcome_matrix"]["REVIEW"]["PASS"] == 1
    assert rep["result_agreement"]["agreed"] == 0


@pytest.mark.parametrize("actual_result", ["NOT_APPLICABLE", None])
def test_missed_blocking_via_not_applicable_or_absent(actual_result):
    exp = audit([finding("SUB-EXAMPLE-003", "MISSING", blocking=True)])
    act_findings = [finding("SUB-EXAMPLE-003", actual_result, ["EV-001"])] if actual_result else []
    rep = compare(exp, audit(act_findings))
    assert rep["missed_blocking"] == [{"rule_id": "SUB-EXAMPLE-003", "expected": "MISSING",
                                       "actual": actual_result or ABSENT}]
    assert rep["false_pass_blocking"] == []
    if actual_result is None:
        assert rep["coverage_gaps"] == ["SUB-EXAMPLE-003"]
        assert rep["outcome_matrix"]["MISSING"][ABSENT] == 1


def test_unsupported_claims_flagged():
    act = audit([finding("SUB-EXAMPLE-001", "PASS", []), finding("SUB-EXAMPLE-005", "NOT_APPLICABLE", []),
                 finding("SUB-EXAMPLE-002", "FAIL", [])])
    rep = compare(audit([]), act)
    assert rep["unsupported_claims"] == [{"rule_id": "SUB-EXAMPLE-001", "result": "PASS"},
                                         {"rule_id": "SUB-EXAMPLE-005", "result": "NOT_APPLICABLE"}]
    assert rep["extra_findings"] == ["SUB-EXAMPLE-001", "SUB-EXAMPLE-002", "SUB-EXAMPLE-005"]


def test_evidence_loose_vs_exact():
    exp = audit([finding("SUB-EXAMPLE-001", "PASS", ["EV-001", "EV-002"])])
    act = audit([finding("SUB-EXAMPLE-001", "PASS", ["EV-002", "EV-007"])])
    loose = compare(exp, act)
    assert loose["evidence"]["accurate"] == 1 and loose["evidence"]["inaccurate"] == []
    exact = compare(exp, act, exact_evidence=True)
    assert exact["evidence"]["accurate"] == 0
    assert exact["evidence"]["inaccurate"][0]["mode"] == "exact"
    assert exact["evidence"]["accuracy_percent"] == "0.00"


def test_evidence_no_overlap_is_inaccurate_even_loose():
    exp = audit([finding("SUB-EXAMPLE-001", "PASS", ["EV-001"])])
    act = audit([finding("SUB-EXAMPLE-001", "PASS", ["EV-002"])])
    rep = compare(exp, act)
    assert rep["evidence"]["accurate"] == 0 and rep["evidence"]["checked"] == 1


def test_evidence_not_checked_when_key_lists_none_or_rule_absent():
    exp = audit([finding("SUB-EXAMPLE-003", "MISSING", []), finding("SUB-EXAMPLE-001", "PASS", ["EV-001"])])
    act = audit([finding("SUB-EXAMPLE-003", "MISSING", [])])
    rep = compare(exp, act)
    assert rep["evidence"]["checked"] == 0 and rep["evidence"]["accuracy_percent"] is None


def test_numeric_compare_is_decimal():
    assert values_equal("1200.00", "1200.0")
    assert not values_equal("1200.00", "1200.01")
    assert values_equal({"monthly": "1200.00", "note": "x"}, {"monthly": "1200.000", "note": "x"})
    assert not values_equal({"monthly": "1200.00"}, {"monthly": "1200.00", "extra": "1"})
    assert values_equal(["1.0", "2"], ["1", "2.00"])
    assert not values_equal(True, "1")
    assert values_equal("abc", "abc") and not values_equal("abc", "abd")


def test_numeric_mismatch_reported_and_agreement_counted():
    exp = audit([finding("SUB-EXAMPLE-001", "PASS", ["EV-001"], output="1200.00"),
                 finding("SUB-EXAMPLE-002", "PASS", ["EV-002"], output="50.00"),
                 finding("SUB-EXAMPLE-003", "PASS", ["EV-003"], output="7.00")])
    act = audit([finding("SUB-EXAMPLE-001", "PASS", ["EV-001"], output="1200.0"),
                 finding("SUB-EXAMPLE-002", "PASS", ["EV-002"], output="50.01"),
                 finding("SUB-EXAMPLE-003", "PASS", ["EV-003"])])
    rep = compare(exp, act)
    assert rep["calculations"] == {"checked": 3, "agreed": 1, "agreement_percent": "33.33"}
    by_rule = {m["rule_id"]: m for m in rep["numeric_mismatches"]}
    assert by_rule["SUB-EXAMPLE-002"]["reason"] == "output differs"
    assert by_rule["SUB-EXAMPLE-003"]["reason"] == "no calculation trail"
    assert Decimal(by_rule["SUB-EXAMPLE-002"]["expected_output"]) == Decimal("50.00")


def test_coverage_gaps_and_extra_findings():
    exp = audit([finding("SUB-EXAMPLE-001", "PASS", ["EV-001"]), finding("SUB-EXAMPLE-002", "FAIL", ["EV-002"])])
    act = audit([finding("SUB-EXAMPLE-002", "FAIL", ["EV-002"]), finding("SUB-EXAMPLE-009", "PASS", ["EV-003"])])
    rep = compare(exp, act)
    assert rep["coverage_gaps"] == ["SUB-EXAMPLE-001"]
    assert rep["extra_findings"] == ["SUB-EXAMPLE-009"]
    assert rep["coverage"] == {"expected_rules": 2, "covered_rules": 1, "coverage_percent": "50.00"}
    assert rep["rules"]["SUB-EXAMPLE-001"]["actual"] == ABSENT


def test_duplicate_rule_ids_recorded_first_wins():
    act = audit([finding("SUB-EXAMPLE-001", "FAIL", ["EV-001"]), finding("SUB-EXAMPLE-001", "PASS", ["EV-001"])])
    rep = compare(audit([finding("SUB-EXAMPLE-001", "FAIL", ["EV-001"])]), act)
    assert rep["duplicate_rule_ids"]["actual"] == ["SUB-EXAMPLE-001"]
    assert rep["false_pass_blocking"] == []


def test_malformed_input_fails_closed():
    with pytest.raises(CompareError):
        compare({"findings": "nope"}, audit([]))
    with pytest.raises(CompareError):
        compare(audit([]), {"findings": [{"result": "PASS"}]})
