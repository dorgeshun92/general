"""Pure comparison of one produced audit_result against one expected audit_result.

Everything here is keyed by rule_id and free of I/O so it can be unit-tested
directly. Money and calculation outputs are compared as Decimal, never float.

    from scripts.eval.compare import compare
    report = compare(expected_audit, actual_audit, exact_evidence=False)

Definitions (the headline metric is `false_pass_blocking`, which must be empty):

- outcome_matrix       expected result x actual result counts; actual may also be
                       ABSENT (rule in expected but not in actual).
- false_pass_blocking  expected != PASS, actual == PASS, rule is blocking.
- missed_blocking      expected FAIL/MISSING on a blocking rule, actual PASS,
                       NOT_APPLICABLE, or absent.
- unsupported_claims   actual PASS or NOT_APPLICABLE with no evidence_ids.
- evidence             when expected lists evidence_ids for a rule that actual
                       also reports: loose mode needs at least one shared id,
                       exact mode needs identical sets.
- numeric_mismatches   expected finding.calculation.output differs from actual
                       (Decimal comparison for decimal strings, recursive for
                       dicts/lists, actual calculation missing counts as mismatch).
- coverage_gaps        rule_ids in expected but absent from actual.
- extra_findings       rule_ids in actual but absent from expected.
- overall_status       expected vs actual agreement.

A rule is treated as blocking when EITHER side flags it blocking (fail closed).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

RESULT_VALUES = ("PASS", "FAIL", "MISSING", "REVIEW", "NOT_APPLICABLE")
ABSENT = "ABSENT"
MATRIX_COLUMNS = RESULT_VALUES + (ABSENT,)
_NO_EVIDENCE_RESULTS = ("PASS", "NOT_APPLICABLE")


class CompareError(ValueError):
    """Raised when an input is not shaped like an audit_result (fail closed)."""


def index_findings(audit: dict[str, Any]) -> tuple[dict[str, dict], list[str]]:
    """Map rule_id -> finding. Returns (index, duplicate_rule_ids). First occurrence wins."""
    if not isinstance(audit, dict):
        raise CompareError("audit_result must be a dict")
    findings = audit.get("findings")
    if not isinstance(findings, list):
        raise CompareError("audit_result.findings must be a list")
    index: dict[str, dict] = {}
    duplicates: list[str] = []
    for f in findings:
        if not isinstance(f, dict) or not isinstance(f.get("rule_id"), str):
            raise CompareError("every finding needs a string rule_id")
        rid = f["rule_id"]
        if rid in index:
            duplicates.append(rid)
            continue
        index[rid] = f
    return index, duplicates


def _to_decimal(value: Any) -> Decimal | None:
    """Decimal for decimal strings / ints / bools-excluded; None when not numeric. Floats are refused."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # Floats are never acceptable money values; convert via str so the
        # comparison is at least deterministic, but callers should not emit them.
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
    return None


