"""Schema invariants proven directly against $defs via validate_ref.

Each test states one repository rule from CLAUDE.md / docs/build-playbook.md
section 4 and shows the schema enforcing it.
"""
from __future__ import annotations

import pytest

from scripts.common.schema_registry import validate_ref

COMMON = "common.defs.schema.json#/$defs/"
AUDIT = "audit_result.schema.json#/$defs/"


def ok(data, ref):
    errors = validate_ref(data, ref)
    assert errors == [], errors


def bad(data, ref, pointer=None):
    errors = validate_ref(data, ref)
    assert errors, f"expected {data!r} to be rejected by {ref}"
    if pointer is not None:
        assert any(e.startswith(pointer + ":") for e in errors), errors
    return errors


def fact(value, evidence=("EV-001",), **extra):
    d = {"value": value, "evidence_ids": list(evidence), "confidence": "HIGH"}
    d.update(extra)
    return d


# ----------------------------------------------------------------- money
class TestMoneyFact:
    """Currency values are decimal strings, never floats."""

    def test_accepts_decimal_string(self):
        ok(fact("1234.56"), COMMON + "money_fact")
        ok(fact("-15.00"), COMMON + "money_fact")
        ok(fact("400000"), COMMON + "money_fact")

    def test_rejects_float(self):
        bad(fact(1234.56), COMMON + "money_fact", "/value")

    def test_rejects_integer_number(self):
        bad(fact(400000), COMMON + "money_fact", "/value")

    @pytest.mark.parametrize("text", ["1,234.56", "$1234.56", "1234.", ".56", "1e5", "", "12 000", "NaN"])
    def test_rejects_non_canonical_strings(self, text):
        bad(fact(text), COMMON + "money_fact", "/value")

    def test_null_with_unknown_status_is_honest(self):
        ok(fact(None, evidence=(), confidence="LOW", status="UNKNOWN"), COMMON + "money_fact")

    @pytest.mark.parametrize("number", [1234.56, 1234, 0, -1.5])
    def test_decimal_string_def_rejects_all_json_numbers(self, number):
        bad(number, COMMON + "decimal_string")


# ----------------------------------------------------------------- fact / evidence
class TestFactEvidence:
    """Every non-null extracted value cites at least one evidence record; unknown values stay null."""

    def test_value_requires_evidence(self):
        bad(fact("x", evidence=()), COMMON + "fact", "/evidence_ids")

    def test_value_with_evidence_is_valid(self):
        ok(fact("x"), COMMON + "fact")

    def test_evidence_ids_must_match_pattern(self):
        bad(fact("x", evidence=("DOC-001",)), COMMON + "fact", "/evidence_ids/0")

    def test_evidence_ids_are_unique(self):
        bad(fact("x", evidence=("EV-001", "EV-001")), COMMON + "fact", "/evidence_ids")

    @pytest.mark.parametrize("status", ["UNKNOWN", "REVIEW", "CONFLICT"])
    def test_null_value_allows_honest_statuses(self, status):
        ok(fact(None, evidence=(), status=status), COMMON + "fact")

    def test_null_value_rejects_extracted_status(self):
        bad(fact(None, evidence=(), status="EXTRACTED"), COMMON + "fact", "/status")

    def test_null_value_requires_explicit_status(self):
        assert any("status" in e for e in bad(fact(None, evidence=()), COMMON + "fact"))

    def test_required_keys(self):
        bad({"value": "x"}, COMMON + "fact")
        bad({"value": "x", "evidence_ids": ["EV-001"]}, COMMON + "fact")

    def test_no_extra_keys(self):
        bad(fact("x", _defect="not allowed"), COMMON + "fact")

    def test_confidence_enum(self):
        bad(fact("x", confidence="CERTAIN"), COMMON + "fact", "/confidence")


# ----------------------------------------------------------------- typed facts
class TestTypedFacts:
    def test_date_fact_requires_iso(self):
        ok(fact("2026-09-08"), COMMON + "date_fact")
        bad(fact("09/08/2026"), COMMON + "date_fact", "/value")
        bad(fact("2026-13-40"), COMMON + "date_fact", "/value")  # format checker rejects impossible dates

    def test_string_fact_rejects_empty_string(self):
        bad(fact(""), COMMON + "string_fact", "/value")

    def test_boolean_fact(self):
        ok(fact(True), COMMON + "boolean_fact")
        bad(fact("true"), COMMON + "boolean_fact", "/value")

    def test_integer_fact(self):
        ok(fact(3), COMMON + "integer_fact")
        bad(fact("3"), COMMON + "integer_fact", "/value")
        bad(fact(3.5), COMMON + "integer_fact", "/value")


# ----------------------------------------------------------------- masking
class TestMaskedAccount:
    """Account numbers appear only masked."""

    @pytest.mark.parametrize("value", ["****1234", "**12", "********AB12", "**********7788"])
    def test_accepts_masked(self, value):
        ok(value, COMMON + "masked_account")

    @pytest.mark.parametrize("value", ["123456781234", "1234", "*1234", "****12345", "****", "", "xxxx1234", "****-1234"])
    def test_rejects_unmasked_or_malformed(self, value):
        bad(value, COMMON + "masked_account")

    def test_nullable_variant(self):
        ok(None, COMMON + "nullable_masked_account")
        bad("123456781234", COMMON + "nullable_masked_account")


# ----------------------------------------------------------------- identifiers
class TestRuleId:
    @pytest.mark.parametrize("value", ["PRE-CREDIT-001", "SUB-PROPERTY-004", "SUB-INCOME-REGULAR-012", "PRE-EXAMPLE-999"])
    def test_accepts(self, value):
        ok(value, COMMON + "rule_id")

    @pytest.mark.parametrize(
        "value",
        ["pre-credit-001", "PRE-001", "PRE-CREDIT-1", "PRE-CREDIT-0001", "XYZ-CREDIT-001", "PRE-CREDIT1-001",
         "PRE--001", "PRE-CREDIT-001 ", "PRE_CREDIT_001"],
    )
    def test_rejects(self, value):
        bad(value, COMMON + "rule_id")


