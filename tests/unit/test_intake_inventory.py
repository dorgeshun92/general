from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.common.masking import SSN_RE
from scripts.intake import classify as classify_mod
from scripts.intake import inventory
from scripts.intake.inventory import EXIT_OK, EXIT_STOP, EXIT_VALIDATION, IntakeStopCondition, OutputValidationError, main, run_intake
from scripts.validate_schema import validate_path
from tests.unit.test_intake_helpers import EDGE_CASES, approved_tmp, fixture_dir, tree_hashes  # noqa: F401 - fixture import

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
# Word-bounded so digit runs inside hex hashes (sha256 values) are not counted.
LONG_DIGITS_WORD_RE = re.compile(r"(?<![\w.,$])\d{8,}(?![\w.,])")


def _run(loan_id: str, out: Path, run_id: str = "t1"):
    return run_intake(fixture_dir(loan_id), run_id, out, now=NOW)


def _docs_by_path(result):
    return {d["relative_path"]: d for d in result.inventory["documents"]}


# ---- every fixture: valid outputs, untouched input, masked text ----------------------

@pytest.mark.parametrize("loan_id", EDGE_CASES)
def test_every_fixture_produces_valid_outputs(loan_id, tmp_path):
    result = _run(loan_id, tmp_path)
    run_dir = tmp_path / loan_id / "t1"
    assert result.run_dir == run_dir
    assert validate_path(run_dir / "document_inventory.json", "document_inventory") == []
    assert validate_path(run_dir / "loan_file.json", "loan_file") == []
    report = json.loads((run_dir / "intake_report.json").read_text())
    for name, digest in report["outputs"].items():
        assert (run_dir / name).is_file()
        assert digest == result.outputs[name]
    inv = json.loads((run_dir / "document_inventory.json").read_text())
    assert [d["document_id"] for d in inv["documents"]] == [f"DOC-{i:03d}" for i in range(1, len(inv["documents"]) + 1)]
    assert inv["documents"] == sorted(inv["documents"], key=lambda d: d["relative_path"])
    lf = json.loads((run_dir / "loan_file.json").read_text())
    assert lf["documents"] == inv["documents"]
    assert lf["evidence"] == [] and lf["contract"] is None and lf["borrowers"] == []
    assert all(f["value"] is None and f["status"] == "UNKNOWN" for f in lf["loan"].values())
    readable = [d["document_id"] for d in inv["documents"] if d["page_count"] is not None]
    for doc_id in readable:
        assert (run_dir / "extracted_text" / f"{doc_id}.json").is_file()


@pytest.mark.parametrize("loan_id", EDGE_CASES)
def test_input_tree_is_unchanged(loan_id, tmp_path):
    src = fixture_dir(loan_id)
    before = tree_hashes(src)
    mtimes = {p: p.stat().st_mtime_ns for p in src.rglob("*")}
    _run(loan_id, tmp_path)
    assert tree_hashes(src) == before
    assert {p: p.stat().st_mtime_ns for p in src.rglob("*")} == mtimes


@pytest.mark.parametrize("loan_id", EDGE_CASES)
def test_no_unmasked_pii_in_any_output(loan_id, tmp_path):
    _run(loan_id, tmp_path)
    for path in (tmp_path / loan_id / "t1").rglob("*.json"):
        data = json.loads(path.read_text())
        for s in inventory._strings(data):
            assert not SSN_RE.search(s), f"{path}: SSN pattern in {s[:60]!r}"
            assert not LONG_DIGITS_WORD_RE.search(s), f"{path}: 8+ digit run in {s[:60]!r}"


def test_outputs_are_deterministic(tmp_path):
    a = _run("LN-EDGE-CLEAN", tmp_path / "a")
    b = _run("LN-EDGE-CLEAN", tmp_path / "b")
    assert a.inventory == b.inventory and a.loan_file == b.loan_file and a.outputs == b.outputs


# ---- per-case behaviour --------------------------------------------------------------

