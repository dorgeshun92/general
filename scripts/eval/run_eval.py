#!/usr/bin/env python3
"""Evaluation harness: score produced audits against human answer keys.

    python scripts/eval/run_eval.py --fixtures tests/fixtures/deidentified \
        --expected tests/expected --results output/audits \
        [--run] [--report output/eval/<timestamp>/] [--exact-evidence] \
        [--propose-answer-key-change <loan_id> <rule_id> "<reason>"] \
        [--proposals-dir output/eval/answer_key_proposals] [--include-self-test]

For every fixture directory under --fixtures that has a matching
<expected>/<loan_id>/expected_audit.json:
  1. (--run) invoke `claude -p "/mortgage-file-audit <dir>" --output-format json`
     when the claude binary is on PATH; otherwise compare-only mode.
  2. locate the latest run directory under <results>/<loan_id>/ (run_manifest
     timestamp, else directory mtime),
  3. validate every JSON output against its schema (reported, never fatal),
  4. compare the produced audit with the answer key (scripts/eval/compare.py),
  5. scan every text/JSON/markdown/YAML file in the run dir for unmasked PII,
  6. record runtime and token use from run_manifest.json metrics.

Answer keys of the form {"expected_stop_condition": "<REASON>"} mark designed
edge cases: the correct outcome is NO completed audit and that reason present
in run_manifest.json.

Exit codes: 0 all Section 10 targets met; 1 any target missed (or schema
failures, which fail closed); 2 harness error. This program never writes to
--expected. Proposed answer-key changes go to --proposals-dir for licensed review.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.common.masking import contains_unmasked_pii  # noqa: E402
from scripts.common.schema_registry import SchemaRegistryError, validate_document  # noqa: E402
from scripts.eval.compare import CompareError, compare  # noqa: E402
from scripts.validate_schema import integrity_errors  # noqa: E402

SCHEMA_FOR_FILE = {
    "document_inventory.json": "document_inventory",
    "loan_file.json": "loan_file",
    "preapproval_audit.json": "audit_result",
    "submission_readiness.json": "audit_result",
}
AUDIT_FILE_FOR_TYPE = {
    "SUBMISSION_READINESS": "submission_readiness.json",
    "PREAPPROVAL": "preapproval_audit.json",
}
AUDIT_FILES = tuple(AUDIT_FILE_FOR_TYPE.values())
PII_SCAN_SUFFIXES = {".json", ".md", ".txt", ".yaml", ".yml", ".log", ".csv"}
MANIFEST_TIMESTAMP_KEYS = ("completed_at", "finished_at", "generated_at", "started_at", "timestamp", "created_at")
STOP_REASON_KEYS = ("stop_condition", "stop_reason", "stopped_reason", "halt_reason", "status", "outcome")
NOT_REPORTED = "not reported"
# SHA-256 hex digests are provenance, not PII; a digest with 8+ consecutive digits would otherwise
# trip the account-number detector. They are blanked before scanning.
SHA256_HEX_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{64}(?![0-9a-fA-F])")

TARGETS = [
    # key, label, target text
    ("classification", "Document classification accuracy", ">= 98%"),
    ("coverage", "Checklist coverage", "100% of expected rules present in the produced audit"),
    ("false_pass", "False PASS rate on blocking items", "0 occurrences"),
    ("calculation", "Calculation agreement", "100% on expected calculation outputs"),
    ("evidence", "Evidence citation accuracy", ">= 99%"),
    ("pii", "PII leakage in reports/logs", "0 occurrences"),
    ("stop", "Correct stop/escalation behavior", "100% on designed edge cases"),
    ("schema", "Schema validity of produced outputs (fail closed)", "0 failures"),
]


class HarnessError(RuntimeError):
    """A problem with the harness inputs themselves (exit 2)."""


# ----------------------------------------------------------------------------- helpers

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _percent(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    return str((Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal("0.01")))


def _parse_timestamp(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_manifest(run_dir: Path) -> dict | None:
    path = run_dir / "run_manifest.json"
    if not path.is_file():
        return None
    try:
        data = _load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def manifest_timestamp(manifest: dict | None) -> float | None:
    if not manifest:
        return None
    for key in MANIFEST_TIMESTAMP_KEYS:
        ts = _parse_timestamp(manifest.get(key))
        if ts is not None:
            return ts
    return None


def latest_run_dir(results_dir: Path, loan_id: str) -> Path | None:
    """Newest run directory for a loan: by run_manifest timestamp, else directory mtime."""
    base = results_dir / loan_id
    if not base.is_dir():
        return None
    candidates = [p for p in base.iterdir() if p.is_dir()]
    if not candidates:
        return None

    def sort_key(p: Path) -> tuple[int, float, str]:
        ts = manifest_timestamp(read_manifest(p))
        if ts is not None:
            return (1, ts, p.name)
        return (0, p.stat().st_mtime, p.name)

    return max(candidates, key=sort_key)


def validate_run_dir(run_dir: Path) -> dict[str, list[str]]:
    """Validate every JSON file in a run dir. Returns {filename: [errors]} for failing files only."""
    failures: dict[str, list[str]] = {}
    for path in sorted(run_dir.rglob("*.json")):
        rel = str(path.relative_to(run_dir))
        try:
            data = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            failures[rel] = [f"unparseable JSON: {exc}"]
            continue
        schema = SCHEMA_FOR_FILE.get(path.name)
        if schema is None:
            continue
        try:
            errors = validate_document(data, schema)
            if not errors:
                errors = integrity_errors(data, schema)
        except SchemaRegistryError as exc:
            errors = [f"schema registry error: {exc}"]
        if errors:
            failures[rel] = errors
    return failures


def _allowlist_patterns(manifest: dict | None) -> tuple[set[str], list[re.Pattern]]:
    """MANIFEST.yaml pii_pattern_allowlist entries: pattern names exclude a detector category;
    anything else is a regex (or literal) removed from the text before scanning."""
    names: set[str] = set()
    regexes: list[re.Pattern] = []
    for entry in (manifest or {}).get("pii_pattern_allowlist") or []:
        if not isinstance(entry, str) or not entry:
            continue
        if entry in ("SSN-shaped value", "8+ digit run (possible account number)"):
            names.add(entry)
            continue
        try:
            regexes.append(re.compile(entry))
        except re.error:
            regexes.append(re.compile(re.escape(entry)))
    return names, regexes


def pii_scan(run_dir: Path, fixture_manifest: dict | None) -> list[dict]:
    """Run contains_unmasked_pii over every text-like file. Returns [{file, pattern}]."""
    excluded_names, regexes = _allowlist_patterns(fixture_manifest)
    hits: list[dict] = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in PII_SCAN_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            hits.append({"file": str(path.relative_to(run_dir)), "pattern": f"unreadable: {exc}"})
            continue
        text = SHA256_HEX_RE.sub(" ", text)
        for rx in regexes:
            text = rx.sub(" ", text)
        for pattern in contains_unmasked_pii(text):
            if pattern in excluded_names:
                continue
            hits.append({"file": str(path.relative_to(run_dir)), "pattern": pattern})
    return hits


def manifest_metrics(manifest: dict | None) -> dict[str, Any]:
    metrics = (manifest or {}).get("metrics") if isinstance((manifest or {}).get("metrics"), dict) else {}
    runtime = metrics.get("runtime_seconds", NOT_REPORTED)
    tokens = metrics.get("tokens", NOT_REPORTED)
    return {"runtime_seconds": runtime if runtime is not None else NOT_REPORTED,
            "tokens": tokens if tokens is not None else NOT_REPORTED}


def _walk_strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^A-Za-z0-9]+", text.upper()) if t}


def reason_matches(expected_reason: str, value: Any) -> bool:
    """True when every token of the expected reason appears in the manifest value.
    Lets ENCRYPTED_DOCUMENT match a free-text stop_condition like "document is encrypted"."""
    if not isinstance(value, str):
        return False
    want = _tokens(expected_reason)
    return bool(want) and want <= _tokens(value)


def check_stop_condition(run_dir: Path | None, expected_reason: str) -> dict[str, Any]:
    """Edge-case fixture: correct = no completed audit file + reason present in run_manifest.json.

    The reason is looked for in stop_condition / stop_reason / status / nested stop.* first, then in
    any string value of the manifest. `completed_normally: true` is reported as contradicting evidence."""
    result = {"expected_stop_condition": expected_reason, "run_dir": str(run_dir) if run_dir else None,
              "manifest_present": False, "reason_found": False, "completed_audit_files": [],
              "completed_normally": None, "correct": False, "detail": ""}
    if run_dir is None:
        result["detail"] = "no run directory found; cannot verify the stop reason"
        return result
    manifest = read_manifest(run_dir)
    if manifest is None:
        result["detail"] = "run_manifest.json missing or unparseable"
    else:
        result["manifest_present"] = True
        result["completed_normally"] = manifest.get("completed_normally")
        found = False
        for key in STOP_REASON_KEYS:
            value = manifest.get(key)
            if reason_matches(expected_reason, value):
                found = True
            elif isinstance(value, dict) and any(reason_matches(expected_reason, v) for v in value.values()):
                found = True
        if not found:
            found = any(reason_matches(expected_reason, s) for s in _walk_strings(manifest))
        result["reason_found"] = found
    completed = [name for name in AUDIT_FILES if (run_dir / name).is_file()]
    result["completed_audit_files"] = completed
    result["correct"] = bool(result["manifest_present"] and result["reason_found"] and not completed
                             and result["completed_normally"] is not True)
    if not result["detail"]:
        if not result["reason_found"]:
            result["detail"] = f"stop reason {expected_reason!r} not found in run_manifest.json"
        elif completed:
            result["detail"] = f"audit completed despite stop condition: {', '.join(completed)}"
        elif result["completed_normally"] is True:
            result["detail"] = "run_manifest.json says completed_normally: true despite the stop reason"
        else:
            result["detail"] = "stopped correctly"
    return result


def classify_documents(expected_docs: dict[str, str], run_dir: Path | None) -> dict[str, Any]:
    """expected_documents.json (filename -> document_type) vs document_inventory.json."""
    out = {"expected": len(expected_docs), "correct": 0, "mismatches": [], "accuracy_percent": None}
    inventory: dict[str, str] = {}
    if run_dir is not None and (run_dir / "document_inventory.json").is_file():
        try:
            inv = _load_json(run_dir / "document_inventory.json")
            for doc in inv.get("documents", []) if isinstance(inv, dict) else []:
                if isinstance(doc, dict) and isinstance(doc.get("filename"), str):
                    inventory[doc["filename"]] = doc.get("document_type")
                    if isinstance(doc.get("relative_path"), str):
                        inventory.setdefault(doc["relative_path"], doc.get("document_type"))
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    for filename, expected_type in sorted(expected_docs.items()):
        actual_type = inventory.get(filename, "ABSENT")
        if actual_type == expected_type:
            out["correct"] += 1
        else:
            out["mismatches"].append({"filename": filename, "expected": expected_type, "actual": actual_type})
    out["accuracy_percent"] = _percent(out["correct"], out["expected"])
    return out


def run_claude(fixture_dir: Path, log_dir: Path) -> dict[str, Any]:
    """Invoke the master skill through the claude CLI when available. Never raises."""
    binary = shutil.which("claude")
    if binary is None:
        return {"invoked": False, "note": "claude CLI not available; compare-only mode"}
    cmd = [binary, "-p", f"/mortgage-file-audit {fixture_dir}", "--output-format", "json"]
    log_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=str(REPO_ROOT))
    except (OSError, subprocess.SubprocessError) as exc:
        return {"invoked": False, "note": f"claude CLI invocation failed: {exc}; compare-only mode"}
    elapsed = time.monotonic() - started
    (log_dir / f"{fixture_dir.name}.claude.stdout.json").write_text(proc.stdout or "", encoding="utf-8")
    (log_dir / f"{fixture_dir.name}.claude.stderr.txt").write_text(proc.stderr or "", encoding="utf-8")
    return {"invoked": True, "command": " ".join(cmd), "exit_code": proc.returncode,
            "wall_seconds": str(Decimal(elapsed).quantize(Decimal("0.01")))}


# ----------------------------------------------------------------------------- per-fixture evaluation

def evaluate_fixture(loan_id: str, fixture_dir: Path, expected_dir: Path, results_dir: Path,
                     exact_evidence: bool, run_log_dir: Path | None) -> dict[str, Any]:
    record: dict[str, Any] = {"loan_id": loan_id, "fixture_dir": str(fixture_dir), "errors": [], "kind": "audit"}
    try:
        expected = _load_json(expected_dir / "expected_audit.json")
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"{loan_id}: cannot read expected_audit.json: {exc}") from exc
    if not isinstance(expected, dict):
        raise HarnessError(f"{loan_id}: expected_audit.json must be a JSON object")

    fixture_manifest = None
    manifest_path = fixture_dir / "MANIFEST.yaml"
    if manifest_path.is_file():
        try:
            fixture_manifest = _load_yaml(manifest_path)
        except yaml.YAMLError as exc:
            record["errors"].append(f"MANIFEST.yaml unparseable: {exc}")
    if not isinstance(fixture_manifest, dict):
        fixture_manifest = None

    if run_log_dir is not None:
        record["claude_run"] = run_claude(fixture_dir, run_log_dir)

    run_dir = latest_run_dir(results_dir, loan_id)
    record["run_dir"] = str(run_dir) if run_dir else None
    manifest = read_manifest(run_dir) if run_dir else None
    record["metrics"] = manifest_metrics(manifest)
    record["schema_failures"] = validate_run_dir(run_dir) if run_dir else {}
    record["pii_hits"] = pii_scan(run_dir, fixture_manifest) if run_dir else []

    stop_reason = expected.get("expected_stop_condition")
    if isinstance(stop_reason, str) and stop_reason:
        record["kind"] = "stop_condition"
        record["stop"] = check_stop_condition(run_dir, stop_reason)
        return record

    key_errors = validate_document(expected, "audit_result") or integrity_errors(expected, "audit_result")
    if key_errors:
        record["errors"].append("answer key is not a valid audit_result: " + "; ".join(key_errors[:5]))
        record["answer_key_invalid"] = True
        return record

    expected_docs_path = expected_dir / "expected_documents.json"
    if expected_docs_path.is_file():
        try:
            expected_docs = _load_json(expected_docs_path)
        except (OSError, json.JSONDecodeError) as exc:
            raise HarnessError(f"{loan_id}: cannot read expected_documents.json: {exc}") from exc
        if not isinstance(expected_docs, dict):
            raise HarnessError(f"{loan_id}: expected_documents.json must map filename -> document_type")
        record["classification"] = classify_documents(expected_docs, run_dir)

    audit_file = AUDIT_FILE_FOR_TYPE.get(expected.get("audit_type"), "submission_readiness.json")
    record["audit_file"] = audit_file
    actual = None
    if run_dir is not None and (run_dir / audit_file).is_file():
        try:
            actual = _load_json(run_dir / audit_file)
        except (OSError, json.JSONDecodeError) as exc:
            record["errors"].append(f"{audit_file} unparseable: {exc}")
    if run_dir is None:
        record["errors"].append("no run directory found under results")
    elif actual is None and not record["errors"]:
        record["errors"].append(f"{audit_file} not produced")

    if actual is None:
        # Treat the whole answer key as uncovered so the coverage target is missed, not silently skipped.
        actual = {"findings": [], "overall_status": None, "audit_type": None}
    try:
        record["comparison"] = compare(expected, actual, exact_evidence=exact_evidence)
    except CompareError as exc:
        record["errors"].append(f"comparison failed: {exc}")
        record["comparison"] = compare(expected, {"findings": []}, exact_evidence=exact_evidence)
    return record


# ----------------------------------------------------------------------------- aggregation

def aggregate(records: list[dict]) -> dict[str, Any]:
    agg: dict[str, Any] = {
        "fixtures_evaluated": len(records),
        "classification": {"expected": 0, "correct": 0},
        "coverage": {"expected_rules": 0, "covered_rules": 0},
        "false_pass": {"count": 0, "blocking_rules_expected": 0, "rules": []},
        "missed_blocking": [],
        "unsupported_claims": [],
        "calculation": {"checked": 0, "agreed": 0},
        "evidence": {"checked": 0, "accurate": 0},
        "pii": {"count": 0, "hits": []},
        "stop": {"designed": 0, "correct": 0},
        "schema": {"failures": 0, "files": []},
        "overall_status": {"total": 0, "agreed": 0},
        "result_agreement": {"total": 0, "agreed": 0},
        "extra_findings": 0,
        "answer_keys_invalid": [],
        "fixture_errors": {},
    }
    for r in records:
        lid = r["loan_id"]
        for fname in r.get("schema_failures", {}):
            agg["schema"]["failures"] += 1
            agg["schema"]["files"].append(f"{lid}: {fname}")
        for hit in r.get("pii_hits", []):
            agg["pii"]["count"] += 1
            agg["pii"]["hits"].append({"loan_id": lid, **hit})
        if r.get("errors"):
            agg["fixture_errors"][lid] = r["errors"]
        if r.get("answer_key_invalid"):
            agg["answer_keys_invalid"].append(lid)
        if r["kind"] == "stop_condition":
            agg["stop"]["designed"] += 1
            if r["stop"]["correct"]:
                agg["stop"]["correct"] += 1
            continue
        cls = r.get("classification")
        if cls:
            agg["classification"]["expected"] += cls["expected"]
            agg["classification"]["correct"] += cls["correct"]
        cmp_ = r.get("comparison")
        if not cmp_:
            continue
        agg["coverage"]["expected_rules"] += cmp_["coverage"]["expected_rules"]
        agg["coverage"]["covered_rules"] += cmp_["coverage"]["covered_rules"]
        agg["false_pass"]["count"] += len(cmp_["false_pass_blocking"])
        agg["false_pass"]["blocking_rules_expected"] += cmp_["blocking_rules_expected"]
        agg["false_pass"]["rules"] += [{"loan_id": lid, **fp} for fp in cmp_["false_pass_blocking"]]
        agg["missed_blocking"] += [{"loan_id": lid, **m} for m in cmp_["missed_blocking"]]
        agg["unsupported_claims"] += [{"loan_id": lid, **u} for u in cmp_["unsupported_claims"]]
        agg["calculation"]["checked"] += cmp_["calculations"]["checked"]
        agg["calculation"]["agreed"] += cmp_["calculations"]["agreed"]
        agg["evidence"]["checked"] += cmp_["evidence"]["checked"]
        agg["evidence"]["accurate"] += cmp_["evidence"]["accurate"]
        agg["overall_status"]["total"] += 1
        agg["overall_status"]["agreed"] += 1 if cmp_["overall_status"]["agree"] else 0
        agg["result_agreement"]["total"] += cmp_["result_agreement"]["total"]
        agg["result_agreement"]["agreed"] += cmp_["result_agreement"]["agreed"]
        agg["extra_findings"] += len(cmp_["extra_findings"])

    def status(observed: str | None, met: bool | None) -> dict:
        if met is None:
            return {"observed": observed or "not evaluated", "status": "NOT_EVALUATED"}
        return {"observed": observed, "status": "MET" if met else "MISSED"}

    c = agg["classification"]
    pct = _percent(c["correct"], c["expected"])
    agg["targets"] = {}
    agg["targets"]["classification"] = status(f"{pct}% ({c['correct']}/{c['expected']})" if pct else None,
                                              None if pct is None else Decimal(pct) >= 98)
    cov = agg["coverage"]
    pct = _percent(cov["covered_rules"], cov["expected_rules"])
    agg["targets"]["coverage"] = status(f"{pct}% ({cov['covered_rules']}/{cov['expected_rules']})" if pct else None,
                                        None if pct is None else Decimal(pct) == 100)
    fp = agg["false_pass"]
    rate = _percent(fp["count"], fp["blocking_rules_expected"])
    agg["targets"]["false_pass"] = status(
        f"{fp['count']} false PASS on {fp['blocking_rules_expected']} blocking rules" + (f" ({rate}%)" if rate else ""),
        None if agg["coverage"]["expected_rules"] == 0 else fp["count"] == 0)
    calc = agg["calculation"]
    pct = _percent(calc["agreed"], calc["checked"])
    agg["targets"]["calculation"] = status(f"{pct}% ({calc['agreed']}/{calc['checked']})" if pct else None,
                                           None if pct is None else Decimal(pct) == 100)
    ev = agg["evidence"]
    pct = _percent(ev["accurate"], ev["checked"])
    agg["targets"]["evidence"] = status(f"{pct}% ({ev['accurate']}/{ev['checked']})" if pct else None,
                                        None if pct is None else Decimal(pct) >= 99)
    agg["targets"]["pii"] = status(f"{agg['pii']['count']} occurrence(s)",
                                   None if not records else agg["pii"]["count"] == 0)
    st = agg["stop"]
    pct = _percent(st["correct"], st["designed"])
    agg["targets"]["stop"] = status(f"{pct}% ({st['correct']}/{st['designed']})" if pct else None,
                                    None if pct is None else Decimal(pct) == 100)
    agg["targets"]["schema"] = status(f"{agg['schema']['failures']} failing file(s)",
                                      None if not records else agg["schema"]["failures"] == 0)
    agg["all_targets_met"] = all(t["status"] != "MISSED" for t in agg["targets"].values()) \
        and not agg["answer_keys_invalid"]
    return agg


# ----------------------------------------------------------------------------- reporting

def _matrix_table(matrix: dict) -> str:
    cols = list(next(iter(matrix.values())).keys()) if matrix else []
    lines = ["| expected \\ actual | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for exp, row in matrix.items():
        lines.append(f"| {exp} | " + " | ".join(str(row[c]) for c in cols) + " |")
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    agg = report["aggregate"]
    out: list[str] = []
    out.append("# Evaluation report")
    out.append("")
    out.append(f"Generated: {report['generated_at']}  ")
    out.append(f"Fixtures: `{report['args']['fixtures']}`  Expected: `{report['args']['expected']}`  "
               f"Results: `{report['args']['results']}`  ")
    out.append(f"Mode: {report['mode']}  Evidence match: {'exact' if report['args']['exact_evidence'] else 'loose'}")
    out.append("")
    out.append(f"**Overall: {'ALL TARGETS MET' if agg['all_targets_met'] else 'TARGETS MISSED'}** "
               f"(exit code {report['exit_code']})")
    out.append("")
    out.append("## Section 10 targets")
    out.append("")
    out.append("| Metric | Target | Observed | Status |")
    out.append("|---|---|---|---|")
    for key, label, target in TARGETS:
        t = agg["targets"][key]
        out.append(f"| {label} | {target} | {t['observed']} | {t['status']} |")
    out.append("")
    out.append("## Aggregate findings")
    out.append("")
    out.append(f"- Fixtures evaluated: {agg['fixtures_evaluated']} "
               f"(skipped without answer key: {len(report['skipped_no_answer_key'])}, "
               f"answer keys without fixture: {len(report['expected_without_fixture'])}, "
               f"self-test fixtures skipped: {len(report['skipped_self_test'])})")
    out.append(f"- Per-rule result agreement: {agg['result_agreement']['agreed']}/{agg['result_agreement']['total']}")
    out.append(f"- Overall status agreement: {agg['overall_status']['agreed']}/{agg['overall_status']['total']}")
    out.append(f"- False PASS on blocking rules: {agg['false_pass']['count']}")
    out.append(f"- Missed blocking issues: {len(agg['missed_blocking'])}")
    out.append(f"- Unsupported claims (PASS/NOT_APPLICABLE without evidence): {len(agg['unsupported_claims'])}")
    out.append(f"- Extra findings not in answer keys: {agg['extra_findings']}")
    out.append(f"- Schema failures: {agg['schema']['failures']}")
    out.append(f"- PII leakage hits: {agg['pii']['count']}")
    if agg["answer_keys_invalid"]:
        out.append(f"- Answer keys that are not valid audit_results (fixture skipped): {', '.join(agg['answer_keys_invalid'])}")
    if agg["fixture_errors"]:
        out.append("- Fixture errors:")
        for lid, errs in agg["fixture_errors"].items():
            for e in errs:
                out.append(f"  - {lid}: {e}")
    out.append("")
    if report["answer_key_proposals"]:
        out.append("## Answer-key change proposals (pending licensed review)")
        out.append("")
        for p in report["answer_key_proposals"]:
            out.append(f"- {p['loan_id']} {p['rule_id']}: {p['reason']} -> `{p['file']}`")
        out.append("")

    out.append("## Per-fixture results")
    out.append("")
    for r in report["fixtures"]:
        out.append(f"### {r['loan_id']}")
        out.append("")
        out.append(f"- Run dir: `{r.get('run_dir') or 'none found'}`")
        m = r.get("metrics", {})
        out.append(f"- Runtime seconds: {m.get('runtime_seconds', NOT_REPORTED)}; tokens: {m.get('tokens', NOT_REPORTED)}")
        if r.get("claude_run"):
            cr = r["claude_run"]
            out.append(f"- Claude run: {cr.get('note') or ('exit ' + str(cr.get('exit_code')) + ', ' + str(cr.get('wall_seconds')) + 's')}")
        if r.get("errors"):
            for e in r["errors"]:
                out.append(f"- ERROR: {e}")
        if r.get("schema_failures"):
            out.append("- Schema failures:")
            for fname, errs in r["schema_failures"].items():
                out.append(f"  - `{fname}`: {len(errs)} error(s); first: {errs[0]}")
        else:
            out.append("- Schema failures: none")
        if r.get("pii_hits"):
            out.append("- PII leakage:")
            for h in r["pii_hits"]:
                out.append(f"  - `{h['file']}`: {h['pattern']}")
        else:
            out.append("- PII leakage: none")
        if r["kind"] == "stop_condition":
            s = r["stop"]
            out.append(f"- Designed edge case: expected stop `{s['expected_stop_condition']}` -> "
                       f"{'CORRECT' if s['correct'] else 'INCORRECT'} ({s['detail']})")
            out.append("")
            continue
        if r.get("classification"):
            c = r["classification"]
            out.append(f"- Document classification: {c['correct']}/{c['expected']} "
                       f"({c['accuracy_percent'] or 'n/a'}%)")
            for mm in c["mismatches"]:
                out.append(f"  - `{mm['filename']}`: expected {mm['expected']}, actual {mm['actual']}")
        cmp_ = r.get("comparison")
        if not cmp_:
            out.append("")
            continue
        out.append(f"- Overall status: expected {cmp_['overall_status']['expected']}, actual "
                   f"{cmp_['overall_status']['actual']} -> {'agree' if cmp_['overall_status']['agree'] else 'DISAGREE'}")
        out.append(f"- Coverage: {cmp_['coverage']['covered_rules']}/{cmp_['coverage']['expected_rules']} expected rules present")
        out.append("")
        out.append("Outcome matrix (rows expected, columns actual):")
        out.append("")
        out.append(_matrix_table(cmp_["outcome_matrix"]))
        out.append("")

        def _list(title: str, items: list, fmt) -> None:
            out.append(f"- {title}: {len(items)}")
            for it in items:
                out.append(f"  - {fmt(it)}")

        _list("False PASS on blocking rules", cmp_["false_pass_blocking"],
              lambda i: f"{i['rule_id']} expected {i['expected']}, actual PASS")
        _list("Missed blocking issues", cmp_["missed_blocking"],
              lambda i: f"{i['rule_id']} expected {i['expected']}, actual {i['actual']}")
        _list("Unsupported claims", cmp_["unsupported_claims"], lambda i: f"{i['rule_id']} {i['result']} without evidence")
        ev = cmp_["evidence"]
        out.append(f"- Evidence citation accuracy: {ev['accurate']}/{ev['checked']} ({ev['accuracy_percent'] or 'n/a'}%)")
        for i in ev["inaccurate"]:
            out.append(f"  - {i['rule_id']} expected {i['expected_evidence_ids']}, actual {i['actual_evidence_ids']} ({i['mode']})")
        _list("Numeric mismatches", cmp_["numeric_mismatches"],
              lambda i: f"{i['rule_id']} expected {i['expected_output']!r}, actual {i['actual_output']!r} ({i['reason']})")
        _list("Coverage gaps (expected rule absent)", cmp_["coverage_gaps"], lambda i: i)
        _list("Extra findings (not in answer key)", cmp_["extra_findings"], lambda i: i)
        if cmp_["duplicate_rule_ids"]["actual"]:
            out.append(f"- Duplicate rule_ids in produced audit: {cmp_['duplicate_rule_ids']['actual']}")
        out.append("")
    return "\n".join(out) + "\n"


def write_proposal(proposals_dir: Path, loan_id: str, rule_id: str, reason: str,
                   record: dict | None, generated_at: str) -> Path:
    """Append an answer-key change proposal for licensed review. Never touches tests/expected."""
    proposals_dir.mkdir(parents=True, exist_ok=True)
    path = proposals_dir / f"{loan_id}.md"
    rule = ((record or {}).get("comparison") or {}).get("rules", {}).get(rule_id)
    lines = []
    if not path.exists():
        lines += [f"# Answer-key change proposals — {loan_id}", "",
                  "Status of every entry: PENDING LICENSED REVIEW. The harness never edits tests/expected;",
                  "a licensed reviewer applies or rejects each proposal by hand.", ""]
    lines += [f"## Proposal {generated_at} — rule {rule_id}", "",
              f"- Loan: {loan_id}", f"- Rule: {rule_id}", f"- Reason given: {reason}"]
    if rule:
        lines += [f"- Expected result (answer key): {rule['expected']}; evidence {rule['expected_evidence_ids']}",
                  f"- Produced result: {rule['actual']}; evidence {rule['actual_evidence_ids']}"]
    else:
        lines += ["- Rule not present in the current comparison (no produced/expected finding available)."]
    lines += ["- Status: PENDING LICENSED REVIEW", "- Reviewer: ____________  Decision: ACCEPT / REJECT  Date: ________", ""]
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


# ----------------------------------------------------------------------------- main

def _discover(fixtures_dir: Path, expected_dir: Path, include_self_test: bool):
    fixtures = sorted(p for p in fixtures_dir.iterdir() if p.is_dir())
    expected_ids = {p.name for p in expected_dir.iterdir() if p.is_dir() and (p / "expected_audit.json").is_file()}
    to_eval, skipped_no_key, skipped_self_test = [], [], []
    for fx in fixtures:
        if fx.name not in expected_ids:
            skipped_no_key.append(fx.name)
            continue
        if not include_self_test and (fx / "MANIFEST.yaml").is_file():
            try:
                mf = _load_yaml(fx / "MANIFEST.yaml")
            except yaml.YAMLError:
                mf = None
            if isinstance(mf, dict) and mf.get("harness_self_test") is True:
                skipped_self_test.append(fx.name)
                continue
        to_eval.append(fx)
    fixture_names = {p.name for p in fixtures}
    expected_without_fixture = sorted(expected_ids - fixture_names)
    return to_eval, skipped_no_key, skipped_self_test, expected_without_fixture


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", type=Path, default=REPO_ROOT / "tests/fixtures/deidentified")
    ap.add_argument("--expected", type=Path, default=REPO_ROOT / "tests/expected")
    ap.add_argument("--results", type=Path, default=REPO_ROOT / "output/audits")
    ap.add_argument("--run", action="store_true", help="invoke the claude CLI for each fixture first")
    ap.add_argument("--report", type=Path, default=None, help="report directory (default output/eval/<timestamp>/)")
    ap.add_argument("--exact-evidence", action="store_true", help="evidence sets must match exactly")
    ap.add_argument("--propose-answer-key-change", nargs=3, action="append", default=[],
                    metavar=("LOAN_ID", "RULE_ID", "REASON"),
                    help="write a proposal for licensed review; never edits tests/expected")
    ap.add_argument("--proposals-dir", type=Path, default=REPO_ROOT / "output/eval/answer_key_proposals")
    ap.add_argument("--include-self-test", action="store_true",
                    help="also evaluate fixtures whose MANIFEST.yaml sets harness_self_test: true")
    args = ap.parse_args(argv)

    generated_at = _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    report_dir = args.report or (REPO_ROOT / "output/eval" / stamp)

    try:
        if not args.fixtures.is_dir():
            raise HarnessError(f"fixtures directory not found: {args.fixtures}")
        if not args.expected.is_dir():
            raise HarnessError(f"expected directory not found: {args.expected}")
        try:
            if report_dir.resolve().is_relative_to(args.expected.resolve()) or \
               args.proposals_dir.resolve().is_relative_to(args.expected.resolve()):
                raise HarnessError("report/proposals directories must not be inside --expected")
        except OSError as exc:
            raise HarnessError(str(exc)) from exc
        to_eval, skipped_no_key, skipped_self_test, expected_without_fixture = _discover(
            args.fixtures, args.expected, args.include_self_test)
        run_log_dir = (report_dir / "claude_runs") if args.run else None
        mode = "compare-only"
        if args.run:
            mode = "run + compare" if shutil.which("claude") else "compare-only (claude CLI not available)"
        records = [evaluate_fixture(fx.name, fx, args.expected / fx.name, args.results,
                                    args.exact_evidence, run_log_dir) for fx in to_eval]
        agg = aggregate(records)
        by_id = {r["loan_id"]: r for r in records}
        proposals = []
        for loan_id, rule_id, reason in args.propose_answer_key_change:
            path = write_proposal(args.proposals_dir, loan_id, rule_id, reason, by_id.get(loan_id), generated_at)
            proposals.append({"loan_id": loan_id, "rule_id": rule_id, "reason": reason, "file": str(path)})
        exit_code = 0 if agg["all_targets_met"] else 1
        report = {
            "generated_at": generated_at,
            "mode": mode,
            "args": {"fixtures": str(args.fixtures), "expected": str(args.expected), "results": str(args.results),
                     "run": args.run, "exact_evidence": args.exact_evidence, "report_dir": str(report_dir)},
            "exit_code": exit_code,
            "aggregate": agg,
            "fixtures": records,
            "skipped_no_answer_key": skipped_no_key,
            "skipped_self_test": skipped_self_test,
            "expected_without_fixture": expected_without_fixture,
            "answer_key_proposals": proposals,
        }
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "eval_report.json").write_text(json.dumps(report, indent=2, sort_keys=False), encoding="utf-8")
        (report_dir / "eval_report.md").write_text(render_markdown(report), encoding="utf-8")
    except HarnessError as exc:
        print(f"HARNESS ERROR: {exc}", file=sys.stderr)
        return 2
    except (OSError, SchemaRegistryError) as exc:
        print(f"HARNESS ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Evaluation report: {report_dir / 'eval_report.md'}")
    for key, label, _ in TARGETS:
        t = agg["targets"][key]
        print(f"  {t['status']:<13} {label}: {t['observed']}")
    print("RESULT: " + ("all targets met" if exit_code == 0 else "targets missed"))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