class TestOtherIds:
    def test_evidence_and_document_ids(self):
        ok("EV-001", COMMON + "evidence_id")
        ok("DOC-000123", COMMON + "document_id")
        bad("EV-1", COMMON + "evidence_id")
        bad("DOC-1", COMMON + "document_id")

    def test_sha256(self):
        ok("a" * 64, COMMON + "sha256")
        bad("A" * 64, COMMON + "sha256")
        bad("a" * 63, COMMON + "sha256")

    def test_iso_datetime(self):
        ok("2026-09-08T12:00:00Z", COMMON + "iso_datetime")
        ok("2026-09-08T12:00:00.123-07:00", COMMON + "iso_datetime")
        bad("2026-09-08 12:00:00", COMMON + "iso_datetime")
        bad("2026-09-08", COMMON + "iso_datetime")


# ----------------------------------------------------------------- findings
def finding(result, **overrides):
    base = {
        "finding_id": "F-001", "rule_id": "PRE-EXAMPLE-001", "result": result,
        "evidence_ids": ["EV-001"], "explanation": "example", "discrepancy": None,
        "proposed_action": "do the example thing", "blocking": True, "reviewer_role": "PROCESSOR",
        "confidence": "HIGH", "review_reason": "example reason",
    }
    base.update(overrides)
    return base


class TestFindingConditionals:
    """PASS needs evidence; FAIL/MISSING need a proposed action; REVIEW needs a reason and a reviewer role;
    NOT_APPLICABLE needs evidence (missing evidence is MISSING or REVIEW, never PASS or NOT_APPLICABLE)."""

    def test_pass_requires_evidence(self):
        bad(finding("PASS", evidence_ids=[]), AUDIT + "finding", "/evidence_ids")
        ok(finding("PASS", proposed_action=None, reviewer_role=None, review_reason=None), AUDIT + "finding")

    @pytest.mark.parametrize("result", ["FAIL", "MISSING"])
    def test_fail_and_missing_require_proposed_action(self, result):
        bad(finding(result, proposed_action=None), AUDIT + "finding", "/proposed_action")
        bad(finding(result, proposed_action=""), AUDIT + "finding", "/proposed_action")
        ok(finding(result, evidence_ids=[], review_reason=None, reviewer_role=None), AUDIT + "finding")

    def test_review_requires_reason(self):
        bad(finding("REVIEW", review_reason=None), AUDIT + "finding", "/review_reason")
        bad(finding("REVIEW", review_reason=""), AUDIT + "finding", "/review_reason")

    def test_review_requires_reviewer_role(self):
        bad(finding("REVIEW", reviewer_role=None), AUDIT + "finding", "/reviewer_role")
        ok(finding("REVIEW", evidence_ids=[], proposed_action=None), AUDIT + "finding")

    def test_not_applicable_requires_evidence(self):
        bad(finding("NOT_APPLICABLE", evidence_ids=[]), AUDIT + "finding", "/evidence_ids")
        ok(finding("NOT_APPLICABLE", proposed_action=None, reviewer_role=None, review_reason=None), AUDIT + "finding")

    def test_result_language_is_closed(self):
        bad(finding("WARN"), AUDIT + "finding", "/result")
        bad(finding("pass"), AUDIT + "finding", "/result")

    def test_evidence_ids_unique(self):
        bad(finding("PASS", evidence_ids=["EV-001", "EV-001"]), AUDIT + "finding", "/evidence_ids")

    def test_guideline_source_needs_effective_date(self):
        src = {"source_id": "SRC-EXAMPLE", "citation": "example"}
        bad(finding("PASS", guideline_source=src), AUDIT + "finding", "/guideline_source")
        src["effective_date"] = "2026-01-01"
        ok(finding("PASS", guideline_source=src), AUDIT + "finding")


class TestProposedAction:
    """Proposed actions are drafts for a human; the status is fixed."""

    def action(self, **overrides):
        base = {"action_id": "PA-001", "action_type": "CLIENT_NEED", "target": "B-1", "description": "example",
                "rule_ids": ["PRE-EXAMPLE-001"], "evidence_ids": [], "approver_role": "LOAN_OFFICER",
                "status": "DRAFT_HUMAN_APPROVAL_REQUIRED"}
        base.update(overrides)
        return base

    def test_status_is_const(self):
        ok(self.action(), AUDIT + "proposed_action")
        bad(self.action(status="APPROVED"), AUDIT + "proposed_action", "/status")
        bad(self.action(status="EXECUTED"), AUDIT + "proposed_action", "/status")


# ----------------------------------------------------------------- calculation trail
class TestCalculationTrail:
    def test_requires_reproducibility_fields(self):
        trail = {"method": "salaried_monthly_base", "method_version": "1.0", "inputs": {"gross": "3250.00"},
                 "formula": "gross * 24 / 12", "intermediate_values": {}, "output": "6500.00", "warnings": []}
        ok(trail, COMMON + "calculation_trail")
        for key in ("method", "method_version", "inputs", "formula", "intermediate_values", "output", "warnings"):
            partial = {k: v for k, v in trail.items() if k != key}
            bad(partial, COMMON + "calculation_trail")

    def test_method_version_pattern(self):
        trail = {"method": "m", "method_version": "v1", "inputs": {}, "formula": "f", "intermediate_values": {},
                 "output": None, "warnings": []}
        bad(trail, COMMON + "calculation_trail", "/method_version")
