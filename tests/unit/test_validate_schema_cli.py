"""Exit-code contract of scripts/validate_schema.py: 0 valid, 1 invalid, 2 could not validate."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import validate_schema

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "tests" / "fixtures" / "examples"
VALID_LOAN = EXAMPLES / "loan_file.valid.json"
INVALID_LOAN = EXAMPLES / "loan_file.invalid.float_money.json"
VALID_CATALOG = EXAMPLES / "checklist_catalog.valid.yaml"
INVALID_CATALOG = EXAMPLES / "checklist_catalog.invalid.duplicate_id.yaml"


def run_main(*argv: str) -> int:
    return validate_schema.main([str(a) for a in argv])


def test_valid_file_exits_0_and_reports_valid(capsys):
    assert run_main(VALID_LOAN, "--schema", "loan_file") == 0
    out = capsys.readouterr()
    assert out.out.startswith("VALID:") and "loan_file" in out.out
    assert out.err == ""


def test_invalid_file_exits_1_and_lists_errors(capsys):
    assert run_main(INVALID_LOAN, "--schema", "loan_file") == 1
    out = capsys.readouterr().out
    assert out.startswith("INVALID:")
    assert "1 error(s)" in out
    assert "/loan/loan_amount/value" in out


def test_valid_file_against_wrong_schema_exits_1():
    assert run_main(VALID_LOAN, "--schema", "audit_result") == 1


def test_nonexistent_file_exits_2(tmp_path, capsys):
    missing = tmp_path / "does_not_exist.json"
    assert run_main(missing, "--schema", "loan_file") == 2
    err = capsys.readouterr().err
    assert err.startswith("ERROR: could not validate") and "does_not_exist.json" in err


def test_malformed_json_exits_2(tmp_path, capsys):
    bad = tmp_path / "broken.json"
    bad.write_text('{"schema_version": "1.0", ', encoding="utf-8")
    assert run_main(bad, "--schema", "loan_file") == 2
    assert "ERROR: could not validate" in capsys.readouterr().err


def test_malformed_yaml_exits_2(tmp_path, capsys):
    bad = tmp_path / "broken.yaml"
    bad.write_text("items: [unclosed\n  - id: PRE-X-001\nsources: {\n", encoding="utf-8")
    assert run_main(bad, "--schema", "checklist_catalog") == 2
    assert "ERROR: could not validate" in capsys.readouterr().err


def test_non_json_file_exits_2():
    """A file that is not JSON at all cannot be validated: fail closed with 2."""
    assert run_main(REPO / "pytest.ini", "--schema", "loan_file") == 2


def test_json_array_is_invalid_not_error(tmp_path):
    arr = tmp_path / "array.json"
    arr.write_text("[]", encoding="utf-8")
    assert run_main(arr, "--schema", "loan_file") == 1


def test_unknown_schema_name_is_rejected_by_argparse(capsys):
    with pytest.raises(SystemExit) as exc:
        run_main(VALID_LOAN, "--schema", "not_a_schema")
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_schema_flag_is_required():
    with pytest.raises(SystemExit) as exc:
        run_main(VALID_LOAN)
    assert exc.value.code == 2


def test_quiet_suppresses_output_but_keeps_exit_codes(capsys):
    assert run_main(VALID_LOAN, "--schema", "loan_file", "--quiet") == 0
    assert capsys.readouterr().out == ""
    assert run_main(INVALID_LOAN, "--schema", "loan_file", "--quiet") == 1
    assert capsys.readouterr().out == ""


def test_quiet_does_not_silence_fail_closed_errors(tmp_path, capsys):
    assert run_main(tmp_path / "nope.json", "--schema", "loan_file", "--quiet") == 2
    assert "ERROR" in capsys.readouterr().err


def test_yaml_valid_catalog_exits_0(capsys):
    assert run_main(VALID_CATALOG, "--schema", "checklist_catalog") == 0
    assert "VALID:" in capsys.readouterr().out


def test_yaml_invalid_catalog_exits_1(capsys):
    assert run_main(INVALID_CATALOG, "--schema", "checklist_catalog") == 1
    assert "duplicate ids" in capsys.readouterr().out


def test_yml_extension_is_treated_as_yaml(tmp_path):
    copy = tmp_path / "catalog.yml"
    copy.write_text(VALID_CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
    assert run_main(copy, "--schema", "checklist_catalog") == 0


def test_load_any_dispatches_on_suffix(tmp_path):
    y = tmp_path / "a.yaml"
    y.write_text("a: 1\nb: [x, y]\n", encoding="utf-8")
    j = tmp_path / "a.json"
    j.write_text('{"a": 1, "b": ["x", "y"]}', encoding="utf-8")
    assert validate_schema.load_any(y) == validate_schema.load_any(j) == {"a": 1, "b": ["x", "y"]}


def test_validate_path_runs_integrity_only_after_schema_passes():
    dangling = EXAMPLES / "loan_file.invalid.dangling_evidence_ref.json"
    errors = validate_schema.validate_path(dangling, "loan_file")
    assert errors == ["evidence_ids: 'EV-999' is referenced but not present in evidence[]"]


@pytest.mark.parametrize(
    "path,schema,expected",
    [(VALID_LOAN, "loan_file", 0), (INVALID_LOAN, "loan_file", 1), (VALID_CATALOG, "checklist_catalog", 0)],
    ids=["valid-json", "invalid-json", "valid-yaml"],
)
def test_cli_subprocess_exit_codes(path, schema, expected):
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "validate_schema.py"), str(path), "--schema", schema],
        cwd=REPO, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == expected, proc.stdout + proc.stderr


def test_cli_subprocess_missing_file_exits_2(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "validate_schema.py"), str(tmp_path / "x.json"), "--schema", "loan_file"],
        cwd=REPO, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2
    assert "ERROR" in proc.stderr
