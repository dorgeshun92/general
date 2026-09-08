from __future__ import annotations

import json

import pytest

from scripts.intake.pdf_pages import main, probe_pdf, probe_tree
from tests.unit.test_intake_helpers import fixture_dir, tree_hashes


@pytest.mark.parametrize("loan_id, rel, status, pages", [
    ("LN-EDGE-CLEAN", "01_urla_1003.pdf", "OK", 2),
    ("LN-EDGE-CLEAN", "assets/bank_statement_2026-07.pdf", "OK", 2),
    ("LN-EDGE-CLEAN", "income/paystub_2026-08.pdf", "OK", 1),
    ("LN-EDGE-ENCRYPTED", "protected_bank_statement.pdf", "ENCRYPTED", None),
    ("LN-EDGE-ENCRYPTED", "paystub.pdf", "OK", 1),
    ("LN-EDGE-UNREADABLE", "garbage.pdf", "CORRUPT", None),
    ("LN-EDGE-UNREADABLE", "empty.pdf", "EMPTY", None),
    ("LN-EDGE-UNREADABLE", "notes.txt", "UNSUPPORTED_FORMAT", None),
    ("LN-EDGE-UNREADABLE", "scan_no_text_layer.pdf", "OK", 2),
    ("LN-EDGE-MISSING-PAGES", "bank_statement_partial.pdf", "OK", 2),
    ("LN-EDGE-CLEAN", "MANIFEST.yaml", "UNSUPPORTED_FORMAT", None),
])
def test_probe_statuses(loan_id, rel, status, pages):
    probe = probe_pdf(fixture_dir(loan_id) / rel)
    assert probe.status == status
    assert probe.page_count == pages
    assert probe.size_bytes == (fixture_dir(loan_id) / rel).stat().st_size


def test_encrypted_probe_never_decrypts(monkeypatch):
    import pypdf
    monkeypatch.setattr(pypdf.PdfReader, "decrypt", lambda self, *a, **k: pytest.fail("decrypt() must not be called"))
    assert probe_pdf(fixture_dir("LN-EDGE-ENCRYPTED") / "protected_bank_statement.pdf").status == "ENCRYPTED"


def test_probe_tree_is_sorted_and_read_only():
    root = fixture_dir("LN-EDGE-UNREADABLE")
    before = tree_hashes(root)
    probes = probe_tree(root)
    assert [p.path for p in probes] == sorted(p.path for p in probes)
    assert tree_hashes(root) == before


def test_cli_json(capsys):
    assert main([str(fixture_dir("LN-EDGE-UNREADABLE")), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {d["status"] for d in data} >= {"CORRUPT", "EMPTY", "UNSUPPORTED_FORMAT", "OK"}


def test_cli_missing_path():
    assert main(["/definitely/not/here"]) == 2
