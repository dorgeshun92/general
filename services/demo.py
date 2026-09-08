"""Seed an in-memory repository with the schema example fixtures so the API, MCP server, and
dashboard can run without Supabase. Everything here is synthetic (LN-EXAMPLE-*), never an answer key."""
from __future__ import annotations

import json
from pathlib import Path

from services.config import REPO_ROOT
from services.models import Run
from services.repository import Repository
from services.run_loader import bundle_from_documents

EXAMPLES = REPO_ROOT / "tests" / "fixtures" / "examples"


def _load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def seed_demo(repo: Repository) -> list[Run]:
    """Three synthetic loans: one READY, one NOT_READY, one preapproval-only (no gate)."""
    loan_file = _load("loan_file.valid.json")
    inventory = _load("document_inventory.valid.json")
    ready = _load("audit_result.valid.submission_ready.json")
    not_ready = _load("audit_result.valid.submission_not_ready.json")
    pre = _load("audit_result.valid.preapproval.json")
    runs: list[Run] = []

    def _rekey(audit: dict, loan_id: str, run_id: str) -> dict:
        return {**audit, "loan_id": loan_id, "run_id": run_id}

    specs = [
        ("LN-EXAMPLE-0001", "RUN-DEMO-0001", [_rekey(pre, "LN-EXAMPLE-0001", "RUN-DEMO-0001"), _rekey(ready, "LN-EXAMPLE-0001", "RUN-DEMO-0001")],
         "Synthetic purchase file, submission gate READY (demo data)"),
        ("LN-EXAMPLE-0002", "RUN-DEMO-0002", [_rekey(not_ready, "LN-EXAMPLE-0002", "RUN-DEMO-0002")],
         "Synthetic purchase file with an open blocking MISSING item (demo data)"),
        ("LN-EXAMPLE-0003", "RUN-DEMO-0003", [_rekey(pre, "LN-EXAMPLE-0003", "RUN-DEMO-0003")],
         "Synthetic file with a preapproval audit only, no submission gate yet (demo data)"),
    ]
    for loan_id, run_id, audits, description in specs:
        manifest = {"schema_version": "1.0", "loan_id": loan_id, "run_id": run_id, "skill": "mortgage-file-audit",
                    "started_at": audits[-1]["generated_at"], "completed_at": audits[-1]["generated_at"],
                    "stop_condition": None, "completed_normally": True, "tool_versions": {"python": "3.11"},
                    "inputs": [], "outputs": [], "totals": {"inputs": 0, "outputs": 0, "input_bytes": 0, "output_bytes": 0}}
        report = f"# DEMO REPORT — {loan_id} / {run_id}\n\nDECISION SUPPORT ONLY — not a credit or compliance decision.\n\nSynthetic example data from tests/fixtures/examples.\n"
        bundle = bundle_from_documents(
            loan_id=loan_id, run_id=run_id,
            loan_file={**loan_file, "loan_id": loan_id}, inventory={**inventory, "loan_id": loan_id, "run_id": run_id},
            audits=audits, manifest=manifest, reports={"report.md": report}, loan_description=description,
            source_root=f"tests/fixtures/deidentified/{loan_id}",
        )
        runs.append(repo.upsert_run_bundle(bundle))
    return runs
