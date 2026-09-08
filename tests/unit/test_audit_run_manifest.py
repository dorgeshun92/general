import json
from pathlib import Path

import pytest

from scripts.audit.run_manifest import ManifestError, main, verify_run_manifest, write_run_manifest


def _setup(tmp_path: Path):
    inp = tmp_path / "in" / "example.pdf"
    inp.parent.mkdir()
    inp.write_bytes(b"%PDF synthetic")
    run_dir = tmp_path / "output" / "audits" / "LN-SYNTH-0001" / "RUN-1"
    run_dir.mkdir(parents=True)
    out = run_dir / "loan_file.json"
    out.write_text("{}", encoding="utf-8")
    return inp, run_dir, out


def test_write_and_verify(tmp_path: Path):
    inp, run_dir, out = _setup(tmp_path)
    path = write_run_manifest(run_dir, [inp], [out], "loan-file-intake", "RUN-1", "LN-SYNTH-0001", None)
    data = json.loads(path.read_text())
    assert data["loan_id"] == "LN-SYNTH-0001" and data["run_id"] == "RUN-1" and data["skill"] == "loan-file-intake"
    assert data["stop_condition"] is None and data["completed_normally"] is True
    assert data["tool_versions"]["python"] and data["tool_versions"]["jsonschema"]
    assert data["inputs"][0]["size_bytes"] == len(b"%PDF synthetic") and len(data["inputs"][0]["sha256"]) == 64
    assert data["outputs"][0]["size_bytes"] == 2
    assert data["completed_at"].endswith("Z")
    report = verify_run_manifest(run_dir)
    assert report["ok"] and report["checked"] == 2 and report["mismatches"] == []


def test_verify_detects_modified_and_missing_files(tmp_path: Path):
    inp, run_dir, out = _setup(tmp_path)
    write_run_manifest(run_dir, [inp], [out], "loan-file-intake", "RUN-1", "LN-SYNTH-0001", None)
    out.write_text('{"tampered": true}', encoding="utf-8")
    inp.unlink()
    report = verify_run_manifest(run_dir)
    assert not report["ok"]
    reasons = {m["path"].split("/")[-1]: m["reason"] for m in report["mismatches"]}
    assert reasons == {"loan_file.json": "HASH_MISMATCH", "example.pdf": "MISSING"}


def test_stop_condition_recorded_and_manifest_excluded_from_outputs(tmp_path: Path):
    inp, run_dir, out = _setup(tmp_path)
    path = write_run_manifest(run_dir, [inp], [out, run_dir / "run_manifest.json"], "mortgage-file-audit", "RUN-1", "LN-SYNTH-0001", "schema validation failed")
    data = json.loads(path.read_text())
    assert data["stop_condition"] == "schema validation failed" and data["completed_normally"] is False
    assert [o["path"].split("/")[-1] for o in data["outputs"]] == ["loan_file.json"]


def test_write_fails_closed_when_listed_file_is_missing(tmp_path: Path):
    inp, run_dir, out = _setup(tmp_path)
    with pytest.raises(ManifestError):
        write_run_manifest(run_dir, [inp, tmp_path / "nope.pdf"], [out], "s", "RUN-1", "LN-SYNTH-0001", None)
    assert not (run_dir / "run_manifest.json").exists()


def test_verify_fails_closed_without_manifest(tmp_path: Path):
    with pytest.raises(ManifestError):
        verify_run_manifest(tmp_path)


def test_cli_write_verify_and_mismatch(tmp_path: Path, capsys):
    inp, run_dir, out = _setup(tmp_path)
    assert main(["write", "--run-dir", str(run_dir), "--skill", "s", "--run-id", "RUN-1", "--loan-id", "LN-SYNTH-0001", "--input", str(inp), "--output", str(out)]) == 0
    assert main(["verify", str(run_dir)]) == 0
    assert "VERIFIED" in capsys.readouterr().out
    out.write_text("changed", encoding="utf-8")
    assert main(["verify", str(run_dir)]) == 1
    assert "HASH_MISMATCH" in capsys.readouterr().out
    assert main(["verify", str(tmp_path / "empty")]) == 2
