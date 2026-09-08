from pathlib import Path

import pytest

from scripts.drafts.build_drafts import (
    ALL_FILES,
    DRAFT_LABEL,
    DraftRefused,
    DraftSafetyError,
    banned_phrase_hits,
    category_label,
    check_draft_text,
    main,
    write_drafts,
)
from test_audit_support import finding, make_audit, write_json


def _findings():
    return [
        finding("F-001", "PRE-EXAMPLE-001", "FAIL", explanation="The example pay statement covers only one week.", proposed_action="Provide your two most recent pay statements."),
        finding("F-002", "PRE-ASSETS-002", "MISSING", explanation="No example bank statement was found in the file.", proposed_action="Provide the last two months of statements for the account used for closing funds."),
        finding("F-003", "PRE-CONTRACT-003", "REVIEW", review_reason="Synthetic contract addendum is unsigned.", proposed_action="Ask the listing agent for the signed contract addendum."),
        finding("F-004", "PRE-EXAMPLE-004", "PASS"),
        finding("F-005", "PRE-EXAMPLE-005", "NOT_APPLICABLE"),
    ]


def _audit_path(tmp_path: Path, findings=None):
    return write_json(tmp_path / "audit_result.json", make_audit(findings or _findings(), audit_type="PREAPPROVAL", n_catalog=5))


def test_builds_all_four_drafts_with_labels_and_grouping(tmp_path: Path):
    written = write_drafts(_audit_path(tmp_path), ["F-001", "F-002", "F-003"], "Example Approver", "2026-09-01", tmp_path / "drafts")
    assert [p.name for p in written] == list(ALL_FILES)
    assert written[0].parent == tmp_path / "drafts" / "LN-SYNTH-0001" / "RUN-SYNTH-01"
    email = written[0].read_text(encoding="utf-8")
    assert email.startswith(DRAFT_LABEL)
    for p in written:
        assert p.read_text(encoding="utf-8").startswith(DRAFT_LABEL)
    assert "**Example**" in email and "**Bank and asset statements**" in email and "**Purchase contract**" in email
    assert "Why we need it: The example pay statement covers only one week." in email
    assert "Provide your two most recent pay statements." in email
    # no internal scoring exposed in borrower-facing text
    for token in ("HIGH", "MEDIUM", "LOW", "confidence", "blocking", "UNDERWRITER", "PRE-EXAMPLE-001"):
        assert token not in email.split("-->", 1)[1]
    agenda = written[1].read_text(encoding="utf-8")
    assert "Borrower call agenda" in agenda and "Ask: Provide your two most recent pay statements." in agenda
    note = written[2].read_text(encoding="utf-8")
    assert "DRAFT_HUMAN_APPROVAL_REQUIRED" in note and "F-001 / PRE-EXAMPLE-001 (FAIL)" in note and "Example Approver" in note
    follow_up = written[3].read_text(encoding="utf-8")
    assert "signed contract addendum" in follow_up and "Not applicable" not in follow_up


def test_agent_title_not_applicable_is_one_line(tmp_path: Path):
    written = write_drafts(_audit_path(tmp_path), ["F-001"], "Example Approver", "2026-09-01", tmp_path / "drafts")
    text = written[3].read_text(encoding="utf-8")
    assert text.startswith(DRAFT_LABEL) and "Not applicable" in text and len(text.strip().splitlines()) == 1


def test_refuses_unknown_ids_and_wrong_results(tmp_path: Path):
    path = _audit_path(tmp_path)
    with pytest.raises(DraftRefused, match="F-099"):
        write_drafts(path, ["F-001", "F-099"], "A", "2026-09-01", tmp_path / "drafts")
    with pytest.raises(DraftRefused, match="F-004 \\(PASS\\)"):
        write_drafts(path, ["F-004"], "A", "2026-09-01", tmp_path / "drafts")
    with pytest.raises(DraftRefused, match="NOT_APPLICABLE"):
        write_drafts(path, ["F-001", "F-005"], "A", "2026-09-01", tmp_path / "drafts")
    with pytest.raises(DraftRefused, match="no approved"):
        write_drafts(path, [], "A", "2026-09-01", tmp_path / "drafts")
    assert not (tmp_path / "drafts").exists()


