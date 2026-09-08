#!/usr/bin/env python3
"""loan-file-intake orchestrating CLI (deterministic part). Read-only on the input directory.

    python scripts/intake/inventory.py <loan-dir> --run-id <id> [--out output/audits]

Exit codes:
    0  outputs written and schema-valid
    1  unexpected internal error (traceback on stderr; nothing trustworthy was written)
    2  an output failed schema validation or the masking gate -> fail closed: only
       <out>/<loan_id>/<run_id>/intake_error.json is written
    3  stop condition (manifest missing/invalid, directory outside approved roots, symlink
       escape, output dir inside input dir, live-PII heuristic not allowlisted): nothing written

Outputs (under <out>/<loan_id>/<run_id>/):
    document_inventory.json      schema document_inventory
    loan_file.json               schema loan_file (skeleton: documents + review_items filled,
                                 every fact null/UNKNOWN, arrays empty, contract null)
    extracted_text/DOC-xxx.json  masked per-page text for every readable PDF
    intake_report.json           paths, sha256 of each output, and counts

The input directory is opened read-only: no file is created, renamed, or deleted under it.
Control files MANIFEST.yaml and README.md at the top level of the loan directory are not
inventoried as documents (the manifest is recorded via manifest_sha256).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pypdf  # noqa: E402

from scripts.common.hashing import sha256_file  # noqa: E402
from scripts.common.masking import contains_unmasked_pii, mask_text  # noqa: E402
from scripts.common.schema_registry import SchemaRegistryError, validate_document  # noqa: E402
from scripts.intake import classify as classify_mod  # noqa: E402
from scripts.intake.extract_text import METHOD_TEXT_LAYER, ExtractionResult, extract_pages  # noqa: E402
from scripts.intake.manifest import (  # noqa: E402
    LOAN_ID_RE,
    REPO_ROOT,
    IntakeStopCondition,
    Manifest,
    check_file_within,
    check_manifest,
)
from scripts.intake.pdf_pages import PdfProbe, probe_pdf  # noqa: E402

SKILL_NAME = "loan-file-intake"
INTAKE_VERSION = "1.0"
CONTROL_FILES = {"MANIFEST.yaml", "README.md"}  # top level of the loan directory only
EXIT_OK, EXIT_INTERNAL, EXIT_VALIDATION, EXIT_STOP = 0, 1, 2, 3


class OutputValidationError(Exception):
    """An output would be invalid; nothing but an error report is written."""

    def __init__(self, errors: dict[str, list[str]]):
        super().__init__("; ".join(f"{k}: {len(v)} error(s)" for k, v in errors.items()))
        self.errors = errors


def _null_fact() -> dict[str, Any]:
    return {"value": None, "evidence_ids": [], "confidence": "LOW", "status": "UNKNOWN"}


@dataclass
class DocRecord:
    document_id: str
    path: Path
    relative_path: str
    sha256: str
    size_bytes: int
    probe: PdfProbe
    extraction: ExtractionResult | None = None
    classification: classify_mod.Classification | None = None
    fields: classify_mod.ExtractedFields | None = None
    duplicate_of: str | None = None
    duplicate_type: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def filename(self) -> str:
        return self.path.name


@dataclass
class IntakeResult:
    loan_id: str
    run_id: str
    run_dir: Path
    outputs: dict[str, str]          # relative output name -> sha256
    inventory: dict[str, Any]
    loan_file: dict[str, Any]
    report_lines: list[str]


# --------------------------------------------------------------------------------------
# Helpers that tests may monkeypatch (kept as module-level functions on purpose)
# --------------------------------------------------------------------------------------

def classify_document(filename: str, first_page_text: str) -> classify_mod.Classification:
    return classify_mod.classify(filename, first_page_text)


def _list_input_files(loan_dir: Path) -> list[Path]:
    """Every regular file under loan_dir, sorted by relative POSIX path. Symlinked directories
    are refused (os.walk would silently skip their contents); symlinked files must resolve
    inside the loan directory."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(loan_dir):
        dirnames.sort()
        for d in dirnames:
            if (Path(dirpath) / d).is_symlink():
                raise IntakeStopCondition(f"symlinked directory {Path(dirpath) / d} inside the loan directory is refused")
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if Path(dirpath) == loan_dir and name in CONTROL_FILES:
                continue
            check_file_within(p, loan_dir)
            files.append(p)
    return sorted(files, key=lambda p: p.relative_to(loan_dir).as_posix())


