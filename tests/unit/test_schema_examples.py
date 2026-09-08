"""Every example fixture in tests/fixtures/examples validates (or fails) as documented.

Valid files must produce zero errors through both the pure JSON Schema layer
(validate_document) and the CLI layer (validate_path), which adds referential
integrity. Invalid files must produce at least one error through validate_path
and that error must point at the single documented defect.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.common.schema_registry import SCHEMA_NAMES, get_validator, validate_document
from scripts.validate_schema import integrity_errors, load_any, validate_path

EXAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "examples"

VALID_FILES = sorted(p for p in EXAMPLES.iterdir() if ".valid." in p.name)
INVALID_FILES = sorted(p for p in EXAMPLES.iterdir() if ".invalid." in p.name)

# filename -> substring that must appear in at least one error message.
EXPECTED_DEFECT = {
    "loan_file.invalid.float_money.json": "/loan/loan_amount/value",
    "loan_file.invalid.fact_without_evidence.json": "/loan/loan_amount/evidence_ids",
    "loan_file.invalid.dangling_evidence_ref.json": "'EV-999' is referenced but not present in evidence[]",
    "loan_file.invalid.unmasked_account.json": "/assets/0/account_number_masked",
    "audit_result.invalid.pass_without_evidence.json": "/findings/0/evidence_ids",
    "audit_result.invalid.missing_without_action.json": "/findings/2/proposed_action",
    "audit_result.invalid.review_without_reason.json": "/findings/3/review_reason",
    "audit_result.invalid.ready_with_open_blocking.json": "READY is not allowed; blocking finding F-002 is MISSING",
    "audit_result.invalid.count_mismatch.json": "/summary/counts/PASS",
    "audit_result.invalid.ready_without_los_export.json": "READY is not allowed without an LOS export",
    "document_inventory.invalid.bad_sha256.json": "/documents/0/sha256",
    "checklist_catalog.invalid.duplicate_id.yaml": "duplicate ids ['PRE-EXAMPLE-001']",
    "checklist_catalog.invalid.always_not_applicable.yaml": "applies_when 'always' cannot produce NOT_APPLICABLE",
}

# Defects that JSON Schema cannot express; only the integrity layer catches them.
INTEGRITY_ONLY = {
    "loan_file.invalid.dangling_evidence_ref.json",
    "audit_result.invalid.ready_with_open_blocking.json",
    "audit_result.invalid.count_mismatch.json",
    "audit_result.invalid.ready_without_los_export.json",
    "checklist_catalog.invalid.duplicate_id.yaml",
    "checklist_catalog.invalid.always_not_applicable.yaml",
}


def schema_name_for(path: Path) -> str:
    name = path.name.split(".", 1)[0]
    assert name in SCHEMA_NAMES, f"{path.name} does not start with a known schema name"
    return name


def _ids(paths):
    return [p.name for p in paths]


def test_fixture_directory_is_complete():
    assert VALID_FILES, "no *.valid.* fixtures found"
    assert INVALID_FILES, "no *.invalid.* fixtures found"
    covered = {schema_name_for(p) for p in VALID_FILES}
    assert covered == set(SCHEMA_NAMES), f"every schema needs a valid example; missing {set(SCHEMA_NAMES) - covered}"
    assert {p.name for p in INVALID_FILES} == set(EXPECTED_DEFECT), (
        "every invalid fixture must have a documented defect in EXPECTED_DEFECT, and vice versa"
    )


@pytest.mark.parametrize("path", VALID_FILES, ids=_ids(VALID_FILES))
def test_valid_example_passes_json_schema(path: Path):
    assert validate_document(load_any(path), schema_name_for(path)) == []


@pytest.mark.parametrize("path", VALID_FILES, ids=_ids(VALID_FILES))
def test_valid_example_passes_cli_validation(path: Path):
    assert validate_path(path, schema_name_for(path)) == []


@pytest.mark.parametrize("path", INVALID_FILES, ids=_ids(INVALID_FILES))
def test_invalid_example_is_rejected_with_documented_defect(path: Path):
    errors = validate_path(path, schema_name_for(path))
    assert errors, f"{path.name} validated cleanly but is supposed to carry a defect"
    expected = EXPECTED_DEFECT[path.name]
    assert any(expected in e for e in errors), f"expected an error mentioning {expected!r}; got {errors}"


@pytest.mark.parametrize("path", INVALID_FILES, ids=_ids(INVALID_FILES))
def test_invalid_example_has_exactly_one_defect(path: Path):
    """A copy of the valid file plus one mutation should surface one error, not a cascade."""
    errors = validate_path(path, schema_name_for(path))
    assert len(errors) == 1, errors


@pytest.mark.parametrize("path", sorted(p for p in INVALID_FILES if p.name in INTEGRITY_ONLY), ids=lambda p: p.name)
def test_integrity_only_defects_pass_bare_json_schema(path: Path):
    """Documents the split: these defects are invisible to bare JSON Schema and are caught by integrity_errors,
    which validate_document now runs after schema validation passes."""
    data = load_any(path)
    name = schema_name_for(path)
    assert get_validator(name).is_valid(data)
    assert integrity_errors(data, name)
    assert validate_document(data, name) == integrity_errors(data, name)


def test_valid_loan_file_shape():
    """The canonical example exercises the patterns the playbook calls for."""
    lf = load_any(EXAMPLES / "loan_file.valid.json")
    assert lf["loan_id"] == "LN-EXAMPLE-0001"
    assert 3 <= len(lf["documents"]) <= 5
    assert 6 <= len(lf["evidence"]) <= 10
    assert [b["full_name"]["value"] for b in lf["borrowers"]] == ["Test Borrower Alpha", "Test Borrower Beta"]
    assert lf["income"][0]["calculation"] is not None
    assert lf["assets"][0]["account_number_masked"].startswith("****")
    assert lf["assets"][0]["large_deposits"]
    assert len(lf["liabilities"]) == 2
    assert {p["role"] for p in lf["properties"]} == {"SUBJECT", "REO"}
    assert lf["contract"] is not None
    assert len(lf["review_items"]) == 1
    assert lf["los_export"] is None
    # honest-unknown pattern
    lender = lf["loan"]["lender"]
    assert lender["value"] is None and lender["status"] == "UNKNOWN" and lender["evidence_ids"] == []
    # every non-null fact under loan cites at least one evidence id
    for key, f in lf["loan"].items():
        if f["value"] is not None:
            assert f["evidence_ids"], f"loan.{key} has a value but no evidence"


def test_preapproval_example_covers_all_result_values():
    ar = load_any(EXAMPLES / "audit_result.valid.preapproval.json")
    assert ar["overall_status"] is None
    assert {f["result"] for f in ar["findings"]} == {"PASS", "FAIL", "MISSING", "REVIEW", "NOT_APPLICABLE"}
    assert ar["known_limitations"]


def test_submission_examples_status_matches_findings():
    ready = load_any(EXAMPLES / "audit_result.valid.submission_ready.json")
    assert ready["overall_status"] == "READY" and ready["los_export_present"] is True
    assert all(f["result"] in ("PASS", "NOT_APPLICABLE") and f["evidence_ids"] for f in ready["findings"] if f["blocking"])
    not_ready = load_any(EXAMPLES / "audit_result.valid.submission_not_ready.json")
    assert not_ready["overall_status"] == "NOT_READY"
    assert any(f["blocking"] and f["result"] == "MISSING" for f in not_ready["findings"])
    assert not_ready["missing_documents"]


def test_catalog_example_is_placeholder_only():
    """The repository rule: never invent mortgage guidelines. Example items must say so."""
    cat = load_any(EXAMPLES / "checklist_catalog.valid.yaml")
    assert all("not a real checklist" in s["title"] for s in cat["sources"])
    for item in cat["items"]:
        assert "not a checklist rule" in item["source_text"], item["id"]
        assert item["id"].split("-")[1] == "EXAMPLE", item["id"]
