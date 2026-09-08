"""SHA-256 helpers. Used for run manifests, input provenance, and duplicate detection."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_CHUNK = 1024 * 1024


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(obj: Any) -> str:
    """Hash of the canonical JSON serialization (sorted keys, no whitespace)."""
    return sha256_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