def values_equal(expected: Any, actual: Any) -> bool:
    """Compare calculation outputs. Numeric-looking values compare as Decimal."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected) != set(actual):
            return False
        return all(values_equal(expected[k], actual[k]) for k in expected)
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(values_equal(e, a) for e, a in zip(expected, actual))
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    de, da = _to_decimal(expected), _to_decimal(actual)
    if de is not None and da is not None:
        return de == da
    return expected == actual


def _is_blocking(expected_finding: dict | None, actual_finding: dict | None) -> bool:
    return bool((expected_finding or {}).get("blocking")) or bool((actual_finding or {}).get("blocking"))


def _percent(numerator: int, denominator: int) -> str | None:
    """Decimal-string percentage with two places, or None when undefined (denominator 0)."""
    if denominator == 0:
        return None
    return str((Decimal(numerator) * Decimal(100) / Decimal(denominator)).quantize(Decimal("0.01")))


def compare(expected: dict[str, Any], actual: dict[str, Any], exact_evidence: bool = False) -> dict[str, Any]:
    """Compare a produced audit_result with an expected one. Pure; returns a structured dict."""
    exp_idx, exp_dupes = index_findings(expected)
    act_idx, act_dupes = index_findings(actual)

    matrix: dict[str, dict[str, int]] = {e: {a: 0 for a in MATRIX_COLUMNS} for e in RESULT_VALUES}
    rules: dict[str, dict[str, Any]] = {}
    false_pass_blocking: list[dict] = []
    missed_blocking: list[dict] = []
    unsupported_claims: list[dict] = []
    evidence_checked = 0
    evidence_accurate = 0
    evidence_inaccurate: list[dict] = []
    calc_checked = 0
    calc_agreed = 0
    numeric_mismatches: list[dict] = []
    coverage_gaps: list[str] = []
    result_agreed = 0
    blocking_rules_expected = 0

    for rid in sorted(exp_idx):
        ef = exp_idx[rid]
        af = act_idx.get(rid)
        e_res = ef.get("result")
        a_res = af.get("result") if af else ABSENT
        blocking = _is_blocking(ef, af)
        if ef.get("blocking"):
            blocking_rules_expected += 1
        if e_res in matrix and a_res in MATRIX_COLUMNS:
            matrix[e_res][a_res] += 1
        match = af is not None and e_res == a_res
        if match:
            result_agreed += 1
        rules[rid] = {
            "expected": e_res,
            "actual": a_res,
            "blocking": blocking,
            "match": match,
            "expected_evidence_ids": sorted(ef.get("evidence_ids") or []),
            "actual_evidence_ids": sorted(af.get("evidence_ids") or []) if af else [],
        }
        if af is None:
            coverage_gaps.append(rid)
        if blocking and e_res != "PASS" and a_res == "PASS":
            false_pass_blocking.append({"rule_id": rid, "expected": e_res, "actual": a_res})
        if blocking and e_res in ("FAIL", "MISSING") and a_res in ("PASS", "NOT_APPLICABLE", ABSENT):
            missed_blocking.append({"rule_id": rid, "expected": e_res, "actual": a_res})

        # Evidence citation accuracy: only where the answer key names evidence and the rule was produced.
        exp_ev = set(ef.get("evidence_ids") or [])
        if exp_ev and af is not None:
            act_ev = set(af.get("evidence_ids") or [])
            evidence_checked += 1
            ok = (act_ev == exp_ev) if exact_evidence else bool(act_ev & exp_ev)
            if ok:
                evidence_accurate += 1
            else:
                evidence_inaccurate.append({
                    "rule_id": rid,
                    "expected_evidence_ids": sorted(exp_ev),
                    "actual_evidence_ids": sorted(act_ev),
                    "mode": "exact" if exact_evidence else "loose",
                })

        # Calculation agreement: expected output present -> actual must match as Decimal.
        exp_calc = ef.get("calculation")
        if isinstance(exp_calc, dict) and "output" in exp_calc and exp_calc.get("output") is not None:
            calc_checked += 1
            act_calc = af.get("calculation") if af else None
            act_out = act_calc.get("output") if isinstance(act_calc, dict) else None
            if af is not None and isinstance(act_calc, dict) and values_equal(exp_calc["output"], act_out):
                calc_agreed += 1
            else:
                numeric_mismatches.append({
                    "rule_id": rid,
                    "expected_output": exp_calc["output"],
                    "actual_output": act_out,
                    "reason": "rule absent" if af is None else ("no calculation trail" if not isinstance(act_calc, dict) else "output differs"),
                })

    extra_findings = sorted(set(act_idx) - set(exp_idx))

    for rid, af in act_idx.items():
        if af.get("result") in _NO_EVIDENCE_RESULTS and not af.get("evidence_ids"):
            unsupported_claims.append({"rule_id": rid, "result": af.get("result")})
    unsupported_claims.sort(key=lambda d: d["rule_id"])

    exp_status = expected.get("overall_status")
    act_status = actual.get("overall_status")
    total_expected = len(exp_idx)
    covered = total_expected - len(coverage_gaps)

    return {
        "loan_id": expected.get("loan_id"),
        "audit_type": {"expected": expected.get("audit_type"), "actual": actual.get("audit_type")},
        "exact_evidence": exact_evidence,
        "rules": rules,
        "outcome_matrix": matrix,
        "false_pass_blocking": false_pass_blocking,
        "blocking_rules_expected": blocking_rules_expected,
        "false_pass_rate_percent": _percent(len(false_pass_blocking), blocking_rules_expected),
        "missed_blocking": missed_blocking,
        "unsupported_claims": unsupported_claims,
        "evidence": {
            "checked": evidence_checked,
            "accurate": evidence_accurate,
            "inaccurate": evidence_inaccurate,
            "accuracy_percent": _percent(evidence_accurate, evidence_checked),
        },
        "calculations": {
            "checked": calc_checked,
            "agreed": calc_agreed,
            "agreement_percent": _percent(calc_agreed, calc_checked),
        },
        "numeric_mismatches": numeric_mismatches,
        "coverage": {
            "expected_rules": total_expected,
            "covered_rules": covered,
            "coverage_percent": _percent(covered, total_expected),
        },
        "coverage_gaps": coverage_gaps,
        "extra_findings": extra_findings,
        "duplicate_rule_ids": {"expected": exp_dupes, "actual": act_dupes},
        "result_agreement": {
            "total": total_expected,
            "agreed": result_agreed,
            "agreement_percent": _percent(result_agreed, total_expected),
        },
        "overall_status": {"expected": exp_status, "actual": act_status, "agree": exp_status == act_status},
    }