def test_clean_package(tmp_path):
    result = _run("LN-EDGE-CLEAN", tmp_path)
    docs = _docs_by_path(result)
    assert {p: (d["document_type"], d["classification_confidence"]) for p, d in docs.items()} == {
        "01_urla_1003.pdf": ("URLA_1003", "HIGH"),
        "assets/bank_statement_2026-07.pdf": ("BANK_STATEMENT", "HIGH"),
        "contract/purchase_contract.pdf": ("PURCHASE_CONTRACT", "HIGH"),
        "income/paystub_2026-08.pdf": ("PAYSTUB", "HIGH"),
        "income/w2_2025.pdf": ("W2", "HIGH"),
    }
    assert all(d["status"] == "OK" for d in docs.values())
    assert result.inventory["duplicates"] == [] and result.inventory["unreadable"] == []
    assert result.inventory["totals"] == {"files": 5, "pages": 7, "bytes": sum(d["size_bytes"] for d in docs.values())}
    bank = docs["assets/bank_statement_2026-07.pdf"]
    assert bank["statement_period"] == {"start": "2026-07-01", "end": "2026-07-31"}
    assert bank["masked_account_numbers_found"] == ["****5678", "****4321"]
    assert bank["borrower_names_found"] == ["Test Borrower Clean"]
    assert docs["income/paystub_2026-08.pdf"]["document_date"] == "2026-08-20"
    # the fake numbers fire the PII heuristic; the manifest allowlists them -> review, not stop
    cats = [r["category"] for r in result.loan_file["review_items"]]
    assert cats == ["POSSIBLE_LIVE_PII"]
    assert result.loan_file["review_items"][0]["reviewer_role"] == "COMPLIANCE"
    assert "allowlisted" in bank["notes"]


def test_duplicates_both_types(tmp_path):
    result = _run("LN-EDGE-DUPLICATE", tmp_path)
    docs = _docs_by_path(result)
    assert result.inventory["duplicates"] == [
        {"document_id": "DOC-002", "duplicate_of": "DOC-001", "match_type": "IDENTICAL_HASH"},
        {"document_id": "DOC-003", "duplicate_of": "DOC-001", "match_type": "SAME_CONTENT_DIFFERENT_FILE"},
    ]
    assert docs["paystub_a.pdf"]["status"] == "OK" and docs["paystub_a.pdf"]["duplicate_of"] is None
    assert docs["paystub_b.pdf"]["status"] == "DUPLICATE" and docs["paystub_b.pdf"]["duplicate_of"] == "DOC-001"
    assert docs["paystub_c_resaved.pdf"]["status"] == "DUPLICATE"
    assert docs["paystub_a.pdf"]["sha256"] == docs["paystub_b.pdf"]["sha256"] != docs["paystub_c_resaved.pdf"]["sha256"]
    assert [r["category"] for r in result.loan_file["review_items"]] == ["DUPLICATE_DOCUMENT"] * 2


def test_unreadable_cases(tmp_path):
    result = _run("LN-EDGE-UNREADABLE", tmp_path)
    docs = _docs_by_path(result)
    reasons = {u["document_id"]: u["reason"] for u in result.inventory["unreadable"]}
    assert reasons == {docs["empty.pdf"]["document_id"]: "EMPTY", docs["garbage.pdf"]["document_id"]: "CORRUPT",
                       docs["notes.txt"]["document_id"]: "UNSUPPORTED_FORMAT",
                       docs["scan_no_text_layer.pdf"]["document_id"]: "NO_TEXT_LAYER"}
    assert docs["garbage.pdf"]["status"] == "UNREADABLE" and docs["garbage.pdf"]["page_count"] is None
    assert docs["scan_no_text_layer.pdf"]["status"] == "REVIEW" and docs["scan_no_text_layer.pdf"]["page_count"] == 2
    assert "extraction_method" not in docs["scan_no_text_layer.pdf"]
    cats = [r["category"] for r in result.loan_file["review_items"]]
    assert cats.count("UNREADABLE_DOCUMENT") == 3 and "LOW_CONFIDENCE_EXTRACTION" in cats
    text = json.loads((result.run_dir / "extracted_text" / f"{docs['scan_no_text_layer.pdf']['document_id']}.json").read_text())
    assert [p["method"] for p in text["pages"]] == ["NO_TEXT_LAYER", "NO_TEXT_LAYER"]


