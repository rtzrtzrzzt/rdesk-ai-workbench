"""Local-only JSON Schema validation; server schemas must never fetch URLs."""

import hashlib
import json
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry


def json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def bounded(value: Any, maximum: int) -> None:
    if len(json_bytes(value)) > maximum:
        raise ValueError("MCP value exceeds the configured byte limit")


def check_schema(schema: dict, maximum: int = 65536) -> Draft202012Validator:
    bounded(schema, maximum)

    def visit(value: Any, depth: int = 0):
        if depth > 32:
            raise ValueError("MCP schema nesting exceeds 32")
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    not isinstance(child, str) or not child.startswith("#")
                ):
                    raise ValueError("External schema references are unsupported")
                visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                visit(child, depth + 1)

    visit(schema)
    if schema.get("$schema", "https://json-schema.org/draft/2020-12/schema") not in {
        "https://json-schema.org/draft/2020-12/schema",
        "https://json-schema.org/draft/2020-12/schema#",
    }:
        raise ValueError("This adapter requires JSON Schema draft 2020-12")
    Draft202012Validator.check_schema(schema)
    # Empty registry has no network retrieval function, even if a reference was missed.
    return Draft202012Validator(schema, registry=Registry())


def validate(schema: dict, instance: Any, maximum: int) -> None:
    bounded(instance, maximum)
    check_schema(schema).validate(instance)
