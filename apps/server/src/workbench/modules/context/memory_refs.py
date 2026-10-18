"""Memory provenance survives bounded history excerpts without parsing arbitrary prose."""

import json

from workbench.contracts.errors import AppError


def context_dependencies(content: str) -> list[dict]:
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return []
    if not isinstance(value, dict) or value.get("error") is not None:
        return []
    items = value.get("context_memory_dependencies") or []
    if (
        not isinstance(items, list)
        or len(items) > 2048
        or any(not isinstance(item, dict) for item in items)
    ):
        raise AppError("history_reference_limit", "上下文来源记录过多或无效，请缩小读取范围。", 422)
    return items


def dependencies(content: str) -> list[dict]:
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return []
    if not isinstance(value, dict):
        return []
    if value.get("error") is not None:
        # A stale-reference error retains an audit ID, never the memory body.
        # Propagating that ID as a dependency would make a safe re-read stale forever.
        return []
    references = list(value.get("memory_dependencies") or [])
    if isinstance(value.get("memory_ref"), dict):
        references.append(value["memory_ref"])
    for group in value.get("memory_results", []):
        for key, identifier in (("hits", "document_id"), ("documents", "id")):
            references.extend(
                {"id": item[identifier], "version": item["version"]} for item in group.get(key, [])
            )
    unique = {(item["id"], item["version"]) for item in references}
    if len(unique) > 2048:
        raise AppError("history_reference_limit", "历史片段的记忆来源过多，请缩小读取范围。", 422)
    return [{"id": identifier, "version": version} for identifier, version in sorted(unique)]