def test_encrypted_case(tmp_path, monkeypatch):
    import pypdf
    monkeypatch.setattr(pypdf.PdfReader, "decrypt", lambda self, *a, **k: pytest.fail("decrypt() must never be called"))
    result = _run("LN-EDGE-ENCRYPTED", tmp_path)
    docs = _docs_by_path(result)
    enc = docs["protected_bank_statement.pdf"]
    assert enc["status"] == "ENCRYPTED" and enc["page_count"] is None
    assert (enc["document_type"], enc["classification_confidence"]) == ("BANK_STATEMENT", "MEDIUM")  # filename only
    assert result.inventory["unreadable"] == [{"document_id": enc["document_id"], "reason": "ENCRYPTED",
                                               "detail": "PDF is encrypted; no password attempted"}]
    assert [r["category"] for r in result.loan_file["review_items"]] == ["ENCRYPTED_DOCUMENT"]
    assert docs["paystub.pdf"]["status"] == "OK"
    assert not (result.run_dir / "extracted_text" / f"{enc['document_id']}.json").exists()


def test_missing_pages_case(tmp_path):
    result = _run("LN-EDGE-MISSING-PAGES", tmp_path)
    doc = result.inventory["documents"][0]
    assert doc["status"] == "POSSIBLE_MISSING_PAGES" and doc["page_count"] == 2
    items = result.loan_file["review_items"]
    assert [r["category"] for r in items] == ["POSSIBLE_MISSING_PAGES"]
    assert "claims 3 pages" in items[0]["description"] and items[0]["document_ids"] == ["DOC-001"]


def test_conflicting_names_case(tmp_path):
    result = _run("LN-EDGE-CONFLICTING-NAMES", tmp_path)
    items = [r for r in result.loan_file["review_items"] if r["category"] == "CONFLICTING_IDENTITY"]
    assert len(items) == 1
    desc = items[0]["description"]
    assert "Test Borrower Alpha" in desc and "Test Borrower Alpha-Smith" in desc and "T. B. Alpha" in desc
    assert items[0]["document_ids"] == ["DOC-001", "DOC-002", "DOC-003"]


# ---- stop conditions and fail-closed -------------------------------------------------

def test_live_pii_without_allowlist_is_a_stop_condition(approved_tmp):
    root, copy = approved_tmp
    loan = copy("LN-EDGE-CLEAN")
    manifest = loan / "MANIFEST.yaml"
    manifest.write_text("\n".join(l for l in manifest.read_text().splitlines() if not l.startswith(("pii_pattern_allowlist", "  - "))) + "\n")
    out = root.parent / "out"
    with pytest.raises(IntakeStopCondition, match="live borrower PII") as exc:
        run_intake(loan, "t1", out, approved_roots=[root])
    assert "12345678" not in exc.value.reason and "87654321" not in exc.value.reason
    assert not out.exists()


def test_partial_allowlist_still_stops(approved_tmp):
    root, copy = approved_tmp
    loan = copy("LN-EDGE-CLEAN")
    manifest = loan / "MANIFEST.yaml"
    manifest.write_text(manifest.read_text().replace('  - "87654321"\n', ""))
    with pytest.raises(IntakeStopCondition, match="live borrower PII"):
        run_intake(loan, "t1", root.parent / "out", approved_roots=[root])


def test_manifest_problems_stop_before_any_write(approved_tmp):
    root, copy = approved_tmp
    loan = copy("LN-EDGE-CLEAN")
    (loan / "MANIFEST.yaml").write_text((loan / "MANIFEST.yaml").read_text().replace("deidentified: true", "deidentified: false"))
    out = root.parent / "out"
    with pytest.raises(IntakeStopCondition, match="deidentified"):
        run_intake(loan, "t1", out, approved_roots=[root])
    assert not out.exists()
    (loan / "MANIFEST.yaml").unlink()
    with pytest.raises(IntakeStopCondition, match="MANIFEST.yaml"):
        run_intake(loan, "t1", out, approved_roots=[root])