def test_cli_refusals_and_approved_file(tmp_path: Path, capsys):
    path = _audit_path(tmp_path)
    out = tmp_path / "drafts"
    assert main([str(path), "--approved", "F-004", "--out", str(out)]) == 1
    assert "REFUSED" in capsys.readouterr().err
    assert not out.exists()
    bad_file = tmp_path / "approved.txt"
    bad_file.write_text("F-001\n", encoding="utf-8")  # no headers
    assert main([str(path), "--approved-file", str(bad_file), "--out", str(out)]) == 1
    assert not out.exists()
    good = tmp_path / "approved_ok.txt"
    good.write_text("# synthetic\napprover: Example Approver (Processor)\ndate: 2026-09-01\nF-001\nF-002, F-003\n", encoding="utf-8")
    assert main([str(path), "--approved-file", str(good), "--out", str(out)]) == 0
    assert "nothing has been sent" in capsys.readouterr().out
    assert sorted(p.name for p in (out / "LN-SYNTH-0001" / "RUN-SYNTH-01").iterdir()) == sorted(ALL_FILES)
    assert "Example Approver (Processor)" in (out / "LN-SYNTH-0001" / "RUN-SYNTH-01" / "post_call_los_note.md").read_text()


def test_banned_phrases_fail_closed(tmp_path: Path):
    f = finding("F-001", "PRE-EXAMPLE-001", "FAIL", explanation="Your loan is approved and guaranteed to fund.", proposed_action="Send the example statement.")
    path = _audit_path(tmp_path, [f])
    out = tmp_path / "drafts"
    with pytest.raises(DraftSafetyError, match="banned phrase"):
        write_drafts(path, ["F-001"], "A", "2026-09-01", out)
    assert not out.exists()
    assert main([str(path), "--approved", "F-001", "--out", str(out)]) == 2
    assert not out.exists()


@pytest.mark.parametrize("phrase", ["approved", "Guaranteed", "will close", "Pre-Approved", "preapproved"])
def test_banned_phrase_detector(phrase):
    assert banned_phrase_hits(f"Note: {phrase} today") == [phrase.lower()]


def test_banned_phrase_detector_allows_label_and_preapproval_noun():
    assert banned_phrase_hits(DRAFT_LABEL) == []
    assert banned_phrase_hits("your pre-approval letter and the approval process") == []


def test_check_draft_text_rules():
    assert check_draft_text("no label here", external=True)[0].startswith("first line must be")
    assert check_draft_text(DRAFT_LABEL + "\nplain text", external=True) == []
    assert any("banned" in p for p in check_draft_text(DRAFT_LABEL + "\nit is approved", external=True))
    assert check_draft_text(DRAFT_LABEL + "\nit is approved", external=False) == []
    assert any("PII" in p for p in check_draft_text(DRAFT_LABEL + "\nSSN 123-45-6789", external=False))


def test_check_cli_on_polished_files(tmp_path: Path, capsys):
    good = tmp_path / "document_request_email.md"
    good.write_text(DRAFT_LABEL + "\n\nHello, please send the example statement.\n", encoding="utf-8")
    assert main(["--check", str(good)]) == 0
    bad = tmp_path / "borrower_call_agenda.md"
    bad.write_text("Hello, you are approved.\n", encoding="utf-8")
    assert main(["--check", str(good), "--check", str(bad)]) == 1
    err = capsys.readouterr().out
    assert "first line must be" in err and "banned phrase" in err


def test_masking_applied_to_all_drafts(tmp_path: Path):
    f = finding("F-001", "PRE-EXAMPLE-001", "MISSING", explanation="Statement for account 12345678901 (SSN 123-45-6789) not found.", proposed_action="Send the statement for the example account.")
    written = write_drafts(_audit_path(tmp_path, [f]), ["F-001"], "A", "2026-09-01", tmp_path / "drafts")
    for p in written[:3]:
        text = p.read_text(encoding="utf-8")
        assert "12345678901" not in text and "123-45-6789" not in text
    assert "***-**-6789" in written[0].read_text(encoding="utf-8")


def test_never_writes_when_audit_invalid(tmp_path: Path):
    audit = make_audit(_findings(), audit_type="PREAPPROVAL", n_catalog=5)
    audit["overall_status"] = "READY"  # forbidden for PREAPPROVAL
    path = write_json(tmp_path / "audit_result.json", audit)
    out = tmp_path / "drafts"
    assert main([str(path), "--approved", "F-001", "--out", str(out)]) == 2
    assert not out.exists()
    with pytest.raises(DraftSafetyError):
        write_drafts(path, ["F-001"], "A", "2026-09-01", out)


def test_category_labels():
    assert category_label("PRE-INCOME-REGULAR-003") == "Income (salary or hourly)"
    assert category_label("SUB-TITLE-001") == "Title"
    assert category_label("PRE-EXAMPLE-001") == "Example"
    assert category_label("junk") == "Other items"
