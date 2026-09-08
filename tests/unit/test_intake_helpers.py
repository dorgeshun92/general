"""Shared helpers and fixtures for the test_intake_* modules (imported explicitly; not a conftest). Fixture loans live under the approved input root, so
most tests run on them directly; tmp copies use the approved_roots override that the CLI
never exposes."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "deidentified"
EDGE_CASES = ["LN-EDGE-CLEAN", "LN-EDGE-DUPLICATE", "LN-EDGE-UNREADABLE", "LN-EDGE-ENCRYPTED",
              "LN-EDGE-MISSING-PAGES", "LN-EDGE-CONFLICTING-NAMES"]


def fixture_dir(loan_id: str) -> Path:
    return FIXTURE_ROOT / loan_id


def tree_hashes(root: Path) -> dict[str, str]:
    from scripts.common.hashing import sha256_file
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def approved_tmp(tmp_path: Path):
    """A temporary approved input root plus a helper to copy a fixture loan into it."""
    root = tmp_path / "approved"
    root.mkdir()

    def copy(loan_id: str, new_id: str | None = None) -> Path:
        dest = root / (new_id or loan_id)
        shutil.copytree(fixture_dir(loan_id), dest)
        return dest

    return root, copy
