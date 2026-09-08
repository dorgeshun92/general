from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.common.hashing import sha256_file
from scripts.intake.manifest import (
    IntakeStopCondition,
    check_approved_location,
    check_file_within,
    check_manifest,
    compute_manifest_sha256,
    load_manifest,
    looks_like_live_pii,
    pii_findings,
)
from tests.unit.test_intake_helpers import EDGE_CASES, fixture_dir

GOOD = 'loan_id: LN-T-1\ndeidentified: true\ndeidentified_by: "tester"\ndeidentified_at: "2026-09-08"\n'


def _loan(root: Path, text: str = GOOD, name: str = "LN-T-1") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "MANIFEST.yaml").write_text(text)
    return d


@pytest.mark.parametrize("loan_id", EDGE_CASES)
def test_fixture_manifests_are_accepted(loan_id):
    resolved, manifest = check_manifest(fixture_dir(loan_id))
    assert manifest.loan_id == loan_id
    assert manifest.deidentified is True
    assert manifest.deidentified_by == "fixture generator"
    assert manifest.deidentified_at == "2026-09-08"
    assert resolved.is_dir()


def test_clean_fixture_allowlists_its_fake_numbers():
    manifest = load_manifest(fixture_dir("LN-EDGE-CLEAN"))
    assert set(manifest.pii_pattern_allowlist) == {"12345678", "87654321"}


def test_manifest_sha256_matches_file():
    d = fixture_dir("LN-EDGE-CLEAN")
    assert compute_manifest_sha256(d) == sha256_file(d / "MANIFEST.yaml") == load_manifest(d).sha256


def test_missing_manifest_is_rejected(tmp_path):
    d = tmp_path / "LN-X"
    d.mkdir()
    with pytest.raises(IntakeStopCondition, match="MANIFEST.yaml"):
        load_manifest(d)


@pytest.mark.parametrize("bad, reason", [
    (GOOD.replace("deidentified: true", "deidentified: false"), "deidentified"),
    (GOOD.replace("deidentified: true", 'deidentified: "true"'), "deidentified"),
    (GOOD.replace("deidentified: true", "deidentified: yes"), "deidentified"),   # YAML 1.1 bool, but not literal
    (GOOD.replace("deidentified: true", "deidentified: True"), "deidentified"),  # must be lower-case true
    (GOOD.replace("loan_id: LN-T-1", "loan_id: bad id!"), "loan_id"),
    (GOOD.replace('deidentified_by: "tester"', 'deidentified_by: ""'), "deidentified_by"),
    (GOOD.replace('deidentified_at: "2026-09-08"', 'deidentified_at: "09/08/2026"'), "deidentified_at"),
    (GOOD.replace('deidentified_at: "2026-09-08"', 'deidentified_at: "2026-13-40"'), "deidentified_at"),
    ("just a string\n", "mapping"),
])
def test_bad_manifests_are_rejected(tmp_path, bad, reason):
    with pytest.raises(IntakeStopCondition, match=reason):
        load_manifest(_loan(tmp_path, bad))


def test_directory_outside_approved_root_is_rejected(tmp_path):
    d = _loan(tmp_path)
    with pytest.raises(IntakeStopCondition, match="approved"):
        check_manifest(d)


def test_approved_root_itself_is_not_a_loan_dir():
    with pytest.raises(IntakeStopCondition):
        check_approved_location(fixture_dir("LN-EDGE-CLEAN").parent)


def test_nonexistent_directory_is_rejected():
    with pytest.raises(IntakeStopCondition, match="does not exist"):
        check_approved_location(fixture_dir("LN-DOES-NOT-EXIST"))


def test_symlink_escape_is_rejected(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    real = _loan(tmp_path / "outside")
    link = approved / "LN-T-1"
    os.symlink(real, link, target_is_directory=True)
    with pytest.raises(IntakeStopCondition, match="not a loan directory under an approved"):
        check_manifest(link, approved_roots=[approved])
    # the same directory via a real path under the approved root is fine
    good = _loan(approved, name="LN-T-2")
    assert check_manifest(good, approved_roots=[approved])[1].loan_id == "LN-T-1"


def test_file_symlink_escape_is_rejected(tmp_path):
    loan = _loan(tmp_path / "approved")
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4\n")
    os.symlink(outside, loan / "linked.pdf")
    with pytest.raises(IntakeStopCondition, match="symlink"):
        check_file_within(loan / "linked.pdf", loan.resolve())
    inside = loan / "real.pdf"
    inside.write_bytes(b"%PDF-1.4\n")
    assert check_file_within(inside, loan.resolve()) == inside.resolve()


# ---- live-PII heuristic --------------------------------------------------------------

def test_ssn_pattern_is_flagged_and_masked():
    reasons = looks_like_live_pii("SSN 123-45-6789 on file")
    assert reasons and "***-**-6789" in reasons[0]
    assert "123-45" not in " ".join(reasons)


def test_bare_nine_digit_run_counts_as_ssn():
    assert any("SSN" in r for r in looks_like_live_pii("id 123456789"))


def test_single_long_run_is_tolerated_but_two_are_not():
    assert looks_like_live_pii("ref 12345678") == []
    reasons = looks_like_live_pii("acct 12345678 routing 87654321")
    assert reasons and "2 distinct" in reasons[0]
    assert "12345678" not in " ".join(reasons)


def test_allowlist_suppresses_exact_tokens_only():
    text = "acct 12345678 routing 87654321"
    assert looks_like_live_pii(text, ["12345678", "87654321"]) == []
    assert looks_like_live_pii(text, ["12345678"])  # strict: the other run still stops the run
    assert looks_like_live_pii("SSN 123-45-6789", ["123-45-6789"]) == []
    assert looks_like_live_pii("SSN 123-45-6789", ["123456789"])  # separators must match verbatim


def test_pii_findings_carry_allowlisted_flag():
    findings = pii_findings("acct 12345678 routing 87654321", ["12345678"])
    assert {f.masked_token: f.allowlisted for f in findings} == {"****5678": True, "****4321": False}


def test_masked_and_money_values_do_not_trigger():
    assert looks_like_live_pii("Account ****1234 balance $12,345,678.90 SSN ***-**-1234") == []
