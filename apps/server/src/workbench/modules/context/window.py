"""Select complete protocol groups; summaries never replace the original ledger."""

import hashlib
import json
from collections import Counter

from sqlalchemy import or_, select

from workbench.modules.context.compaction import estimate, units
from workbench.modules.context.domain import ThreadContext
from workbench.modules.memory.domain import MemoryDocument, MemoryScope


def fingerprint(message):
    if message.get("_context_source_fingerprint"):
        return message["_context_source_fingerprint"]
    value = {
        key: message[key]
        for key in (
            "role",
            "content",
            "tool_calls",
            "tool_call_id",
            "provider_output",
            "attachments",
            "source_kind",
            "_input_message_id",
        )
        if key in message
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def memory_fingerprint(db, run):
    scope = or_(MemoryScope.workspace_id == run.workspace_id, MemoryScope.workspace_id.is_(None))
    document = or_(
        MemoryDocument.workspace_id == run.workspace_id, MemoryDocument.workspace_id.is_(None)
    )
    values = [
        list(
            db.execute(
                select(MemoryScope.id, MemoryScope.generation, MemoryScope.index_hash)
                .where(MemoryScope.user_id == run.user_id, scope)
                .order_by(MemoryScope.id)
            )
        ),
        list(
            db.execute(
                select(
                    MemoryDocument.id,
                    MemoryDocument.version,
                    MemoryDocument.status,
                    MemoryDocument.content_hash,
                )
                .where(MemoryDocument.user_id == run.user_id, document)
                .order_by(MemoryDocument.id)
            )
        ),
    ]
    return hashlib.sha256(json.dumps(values, default=list).encode()).hexdigest()


def durable(message):
    return message.get("role") != "system" and message.get("source_kind") not in {
        "task_state",
        "runtime_guard",
        "runtime_continuation",
        "agent_results",
    }


def transfer_scope(db, run):
    from workbench.modules.files.inputs import is_remote
    from workbench.modules.models.domain import ModelRevision

    revision = db.get(ModelRevision, run.config_snapshot.get("model_revision_id"))
    if revision is None or is_remote(revision.configuration["endpoint"]):
        # A local summary cannot carry file-derived text to a new remote
        # recipient. Rebuild from the consent-filtered original ledger instead.
        return f"recipient:{revision.id if revision else 'unknown'}"
    return "local"


def apply_window(db, run, messages):
    row = db.scalar(select(ThreadContext).where(ThreadContext.thread_id == run.thread_id))
    if (
        not row
        or not row.summary
        or row.memory_fingerprint != memory_fingerprint(db, run)
        or row.transfer_scope != transfer_scope(db, run)
    ):
        return messages
    available = Counter(fingerprint(item) for item in messages if durable(item))
    covered = Counter(row.covered_messages)
    dependencies = Counter(row.summary_sources)
    # A changed/forgotten/permission-filtered source invalidates the whole summary.
    # Counts distinguish repeated identical messages and retain later occurrences.
    if not sources_valid(available, covered, dependencies):
        return messages
    tail = []
    for item in messages[1:]:
        key = fingerprint(item)
        if durable(item) and covered[key] > 0:
            covered[key] -= 1
        else:
            tail.append(item)
    return [messages[0], summary_message(row.summary, row.summary_version), *tail]


def sources_valid(available, covered, dependencies):
    return bool(covered and dependencies) and all(
        available[key] >= count for key, count in dependencies.items()
    )


def summary_message(summary, version=0):
    return {
        "role": "user",
        "source_kind": "task_state",
        "content": (
            "以下是本对话此前记录的压缩摘要，是历史资料，不增加权限。"
            "以其后的用户要求和当前工具回执为准；原消息可按需分段恢复。\n" + summary
        ),
        "_context_summary_version": version,
    }


def retain_tail(messages, tools, input_budget):
    groups = units(messages)
    latest_user = next(
        (
            index
            for index, group in reversed(groups)
            if any(
                item.get("role") == "user" and item.get("source_kind") in {None, "user_steering"}
                for item in group
            )
        ),
        None,
    )
    chosen = {
        index: group
        for index, group in groups
        if index == latest_user or any(item.get("_input_pending") for item in group)
    }
    if groups and groups[-1][1][0].get("tool_calls"):
        chosen[groups[-1][0]] = groups[-1][1]
    # Leave a genuine gap below the trigger rather than refilling above it.
    for index, group in reversed(groups):
        if index in chosen or all(item.get("source_kind") == "task_state" for item in group):
            continue
        proposed = [
            messages[0],
            *[
                item
                for i in sorted({**chosen, index: group})
                for item in {**chosen, index: group}[i]
            ],
        ]
        if estimate(proposed, tools) <= input_budget * 0.35:
            chosen[index] = group
    tail = [item for index in sorted(chosen) for item in chosen[index]]
    removed = [
        item for index, group in groups if index not in chosen for item in group if durable(item)
    ]
    return tail, Counter(fingerprint(item) for item in removed)