def _display_path(path: Path) -> str:
    """Repo-relative POSIX path when inside the repository, else absolute."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _first_text_page(extraction: ExtractionResult) -> str:
    for page in extraction.pages:
        if page.method == METHOD_TEXT_LAYER:
            return page.text
    return ""


def _all_text(extraction: ExtractionResult) -> str:
    return "\n".join(p.text for p in extraction.pages if p.method == METHOD_TEXT_LAYER)


# --------------------------------------------------------------------------------------
# Core pipeline
# --------------------------------------------------------------------------------------

def _process_documents(loan_dir: Path, manifest: Manifest) -> list[DocRecord]:
    records: list[DocRecord] = []
    for index, path in enumerate(_list_input_files(loan_dir), 1):
        rel = path.relative_to(loan_dir).as_posix()
        rec = DocRecord(
            document_id=f"DOC-{index:03d}",
            path=path,
            relative_path=rel,
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            probe=probe_pdf(path),
        )
        if rec.probe.status == "OK":
            rec.extraction = extract_pages(path, manifest.pii_pattern_allowlist)
            live = [f for f in rec.extraction.pii if not f.allowlisted]
            if live:
                # Stop immediately: never continue past a possible live-PII directory.
                reasons = sorted({f.reason for f in live})
                raise IntakeStopCondition(
                    f"{rel} looks like it holds live borrower PII ({'; '.join(reasons)}). "
                    "Intake refuses to continue. If the values are synthetic, list the exact tokens under "
                    "pii_pattern_allowlist in MANIFEST.yaml."
                )
            allowlisted = sorted({f.masked_token for f in rec.extraction.pii})
            if allowlisted:
                rec.notes.append(
                    f"PII heuristic fired on token(s) {', '.join(allowlisted)}; allowlisted in MANIFEST.yaml"
                )
            rec.classification = classify_document(rec.filename, _first_text_page(rec.extraction))
            rec.fields = classify_mod.extract_fields(_all_text(rec.extraction))
            if rec.classification.note:
                rec.notes.append(rec.classification.note)
            if rec.extraction.possible_missing_pages:
                rec.notes.append(f"possible missing pages: {rec.extraction.missing_pages_detail}")
            missing_text = rec.extraction.pages_without_text
            if missing_text and rec.extraction.has_text:
                rec.notes.append(f"pages without a text layer (OCR not attempted): {missing_text}")
            elif missing_text:
                rec.notes.append("no text layer on any page (OCR not attempted)")
        else:
            # Unreadable files still get a filename-only classification attempt (MEDIUM at best).
            rec.classification = classify_document(rec.filename, "")
            rec.notes.append(f"{rec.probe.status}: {rec.probe.detail}")
        records.append(rec)
    return records


def _detect_duplicates(records: list[DocRecord]) -> list[dict[str, str]]:
    """IDENTICAL_HASH: same sha256. SAME_CONTENT_DIFFERENT_FILE: same normalized extracted text
    but different bytes (e.g. re-saved or re-printed copy). The earliest DOC id is canonical.
    Limitation: a re-scan of the same paper (OCR noise, different layout) is not detected."""
    duplicates: list[dict[str, str]] = []
    first_by_hash: dict[str, DocRecord] = {}
    first_by_content: dict[str, DocRecord] = {}
    for rec in records:
        canonical = first_by_hash.get(rec.sha256)
        if canonical is not None:
            rec.duplicate_of, rec.duplicate_type = canonical.document_id, "IDENTICAL_HASH"
        else:
            first_by_hash[rec.sha256] = rec
            fp = rec.extraction.content_sha256 if rec.extraction else None
            if fp:
                content_canonical = first_by_content.get(fp)
                if content_canonical is not None:
                    rec.duplicate_of, rec.duplicate_type = content_canonical.document_id, "SAME_CONTENT_DIFFERENT_FILE"
                else:
                    first_by_content[fp] = rec
        if rec.duplicate_of:
            duplicates.append({"document_id": rec.document_id, "duplicate_of": rec.duplicate_of,
                               "match_type": rec.duplicate_type})
    return duplicates


def _document_status(rec: DocRecord) -> str:
    if rec.probe.status == "ENCRYPTED":
        return "ENCRYPTED"
    if rec.probe.status != "OK":
        return "UNREADABLE"
    if rec.duplicate_of:
        return "DUPLICATE"
    assert rec.extraction is not None
    if rec.extraction.possible_missing_pages:
        return "POSSIBLE_MISSING_PAGES"
    if rec.extraction.pages_without_text:
        return "REVIEW"
    return "OK"


def _document_json(rec: DocRecord) -> dict[str, Any]:
    cls = rec.classification
    fields = rec.fields
    doc: dict[str, Any] = {
        "document_id": rec.document_id,
        "filename": rec.filename,
        "relative_path": rec.relative_path,
        "sha256": rec.sha256,
        "size_bytes": rec.size_bytes,
        "document_type": cls.document_type if cls else "UNKNOWN",
        "classification_confidence": cls.confidence if cls else "LOW",
        "page_count": rec.probe.page_count,
        "status": _document_status(rec),
        "duplicate_of": rec.duplicate_of,
        "document_date": fields.document_date if fields else None,
        "statement_period": fields.statement_period if fields else {"start": None, "end": None},
        "borrower_names_found": [mask_text(n) for n in fields.borrower_name_candidates] if fields else [],
        "masked_account_numbers_found": fields.masked_account_numbers if fields else [],
        "notes": mask_text("; ".join(rec.notes)),
    }
    if rec.extraction is not None and rec.extraction.has_text:
        doc["extraction_method"] = "TEXT_LAYER"
    return doc


def _unreadable_entries(records: list[DocRecord]) -> list[dict[str, str]]:
    out = []
    for rec in records:
        if rec.probe.status != "OK":
            out.append({"document_id": rec.document_id, "reason": rec.probe.status, "detail": rec.probe.detail})
        elif rec.extraction is not None and not rec.extraction.has_text:
            out.append({"document_id": rec.document_id, "reason": "NO_TEXT_LAYER",
                        "detail": "no extractable text on any page; OCR not attempted"})
    return out


def _conflicting_names(records: list[DocRecord]) -> tuple[list[str], list[str]]:
    """(distinct names, document ids carrying a name). More than one distinct normalized name
    across the package is reported as CONFLICTING_IDENTITY. Limitation: legitimate co-borrowers
    also trigger this; the reviewer decides."""
    seen: dict[str, str] = {}
    doc_ids: list[str] = []
    for rec in records:
        if not rec.fields or not rec.fields.borrower_name_candidates:
            continue
        doc_ids.append(rec.document_id)
        for name in rec.fields.borrower_name_candidates:
            seen.setdefault(classify_mod.normalize_name(name), name)
    names = list(seen.values())
    return (names if len(names) > 1 else []), doc_ids


def _review_items(records: list[DocRecord], duplicates: list[dict[str, str]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def add(category: str, description: str, doc_ids: list[str], role: str = "PROCESSOR") -> None:
        items.append({
            "review_id": f"RV-{len(items) + 1:03d}",
            "category": category,
            "description": mask_text(description),
            "document_ids": doc_ids,
            "reviewer_role": role,
        })

    for rec in records:
        rel = rec.relative_path
        if rec.probe.status == "ENCRYPTED":
            add("ENCRYPTED_DOCUMENT", f"{rel} is password-protected; no password was attempted. Obtain an unlocked copy.", [rec.document_id])
        elif rec.probe.status != "OK":
            add("UNREADABLE_DOCUMENT", f"{rel} is unreadable ({rec.probe.status}: {rec.probe.detail}).", [rec.document_id])
        else:
            ext = rec.extraction
            assert ext is not None
            if not ext.has_text:
                add("LOW_CONFIDENCE_EXTRACTION", f"{rel} has no text layer on any page; OCR was not attempted.", [rec.document_id])
            elif ext.pages_without_text:
                add("LOW_CONFIDENCE_EXTRACTION", f"{rel}: pages {ext.pages_without_text} have no text layer; OCR was not attempted.", [rec.document_id])
            if ext.possible_missing_pages:
                add("POSSIBLE_MISSING_PAGES", f"{rel}: {ext.missing_pages_detail}.", [rec.document_id])
            if rec.classification and rec.classification.document_type == "UNKNOWN":
                add("UNCLASSIFIED_DOCUMENT", f"{rel} matched no classification keyword; classify manually.", [rec.document_id])
            if ext.pii:
                tokens = sorted({f.masked_token for f in ext.pii})
                add("POSSIBLE_LIVE_PII", f"{rel}: PII heuristic fired on {', '.join(tokens)}; allowlisted in MANIFEST.yaml. Confirm the values are synthetic.", [rec.document_id], "COMPLIANCE")
    for dup in duplicates:
        add("DUPLICATE_DOCUMENT", f"{dup['document_id']} duplicates {dup['duplicate_of']} ({dup['match_type']}).", [dup["document_id"], dup["duplicate_of"]])
    names, doc_ids = _conflicting_names(records)
    if names:
        add("CONFLICTING_IDENTITY", "Borrower name candidates differ across documents: " + "; ".join(f"'{n}'" for n in names) + ". Confirm identity before relying on any of them.", doc_ids)
    return items


def _build_outputs(loan_dir: Path, manifest: Manifest, run_id: str, records: list[DocRecord],
                   duplicates: list[dict[str, str]], now: datetime) -> tuple[dict, dict, dict[str, dict]]:
    generated_at = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    documents = [_document_json(r) for r in records]
    input_dir = _display_path(loan_dir)
    inventory = {
        "schema_version": "1.0",
        "loan_id": manifest.loan_id,
        "run_id": run_id,
        "generated_at": generated_at,
        "input_directory": input_dir,
        "manifest_sha256": manifest.sha256,
        "documents": documents,
        "duplicates": duplicates,
        "unreadable": _unreadable_entries(records),
        "totals": {
            "files": len(records),
            "pages": sum(r.probe.page_count or 0 for r in records),
            "bytes": sum(r.size_bytes for r in records),
        },
    }
    loan_file = {
        "schema_version": "1.0",
        "loan_id": manifest.loan_id,
        "generated_at": generated_at,
        "generated_by": {"skill": SKILL_NAME, "run_id": run_id,
                         "tool_versions": {"pypdf": pypdf.__version__, "intake": INTAKE_VERSION}},
        "source": {"input_directory": input_dir, "deidentified": True, "manifest_sha256": manifest.sha256},
        "loan": {k: _null_fact() for k in ("purpose", "occupancy", "product_family", "lender", "loan_amount", "closing_date", "status")},
        "borrowers": [],
        "income": [],
        "assets": [],
        "liabilities": [],
        "properties": [],
        "contract": None,
        "documents": documents,
        "evidence": [],
        "review_items": _review_items(records, duplicates),
        "los_export": None,
    }
    texts: dict[str, dict] = {}
    for rec in records:
        if rec.extraction is None:
            continue
        texts[rec.document_id] = {
            "document_id": rec.document_id,
            "filename": rec.filename,
            "relative_path": rec.relative_path,
            "sha256": rec.sha256,
            "page_count": rec.extraction.page_count,
            "possible_missing_pages": rec.extraction.possible_missing_pages,
            "missing_pages_detail": rec.extraction.missing_pages_detail,
            "pages": [{"page": p.page, "text": p.text, "method": p.method, "chars": p.chars} for p in rec.extraction.pages],
            "masked": True,
        }
    return inventory, loan_file, texts


# --------------------------------------------------------------------------------------
# Validation gates
# --------------------------------------------------------------------------------------
_HASH_KEYS = {"sha256", "manifest_sha256"}


def _strings(obj: Any, skip_keys: set[str] = _HASH_KEYS) -> Iterable[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip_keys:
                continue
            yield from _strings(v, skip_keys)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, skip_keys)
    elif isinstance(obj, str):
        yield obj


def _masking_errors(obj: Any) -> list[str]:
    """Every string in an output (hash fields excluded) must be free of SSN / 8+ digit patterns."""
    errs = []
    for s in _strings(obj):
        hits = contains_unmasked_pii(s)
        if hits:
            errs.append(f"unmasked pattern ({', '.join(hits)}) in output string starting '{mask_text(s[:40])}'")
    return errs


def _integrity_errors(loan_file: dict) -> list[str]:
    """Same referential checks scripts/validate_schema.py applies (duplicated here so the gate
    does not depend on importing a CLI module)."""
    doc_ids = {d["document_id"] for d in loan_file["documents"]}
    errs = []
    if len(doc_ids) != len(loan_file["documents"]):
        errs.append("/documents: duplicate document_id values")
    for d in loan_file["documents"]:
        if d.get("duplicate_of") and d["duplicate_of"] not in doc_ids:
            errs.append(f"/documents/{d['document_id']}: duplicate_of {d['duplicate_of']} not in documents[]")
    for item in loan_file["review_items"]:
        for did in item["document_ids"]:
            if did not in doc_ids:
                errs.append(f"/review_items/{item['review_id']}: document_id {did} not in documents[]")
    return errs


def validate_outputs(inventory: dict, loan_file: dict, texts: dict[str, dict]) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = {}
    try:
        inv_errs = validate_document(inventory, "document_inventory") + _masking_errors(inventory)
        lf_errs = validate_document(loan_file, "loan_file") + _integrity_errors(loan_file) + _masking_errors(loan_file)
    except SchemaRegistryError as exc:
        return {"schema_registry": [str(exc)]}
    if inv_errs:
        errors["document_inventory.json"] = inv_errs
    if lf_errs:
        errors["loan_file.json"] = lf_errs
    for doc_id, payload in texts.items():
        m = _masking_errors(payload)
        if m:
            errors[f"extracted_text/{doc_id}.json"] = m
    return errors


# --------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------

def _write_json(path: Path, data: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return sha256_file(path)


def run_intake(loan_dir: Path, run_id: str, out_root: Path, approved_roots: Iterable[Path] | None = None,
               now: datetime | None = None) -> IntakeResult:
    """Run the full deterministic intake. Raises IntakeStopCondition (exit 3) or
    OutputValidationError (exit 2). ``approved_roots`` is for tests only; the CLI never sets it."""
    if not LOAN_ID_RE.match(run_id or ""):
        raise IntakeStopCondition(f"run id {run_id!r} must match {LOAN_ID_RE.pattern}")
    loan_dir_resolved, manifest = check_manifest(Path(loan_dir), approved_roots)
    out_root = Path(out_root)
    out_root_resolved = out_root.resolve()
    if out_root_resolved == loan_dir_resolved or loan_dir_resolved in out_root_resolved.parents:
        raise IntakeStopCondition(f"output directory {out_root} lies inside the input directory; refusing to write there")
    run_dir = out_root / manifest.loan_id / run_id

    records = _process_documents(loan_dir_resolved, manifest)
    duplicates = _detect_duplicates(records)
    inventory, loan_file, texts = _build_outputs(loan_dir_resolved, manifest, run_id, records, duplicates,
                                                 now or datetime.now(timezone.utc))

    errors = validate_outputs(inventory, loan_file, texts)
    if errors:
        _write_json(run_dir / "intake_error.json", {
            "loan_id": manifest.loan_id, "run_id": run_id, "status": "FAILED_CLOSED",
            "reason": "an output failed schema validation or the masking gate; nothing else was written",
            "errors": errors,
        })
        raise OutputValidationError(errors)

    outputs: dict[str, str] = {}
    outputs["document_inventory.json"] = _write_json(run_dir / "document_inventory.json", inventory)
    outputs["loan_file.json"] = _write_json(run_dir / "loan_file.json", loan_file)
    for doc_id, payload in texts.items():
        name = f"extracted_text/{doc_id}.json"
        outputs[name] = _write_json(run_dir / name, payload)

    report_lines = _report_lines(manifest, run_id, run_dir, inventory, loan_file, outputs)
    _write_json(run_dir / "intake_report.json", {
        "loan_id": manifest.loan_id, "run_id": run_id, "generated_at": inventory["generated_at"],
        "input_directory": inventory["input_directory"], "manifest_sha256": manifest.sha256,
        "run_dir": _display_path(run_dir), "outputs": outputs, "totals": inventory["totals"],
        "duplicates": len(duplicates), "unreadable": len(inventory["unreadable"]),
        "review_items": len(loan_file["review_items"]), "report": report_lines,
    })
    return IntakeResult(manifest.loan_id, run_id, run_dir, outputs, inventory, loan_file, report_lines)


def _report_lines(manifest: Manifest, run_id: str, run_dir: Path, inventory: dict, loan_file: dict,
                  outputs: dict[str, str]) -> list[str]:
    docs = inventory["documents"]
    lines = [
        f"loan-file-intake complete: loan {manifest.loan_id}, run {run_id}",
        f"  input: {inventory['input_directory']} (manifest sha256 {manifest.sha256[:12]}...)",
        f"  files: {inventory['totals']['files']}  pages: {inventory['totals']['pages']}  bytes: {inventory['totals']['bytes']}",
        "  documents:",
    ]
    for d in docs:
        lines.append(f"    {d['document_id']}  {d['status']:<22} {d['document_type']:<20} {d['classification_confidence']:<6} "
                     f"{'-' if d['page_count'] is None else d['page_count']:>3}p  {d['relative_path']}")
    lines.append(f"  duplicates: {len(inventory['duplicates'])}  unreadable: {len(inventory['unreadable'])}  "
                 f"review items: {len(loan_file['review_items'])}")
    for item in loan_file["review_items"]:
        lines.append(f"    {item['review_id']} {item['category']}: {item['description']}")
    lines.append(f"  outputs in {run_dir}:")
    for name, digest in outputs.items():
        lines.append(f"    {digest}  {name}")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("loan_dir", type=Path, help="loan package directory under an approved input root")
    ap.add_argument("--run-id", required=True, help="run identifier (letters, digits, . _ -)")
    ap.add_argument("--out", type=Path, default=Path("output/audits"), help="output root (default output/audits)")
    args = ap.parse_args(argv)
    try:
        result = run_intake(args.loan_dir, args.run_id, args.out)
    except IntakeStopCondition as exc:
        print(f"STOP: {exc.reason}", file=sys.stderr)
        return EXIT_STOP
    except OutputValidationError as exc:
        print("FAILED CLOSED: an output did not validate; only intake_error.json was written", file=sys.stderr)
        for name, errs in exc.errors.items():
            for e in errs:
                print(f"  {name}: {e}", file=sys.stderr)
        return EXIT_VALIDATION
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return EXIT_INTERNAL
    print("\n".join(result.report_lines))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
