"""Load the repository's JSON Schemas (Draft 2020-12) with cross-file $ref support.

Usage:
    from scripts.common.schema_registry import validate_document, SCHEMA_NAMES
    errors = validate_document(data, "loan_file")   # [] when valid

Every error is a plain string "<json-pointer>: <message>" so callers can print
or embed them without knowing about jsonschema. Fail closed: any loading or
validation exception propagates as SchemaRegistryError.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas"
SCHEMA_NAMES = ("loan_file", "audit_result", "document_inventory", "checklist_catalog")
_ID_PREFIX = "https://mpire.local/schemas/"


class SchemaRegistryError(RuntimeError):
    """Raised when a schema cannot be loaded. Callers must treat this as a failed validation."""


@lru_cache(maxsize=1)
def _registry() -> Registry:
    registry = Registry()
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        try:
            contents = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SchemaRegistryError(f"cannot load schema {path.name}: {exc}") from exc
        if "$id" not in contents:
            raise SchemaRegistryError(f"schema {path.name} has no $id")
        registry = registry.with_resource(contents["$id"], Resource.from_contents(contents))
    return registry


def schema_path(name: str) -> Path:
    if name not in SCHEMA_NAMES:
        raise SchemaRegistryError(f"unknown schema '{name}'; expected one of {SCHEMA_NAMES}")
    return SCHEMA_DIR / f"{name}.schema.json"


@lru_cache(maxsize=None)
def get_validator(name: str) -> Draft202012Validator:
    path = schema_path(name)
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except Exception as exc:  # noqa: BLE001 - fail closed on anything
        raise SchemaRegistryError(f"schema {path.name} is invalid: {exc}") from exc
    return Draft202012Validator(schema, registry=_registry(), format_checker=FormatChecker())


def validate_document(data: Any, name: str) -> list[str]:
    """Return a list of error strings (schema errors, else integrity errors). Empty list means valid."""
    validator = get_validator(name)
    errors = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        pointer = "/" + "/".join(str(p) for p in err.absolute_path)
        errors.append(f"{pointer}: {err.message}")
    if not errors:
        # Cross-reference checks JSON Schema cannot express (dangling ids, count mismatches, READY gating).
        from scripts.common.integrity import integrity_errors  # local import: keeps this module dependency-free

        errors = integrity_errors(data, name)
    return errors


def validate_ref(data: Any, ref: str) -> list[str]:
    """Validate against a sub-definition, e.g. validate_ref(x, 'common.defs.schema.json#/$defs/money_fact')."""
    validator = Draft202012Validator(
        {"$ref": _ID_PREFIX + ref}, registry=_registry(), format_checker=FormatChecker()
    )
    return [f"/{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in validator.iter_errors(data)]