def test_symlinked_file_escape_stops(approved_tmp, tmp_path):
    root, copy = approved_tmp
    loan = copy("LN-EDGE-CLEAN")
    outside = tmp_path / "outside.pdf"
    shutil.copy(fixture_dir("LN-EDGE-CLEAN") / "income/w2_2025.pdf", outside)
    (loan / "linked.pdf").symlink_to(outside)
    with pytest.raises(IntakeStopCondition, match="symlink"):
        run_intake(loan, "t1", root.parent / "out", approved_roots=[root])


def test_output_inside_input_is_refused():
    with pytest.raises(IntakeStopCondition, match="inside the input directory"):
        run_intake(fixture_dir("LN-EDGE-CLEAN"), "t1", fixture_dir("LN-EDGE-CLEAN") / "out")


def test_bad_run_id_is_a_stop_condition(tmp_path):
    with pytest.raises(IntakeStopCondition, match="run id"):
        run_intake(fixture_dir("LN-EDGE-CLEAN"), "bad run id!", tmp_path)


def test_invalid_output_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "classify_document",
                        lambda filename, text: classify_mod.Classification("NOT_A_REAL_TYPE", "HIGH"))
    with pytest.raises(OutputValidationError) as exc:
        _run("LN-EDGE-CLEAN", tmp_path)
    run_dir = tmp_path / "LN-EDGE-CLEAN" / "t1"
    assert sorted(p.name for p in run_dir.iterdir()) == ["intake_error.json"]
    err = json.loads((run_dir / "intake_error.json").read_text())
    assert err["status"] == "FAILED_CLOSED"
    assert "document_inventory.json" in exc.value.errors and "loan_file.json" in exc.value.errors
    assert any("NOT_A_REAL_TYPE" in e for e in exc.value.errors["loan_file.json"])


def test_unmasked_output_fails_closed(tmp_path, monkeypatch):
    original = inventory._build_outputs

    def leaky(*args, **kwargs):
        inv, lf, texts = original(*args, **kwargs)
        lf["review_items"].append({"review_id": "RV-999", "category": "OTHER", "description": "SSN 123-45-6789 leaked",
                                   "document_ids": [], "reviewer_role": "PROCESSOR"})
        return inv, lf, texts

    monkeypatch.setattr(inventory, "_build_outputs", leaky)
    with pytest.raises(OutputValidationError) as exc:
        _run("LN-EDGE-CLEAN", tmp_path)
    assert any("unmasked" in e for e in exc.value.errors["loan_file.json"])
    assert sorted(p.name for p in (tmp_path / "LN-EDGE-CLEAN" / "t1").iterdir()) == ["intake_error.json"]


# ---- CLI exit codes -------------------------------------------------------------------

def test_cli_exit_ok(tmp_path, capsys):
    assert main([str(fixture_dir("LN-EDGE-CLEAN")), "--run-id", "cli-1", "--out", str(tmp_path)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "loan-file-intake complete" in out and "document_inventory.json" in out and "loan_file.json" in out
    assert (tmp_path / "LN-EDGE-CLEAN" / "cli-1" / "loan_file.json").is_file()


def test_cli_exit_validation_fail_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(inventory, "classify_document",
                        lambda filename, text: classify_mod.Classification("NOT_A_REAL_TYPE", "HIGH"))
    assert main([str(fixture_dir("LN-EDGE-CLEAN")), "--run-id", "cli-2", "--out", str(tmp_path)]) == EXIT_VALIDATION
    assert "FAILED CLOSED" in capsys.readouterr().err


def test_cli_exit_stop_condition(tmp_path, capsys):
    loan = tmp_path / "LN-OUTSIDE"
    loan.mkdir()
    (loan / "MANIFEST.yaml").write_text('loan_id: LN-OUTSIDE\ndeidentified: true\ndeidentified_by: "x"\ndeidentified_at: "2026-09-08"\n')
    assert main([str(loan), "--run-id", "cli-3", "--out", str(tmp_path / "out")]) == EXIT_STOP
    assert "STOP:" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()
