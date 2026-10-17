"""Scoped Markdown memory with durable intents; files and PostgreSQL are not one transaction."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import new_id, now
from workbench.modules import workspace_service
from workbench.modules.identity import User
from workbench.modules.tools.filesystem import authorized_path, read_text

from . import dto, files, revisionfiles
from .domain import (
    MemoryDocument,
    MemoryProposal,
    MemoryRevision,
    MemoryScope,
    MemoryTombstone,
    MemoryWriteJournal,
)

UNRESOLVED = {"prepared", "published", "outcome_unknown"}
MAX_DOCUMENTS = 500


def _authorize(db: Session, user_id: str, workspace_id: str):
    try:
        UUID(user_id)
        UUID(workspace_id)
    except ValueError:
        raise AppError("memory_scope_denied", "记忆所属用户或工作区无效。", 403, "policy") from None
    if db.get(User, user_id) is None:
        raise AppError("memory_scope_denied", "用户不存在或不可访问。", 403, "policy")
    return workspace_service.get(db, user_id, workspace_id)


def _key(user_id: str, workspace_id: str, scope: str) -> str:
    if scope not in {"user", "workspace"}:
        raise AppError("invalid_memory_scope", "记忆范围必须是用户或工作区。")
    return f"user:{user_id}" if scope == "user" else f"workspace:{workspace_id}"


def _root(user_id: str, workspace_id: str, scope: str) -> str:
    return f"users/{user_id}/memory" if scope == "user" else f"workspaces/{workspace_id}/memory"


def _scope(db: Session, user_id: str, workspace_id: str, scope: str, *, create=False):
    key = _key(user_id, workspace_id, scope)
    row = db.scalar(
        select(MemoryScope).where(MemoryScope.scope_key == key, MemoryScope.user_id == user_id)
    )
    if row is None and create:
        row = MemoryScope(
            id=new_id(),
            user_id=user_id,
            workspace_id=workspace_id if scope == "workspace" else None,
            scope=scope,
            scope_key=key,
            generation=1,
        )
        db.add(row)
        db.flush()
    return row


def _document(db: Session, user_id: str, workspace_id: str, document_id: str):
    row = db.scalar(
        select(MemoryDocument).where(
            MemoryDocument.id == document_id, MemoryDocument.user_id == user_id
        )
    )
    if row is None or row.workspace_id not in {None, workspace_id}:
        raise AppError("memory_not_found", "记忆不存在或不属于当前范围。", 404)
    return row


def _proposal(db: Session, user_id: str, workspace_id: str, proposal_id: str):
    row = db.scalar(
        select(MemoryProposal).where(
            MemoryProposal.id == proposal_id, MemoryProposal.user_id == user_id
        )
    )
    if row is None or row.workspace_id not in {None, workspace_id}:
        raise AppError("memory_proposal_not_found", "记忆候选不存在或不属于当前范围。", 404)
    return row


def _document_view(db: Session, row: MemoryDocument) -> dto.Document:
    scope = db.get(MemoryScope, row.scope_id)
    status, body, version, title, refs = (
        row.status,
        None,
        row.content_hash or "missing",
        row.title,
        row.source_refs,
    )
    if status != "forgotten":
        raw, version = files.read(row.storage_path)
        if raw is None:
            status = "missing"
        else:
            try:
                metadata, body = files.decode(raw, row.id)
                title, refs = metadata["title"], metadata["source_refs"]
                status = "active"
            except AppError:
                status, body = "conflict", raw.decode("utf-8")
        pending = db.scalar(
            select(MemoryWriteJournal.id)
            .where(
                MemoryWriteJournal.document_id == row.id, MemoryWriteJournal.status.in_(UNRESOLVED)
            )
            .limit(1)
        )
        if pending or row.status == "publishing":
            status = "outcome_unknown"
        elif row.status == "conflict":
            status = "conflict"
    return dto.Document(
        id=row.id,
        scope=scope.scope,
        workspace_id=row.workspace_id,
        title=title,
        path=row.storage_path,
        version=version,
        body=body,
        status=status,
        source_refs=refs,
        updated_at=row.updated_at,
        scope_generation=scope.generation,
    )


def _notice(row: MemoryWriteJournal) -> dto.WriteNotice:
    message = {
        "prepared": "已保存写入意图，发布结果待核对。",
        "published": "文件已发布，元数据待确认。",
        "outcome_unknown": "文件与数据库结果尚未完成对账。",
        "conflict": "检测到冲突，未自动覆盖外部内容。",
        "not_applied": "真实文件仍为原版本，本次变更未应用。",
        "committed": "已根据真实文件确认完成。",
    }
    return dto.WriteNotice(
        id=row.id,
        operation=row.operation,
        document_id=row.document_id,
        status=row.status,
        created_at=row.created_at,
        message=message.get(row.status, "请核对写入记录。"),
    )


def list_documents(
    db: Session, user_id: str, workspace_id: str, scope: dto.Scope
) -> dto.DocumentList:
    _authorize(db, user_id, workspace_id)
    owner = _scope(db, user_id, workspace_id, scope)
    documents, pending = [], []
    if owner:
        rows = db.scalars(
            select(MemoryDocument)
            .where(MemoryDocument.scope_id == owner.id, MemoryDocument.status != "forgotten")
            .order_by(MemoryDocument.created_at)
            .limit(MAX_DOCUMENTS)
        ).all()
        documents = [
            dto.DocumentSummary.model_validate(_document_view(db, row).model_dump(exclude={"body"}))
            for row in rows
        ]
        pending = [
            _notice(row)
            for row in db.scalars(
                select(MemoryWriteJournal)
                .where(
                    MemoryWriteJournal.scope_id == owner.id,
                    MemoryWriteJournal.status.in_(UNRESOLVED | {"conflict"}),
                )
                .order_by(MemoryWriteJournal.created_at)
                .limit(100)
            )
        ]
    return dto.DocumentList(
        scope=scope,
        workspace_id=workspace_id if scope == "workspace" else None,
        scope_generation=owner.generation if owner else 1,
        documents=documents,
        pending_writes=pending,
    )


def read_document(db: Session, user_id: str, workspace_id: str, document_id: str) -> dto.Document:
    _authorize(db, user_id, workspace_id)
    return _document_view(db, _document(db, user_id, workspace_id, document_id))


def search(
    db: Session, user_id: str, workspace_id: str, query: str, scope: dto.Scope
) -> dto.SearchResult:
    _authorize(db, user_id, workspace_id)
    query = query.strip()
    if not query or len(query) > 500:
        raise AppError("invalid_memory_query", "请提供 1 到 500 字的查询。")
    owner = _scope(db, user_id, workspace_id, scope)
    generation = owner.generation if owner else 1
    rows = (
        list(
            db.scalars(
                select(MemoryDocument)
                .where(MemoryDocument.scope_id == owner.id, MemoryDocument.status != "forgotten")
                .order_by(MemoryDocument.created_at)
                .limit(MAX_DOCUMENTS)
            )
        )
        if owner
        else []
    )
    hits, truncated, consumed = [], False, 0
    started = time.monotonic()
    if owner:
        index, _ = files.read(f"{_root(user_id, workspace_id, scope)}/MEMORY.md")
        consumed = len(index) if index else 0
    for row in rows:
        path = files.private_path(row.storage_path)
        if path.is_file():
            consumed += path.stat().st_size
        if consumed > 16 * 1024 * 1024 or time.monotonic() - started > 5:
            truncated = True
            break
        document = _document_view(db, row)
        if document.status != "active" or document.body is None:
            continue
        # Line numbers refer to actual MD: metadata + blank line precede the body.
        lines = document.body.splitlines()
        matched = [i for i, line in enumerate(lines) if query.casefold() in line.casefold()]
        if not matched and query.casefold() in document.title.casefold():
            hits.append(
                dto.SearchHit(
                    document_id=document.id,
                    title=document.title,
                    path=document.path,
                    version=document.version,
                    scope=document.scope,
                    workspace_id=document.workspace_id,
                    start_line=1,
                    end_line=1,
                    text=document.title,
                    source_refs=document.source_refs,
                )
            )
        for index in matched[:5]:
            start, end = max(0, index - 1), min(len(lines), index + 2)
            excerpt = "\n".join(lines[start:end])
            match_offset = max(0, excerpt.casefold().find(query.casefold()) - 400)
            snippet = excerpt[match_offset : match_offset + 1800]
            hits.append(
                dto.SearchHit(
                    document_id=document.id,
                    title=document.title,
                    path=document.path,
                    version=document.version,
                    scope=document.scope,
                    workspace_id=document.workspace_id,
                    start_line=start + 3,
                    end_line=max(start + 3, end + 2),
                    text=snippet,
                    source_refs=document.source_refs,
                )
            )
            if len(hits) >= 50:
                truncated = True
                break
        if len(matched) > 5:
            truncated = True
        if len(hits) >= 50:
            break
    return dto.SearchResult(
        scope=scope,
        workspace_id=workspace_id if scope == "workspace" else None,
        scope_generation=generation,
        query=query,
        hits=hits,
        truncated=truncated,
    )


@contextmanager
def _writer(db: Session, user_id: str, workspace_id: str, scope: str, request_id: str):
    keys = sorted(
        {
            int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], signed=True)
            for value in (
                f"memory-scope:{_key(user_id, workspace_id, scope)}",
                f"memory-request:{user_id}:{request_id}",
            )
        }
    )
    bind = db.get_bind()
    with bind.connect() as connection:
        locked = []
        try:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            for key in keys:
                connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
                locked.append(key)
            connection.commit()
            with Session(connection, expire_on_commit=False) as owned:
                yield owned
        finally:
            already_failing = sys.exc_info()[0] is not None
            try:
                connection.rollback()
                for key in reversed(locked):
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                connection.commit()
            except Exception:
                # A pooled connection must never retain an unaccounted session lock.
                connection.invalidate()
                if not already_failing:
                    raise AppError(
                        "memory_lock_cleanup",
                        "写入连接已关闭，请通过相同请求标识核对结果。",
                        503,
                        "dependency",
                        True,
                    ) from None


def _expected_generation(scope: MemoryScope, value: int):
    if scope.generation != value:
        raise AppError(
            "memory_generation_conflict",
            "此范围发生过遗忘；请刷新，旧候选不能重新写入。",
            409,
            "conflict",
        )


def _check_version(path: str, expected: str) -> tuple[bytes | None, str]:
    data, version = files.read(path)
    if version != expected:
        raise AppError(
            "memory_version_conflict",
            "文件版本已变化；未覆盖当前内容，请重读后比较。",
            409,
            "conflict",
        )
    return data, version


def _index_plan(
    db: Session,
    scope: MemoryScope,
    root: str,
    journal_id: str,
    document: MemoryDocument | None,
    *,
    forget=False,
) -> tuple[dict | None, bool]:
    path = f"{root}/MEMORY.md"
    _, current = files.read(path)
    expected = scope.index_hash or "missing"
    if current != expected:
        if forget:
            return None, True
        raise AppError(
            "memory_index_conflict",
            "MEMORY.md 索引已由外部修改；未覆盖，请人工核对。",
            409,
            "conflict",
        )
    rows = db.scalars(
        select(MemoryDocument)
        .where(MemoryDocument.scope_id == scope.id, MemoryDocument.status != "forgotten")
        .order_by(MemoryDocument.created_at)
    ).all()
    lines = ["<!-- workbench-memory-index schema=1 -->", "# MEMORY", ""]
    for row in rows:
        if forget and document and row.id == document.id:
            continue
        if row.status not in {"active", "publishing"}:
            continue
        title = row.title.replace("[", "\\[").replace("]", "\\]").replace("\n", " ")
        lines.append(f"- [{title}](topics/{row.id}.md) <!-- document_id:{row.id} -->")
    data = ("\n".join(lines) + "\n").encode()
    return files.stage(root, journal_id, 1, path, data, expected_version=expected), False


def _finalize(db: Session, journal: MemoryWriteJournal, *, revision_after_proven=False):
    plan = journal.plan
    scope = db.get(MemoryScope, journal.scope_id)
    if plan.get("generation") != scope.generation:
        raise AppError(
            "memory_generation_conflict", "遗忘屏障已更新，旧写入不能恢复。", 409, "conflict"
        )
    revisionfiles.after(db, journal, proven=revision_after_proven)
    kind = plan["result_type"]
    if kind == "document" or plan.get("approve_proposal"):
        document = db.get(MemoryDocument, journal.document_id)
        if document.status == "forgotten" or db.scalar(
            select(MemoryTombstone.id).where(MemoryTombstone.document_id == document.id)
        ):
            raise AppError("memory_forgotten", "已遗忘文档不能通过旧写入恢复。", 410, "policy")
        document.status = "active"
        document.content_hash = plan["document_hash"]
        document.updated_at = now()
        document.version += 1
    if plan.get("index_hash"):
        scope.index_hash = plan["index_hash"]
        scope.version += 1
    if plan.get("proposal_id"):
        proposal = db.get(MemoryProposal, plan["proposal_id"])
        proposal.status = plan["proposal_status"]
        if proposal.status in {"approved", "rejected"}:
            proposal.decided_at = now()
        proposal.version += 1
    journal.status, journal.finished_at = "committed", now()
    journal.result = {"type": kind, "id": plan.get("result_id"), "generation": scope.generation}
    db.flush()


def _reconcile(db: Session, journal: MemoryWriteJournal):
    if journal.status not in UNRESOLVED:
        return
    plan = journal.plan
    revisionfiles.after(db, journal)  # Only a durable publish receipt can bypass current checks.
    revisionfiles.observed_current(db, journal)
    current = [files.read(entry["path"])[1] for entry in plan.get("files", [])]
    if plan.get("blocked_index") or plan.get("blocked_history"):
        journal.status = "outcome_unknown"
    elif all(
        version == entry["after"]
        for version, entry in zip(current, plan.get("files", []), strict=True)
    ):
        try:
            _finalize(db, journal, revision_after_proven=True)
        except AppError:
            journal.status = "conflict"
    elif all(
        version == entry["before"]
        for version, entry in zip(current, plan.get("files", []), strict=True)
    ):
        journal.status = "outcome_unknown" if journal.operation == "forget" else "not_applied"
        if journal.operation != "forget" and journal.document_id:
            document = db.get(MemoryDocument, journal.document_id)
            if document and document.status == "publishing":
                document.status = "missing"
        if plan.get("proposal_id"):
            proposal = db.get(MemoryProposal, plan["proposal_id"])
            if proposal and proposal.status == "outcome_unknown":
                proposal.status = (
                    "pending" if journal.operation == "proposal_decide" else "conflict"
                )
    else:
        journal.status = "outcome_unknown"
    db.flush()


def _result(db: Session, journal: MemoryWriteJournal, user_id: str, workspace_id: str):
    result = journal.result
    if result["type"] == "document":
        return _document_view(db, _document(db, user_id, workspace_id, result["id"]))
    if result["type"] == "forget":
        return dto.ForgetResult(
            document_id=result["id"],
            status="forgotten",
            scope_generation=result["generation"],
            journal_id=journal.id,
        )
    if result["type"] == "global_rules":
        return get_global_rules(db, user_id, workspace_id)
    return _proposal_view(db, _proposal(db, user_id, workspace_id, result["id"]))


def _write(
    db: Session,
    user_id: str,
    workspace_id: str,
    scope_name: str,
    request_id: str,
    operation: str,
    payload: dict,
    prepare: Callable,
):
    request_id = str(UUID(request_id))
    digest = hashlib.sha256(
        json.dumps(
            {
                "operation": operation,
                "workspace_id": workspace_id,
                "scope": scope_name,
                "payload": payload,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    try:
        with _writer(db, user_id, workspace_id, scope_name, request_id) as owned:
            with owned.begin():
                _authorize(owned, user_id, workspace_id)
                existing = owned.scalar(
                    select(MemoryWriteJournal).where(
                        MemoryWriteJournal.user_id == user_id,
                        MemoryWriteJournal.request_id == request_id,
                    )
                )
                if existing:
                    if existing.request_digest != digest:
                        raise AppError(
                            "idempotency_conflict",
                            "相同请求标识不能提交不同的记忆命令。",
                            409,
                            "conflict",
                        )
                    _reconcile(owned, existing)
            if existing:
                if existing.status == "committed":
                    return _result(owned, existing, user_id, workspace_id)
                if existing.status in {"conflict", "not_applied"}:
                    raise AppError(
                        "memory_write_conflict",
                        "此前请求未完成发布或存在冲突；请核对后使用新的请求标识。",
                        409,
                        "conflict",
                    )
                raise AppError(
                    "memory_write_pending",
                    "此前写入尚未确认，请检查写入记录；不会重复发布。",
                    409,
                    "outcome_unknown",
                )
            with owned.begin():
                owner = _scope(owned, user_id, workspace_id, scope_name, create=True)
                pending = list(
                    owned.scalars(
                        select(MemoryWriteJournal).where(
                            MemoryWriteJournal.scope_id == owner.id,
                            MemoryWriteJournal.status.in_(UNRESOLVED),
                        )
                    )
                )
                for previous in pending:
                    if operation == "forget":
                        previous.status = "conflict"
                    else:
                        _reconcile(owned, previous)
                        if previous.status in UNRESOLVED:
                            raise AppError(
                                "memory_write_pending",
                                "此范围有待核对的文件写入，请先处理。",
                                409,
                                "outcome_unknown",
                            )
                journal = MemoryWriteJournal(
                    id=new_id(),
                    user_id=user_id,
                    workspace_id=workspace_id if scope_name == "workspace" else None,
                    scope_id=owner.id,
                    request_id=request_id,
                    request_digest=digest,
                    operation=operation,
                    status="prepared",
                    plan={},
                )
                owned.add(journal)
                owned.flush()
                journal.plan = prepare(owned, owner, journal)
                # Commit the intent before target publication; keep the session lock held.
            target_phase_complete = False
            try:
                files.publish(journal.plan)
                target_phase_complete = True
                with owned.begin():
                    journal.status = "published"
                    tracking = journal.plan.get("revision_tracking")
                    if tracking:
                        journal.plan = {
                            **journal.plan,
                            "revision_tracking": {
                                **tracking,
                                "publish_confirmed": True,
                            },
                        }
                    owned.flush()
                with owned.begin():
                    _finalize(owned, journal, revision_after_proven=True)
            except Exception as error:
                owned.rollback()
                try:
                    with owned.begin():
                        journal = owned.get(MemoryWriteJournal, journal.id)
                        preflight_conflict = (
                            isinstance(error, AppError)
                            and error.kind == "conflict"
                            and not target_phase_complete
                        )
                        journal.status = "conflict" if preflight_conflict else "outcome_unknown"
                        if preflight_conflict:
                            if journal.document_id:
                                document = owned.get(MemoryDocument, journal.document_id)
                                if document and document.status == "publishing":
                                    document.status = "conflict"
                            if journal.plan.get("proposal_id"):
                                proposal = owned.get(MemoryProposal, journal.plan["proposal_id"])
                                if proposal and proposal.status == "outcome_unknown":
                                    proposal.status = "conflict"
                                    proposal.version += 1
                        revisionfiles.after(owned, journal)
                        revisionfiles.observed_current(owned, journal)
                        owned.flush()
                except Exception:
                    owned.rollback()
                if isinstance(error, AppError):
                    raise
                raise AppError(
                    "memory_write_unknown",
                    "已保存写入意图，但文件与数据库提交结果待核对。",
                    409,
                    "outcome_unknown",
                ) from None
            return _result(owned, journal, user_id, workspace_id)
    except SQLAlchemyError:
        raise AppError(
            "memory_journal_unknown",
            "记忆数据库提交未获确认；请用相同请求标识查询或核对写入记录。",
            409,
            "outcome_unknown",
        ) from None


def _document_plan(
    db,
    owner,
    journal,
    user_id,
    workspace_id,
    title,
    body,
    refs,
    *,
    document=None,
    expected="missing",
    result_type="document",
):
    root = _root(user_id, workspace_id, owner.scope)
    if document is not None:
        if document.status == "forgotten":
            raise AppError("memory_forgotten", "已遗忘文档不能被更新或恢复。", 410)
        _check_version(document.storage_path, expected)
    else:
        count = len(
            db.scalars(
                select(MemoryDocument.id).where(
                    MemoryDocument.scope_id == owner.id, MemoryDocument.status != "forgotten"
                )
            ).all()
        )
        if count >= MAX_DOCUMENTS:
            raise AppError("memory_document_limit", "当前范围的文档数量超过初版限制。", 409)
        identifier = new_id()
        document = MemoryDocument(
            id=identifier,
            user_id=user_id,
            workspace_id=owner.workspace_id,
            scope_id=owner.id,
            title=title,
            storage_path=f"{root}/topics/{identifier}.md",
            status="publishing",
            source_refs=refs,
        )
        db.add(document)
        db.flush()
        _check_version(document.storage_path, "missing")
    journal.document_id = document.id
    document.title, document.source_refs = title, refs
    data = files.encode(document.id, title, owner.scope, owner.generation, refs, body)
    primary = files.stage(
        root, journal.id, 0, document.storage_path, data, expected_version=expected
    )
    index, _ = _index_plan(db, owner, root, journal.id, document)
    revisionfiles.before(db, document, owner, journal, primary)
    return {
        "revision_tracking": {"version": revisionfiles.TRACKING},
        "generation": owner.generation,
        "files": [primary, index],
        "index_hash": index["after"],
        "document_hash": primary["after"],
        "result_type": result_type,
        "result_id": document.id,
    }


def create_document(db, user_id, workspace_id, command: dto.MemoryCreate) -> dto.Document:
    def prepare(owned, scope, journal):
        _expected_generation(scope, command.expected_scope_generation)
        return _document_plan(
            owned,
            scope,
            journal,
            user_id,
            workspace_id,
            command.title,
            command.body,
            command.source_refs,
        )

    return _write(
        db,
        user_id,
        workspace_id,
        command.scope,
        str(command.request_id),
        "create",
        command.model_dump(mode="json"),
        prepare,
    )


def _record_scope(db, user_id, workspace_id, identifier, *, proposal=False):
    with Session(db.get_bind()) as lookup:
        _authorize(lookup, user_id, workspace_id)
        row = (_proposal if proposal else _document)(lookup, user_id, workspace_id, identifier)
        return lookup.get(MemoryScope, row.scope_id).scope


def update_document(
    db, user_id, workspace_id, document_id: str, command: dto.MemoryUpdate
) -> dto.Document:
    scope_name = _record_scope(db, user_id, workspace_id, document_id)

    def prepare(owned, scope, journal):
        document = _document(owned, user_id, workspace_id, document_id)
        raw, _ = _check_version(document.storage_path, command.expected_version)
        title, references = document.title, document.source_refs
        if raw is not None:
            try:
                metadata, _ = files.decode(raw, document.id)
                title, references = metadata["title"], metadata["source_refs"]
            except AppError:
                # An explicit full-body edit at the freshly read file hash may repair its header.
                pass
        return _document_plan(
            owned,
            scope,
            journal,
            user_id,
            workspace_id,
            title,
            command.body,
            references,
            document=document,
            expected=command.expected_version,
        )

    return _write(
        db,
        user_id,
        workspace_id,
        scope_name,
        str(command.request_id),
        "update",
        {"document_id": document_id, **command.model_dump(mode="json")},
        prepare,
    )


def forget_document(
    db, user_id, workspace_id, document_id: str, command: dto.MemoryForget
) -> dto.ForgetResult:
    scope_name = _record_scope(db, user_id, workspace_id, document_id)

    def prepare(owned, scope, journal):
        document = _document(owned, user_id, workspace_id, document_id)
        if document.status == "forgotten":
            raise AppError("memory_forgotten", "文档已经遗忘，请刷新。", 410)
        _, version = _check_version(document.storage_path, command.expected_version)
        scope.generation += 1
        document.status, document.updated_at = "forgotten", now()
        journal.document_id = document.id
        owned.add(
            MemoryTombstone(
                user_id=user_id,
                scope_id=scope.id,
                document_id=document.id,
                generation=scope.generation,
                forgotten_version=version,
                journal_id=journal.id,
            )
        )
        for proposal in owned.scalars(
            select(MemoryProposal).where(
                MemoryProposal.scope_id == scope.id,
                MemoryProposal.status.in_({"pending", "conflict", "outcome_unknown"}),
            )
        ):
            proposal.status, proposal.decided_at = "invalidated", now()
            proposal.version += 1
        owned.flush()
        root = _root(user_id, workspace_id, scope_name)
        primary = files.stage(
            root,
            journal.id,
            0,
            document.storage_path,
            None,
            expected_version=command.expected_version,
        )
        history_deletions, history_blocked = revisionfiles.revoke(owned, document)
        index, blocked = _index_plan(owned, scope, root, journal.id, document, forget=True)
        return {
            "generation": scope.generation,
            "files": [primary] + ([index] if index else []) + history_deletions,
            "blocked_history": history_blocked,
            "index_hash": index["after"] if index else None,
            "blocked_index": blocked,
            "result_type": "forget",
            "result_id": document.id,
        }

    return _write(
        db,
        user_id,
        workspace_id,
        scope_name,
        str(command.request_id),
        "forget",
        {"document_id": document_id, **command.model_dump(mode="json")},
        prepare,
    )


def get_global_rules(db, user_id, workspace_id) -> dto.GlobalRules:
    _authorize(db, user_id, workspace_id)
    path = f"users/{user_id}/AGENTS.md"
    data, version = files.read(path)
    return dto.GlobalRules(
        body=data.decode("utf-8") if data is not None else "",
        version=version,
        source_path=path,
        exists=data is not None,
        editable=True,
        scope="user",
        workspace_id=None,
    )


def update_global_rules(
    db, user_id, workspace_id, command: dto.GlobalRulesUpdate
) -> dto.GlobalRules:
    def prepare(owned, scope, journal):
        path = f"users/{user_id}/AGENTS.md"
        _check_version(path, command.expected_version)
        if "\0" in command.body:
            raise AppError("invalid_memory_text", "AGENTS 规则不能包含 NUL 字符。")
        data = command.body.encode()
        if len(data) > files.MAX_BYTES:
            raise AppError("memory_file_too_large", "全局规则超过文本大小限制。", 413)
        primary = files.stage(
            _root(user_id, workspace_id, "user"),
            journal.id,
            0,
            path,
            data,
            expected_version=command.expected_version,
        )
        return {
            "generation": scope.generation,
            "files": [primary],
            "result_type": "global_rules",
            "result_id": None,
        }

    return _write(
        db,
        user_id,
        workspace_id,
        "user",
        str(command.request_id),
        "global_rules",
        command.model_dump(mode="json"),
        prepare,
    )


def load_rules(db, user_id, workspace_id, relative_paths: list[str]) -> dto.RulesSnapshot:
    workspace = _authorize(db, user_id, workspace_id)
    if len(relative_paths) > 32:
        raise AppError("rules_path_limit", "每次加载规则最多提供 32 个相关路径。")
    global_rules = get_global_rules(db, user_id, workspace_id)
    result = []
    if global_rules.exists:
        result.append(
            dto.RuleSource(
                source_path=global_rules.source_path,
                version=global_rules.version,
                scope="user",
                applies_to="*",
                body=global_rules.body,
                editable=True,
                precedence=0,
            )
        )
    root = Path(workspace.root_path).resolve()
    directories = {Path(".")}
    normalized = []
    for value in relative_paths:
        if len(value) > 4096 or Path(value).is_absolute() or ".." in Path(value).parts:
            raise AppError(
                "rule_path_denied", "规则加载只接受工作区内的相关相对路径。", 403, "policy"
            )
        candidate = authorized_path(root, value)
        relative = candidate.relative_to(root)
        normalized.append(relative.as_posix())
        parent = relative if candidate.is_dir() else relative.parent
        if len(parent.parts) > 32:
            raise AppError("rules_path_limit", "相关目录层级超过规则加载限制。")
        directories.update([parent, *parent.parents])
    total = len(global_rules.body.encode())
    if total > 512 * 1024:
        raise AppError("rules_size_limit", "全局规则超过有界上下文限制。", 413)
    for directory in sorted(directories, key=lambda item: (len(item.parts), item.as_posix())):
        relative = directory / "AGENTS.md"
        candidate = authorized_path(root, relative.as_posix())
        if not candidate.is_file():
            continue
        body, version = read_text(candidate)
        total += len(body.encode())
        if total > 512 * 1024 or len(result) >= 128:
            raise AppError("rules_size_limit", "相关规则超过有界上下文限制，请缩小路径范围。", 413)
        project = directory == Path(".")
        result.append(
            dto.RuleSource(
                source_path=relative.as_posix(),
                version=version,
                scope="project" if project else "directory",
                applies_to="." if project else directory.as_posix(),
                body=body,
                editable=False,
                precedence=len(result),
            )
        )
    version = hashlib.sha256(
        json.dumps(
            [rule.model_dump() for rule in result], sort_keys=True, ensure_ascii=False
        ).encode()
    ).hexdigest()
    return dto.RulesSnapshot(
        global_rules=global_rules, rules=result, version_digest=version, relative_paths=normalized
    )


def _proposal_view(db: Session, row: MemoryProposal) -> dto.Proposal:
    scope = db.get(MemoryScope, row.scope_id)
    body = None
    status = row.status
    if (
        row.status not in {"invalidated", "outcome_unknown"}
        and row.scope_generation == scope.generation
    ):
        raw, current_hash = files.read(row.storage_path)
        if raw is not None:
            try:
                _, body = files.decode(raw, row.id)
            except AppError:
                body, status = raw.decode("utf-8"), "conflict"
            if current_hash != row.content_hash:
                status = "conflict"
        else:
            status = "conflict"
    return dto.Proposal(
        id=row.id,
        scope=scope.scope,
        workspace_id=row.workspace_id,
        document_id=row.document_id,
        title=row.title,
        body=body,
        base_version=row.base_version,
        scope_generation=row.scope_generation,
        status=status,
        source_refs=row.source_refs,
        source_actor=row.source_actor,
        created_at=row.created_at,
        decided_at=row.decided_at,
        version=row.version,
    )


def list_proposals(db, user_id, workspace_id, scope: dto.Scope) -> list[dto.Proposal]:
    _authorize(db, user_id, workspace_id)
    owner = _scope(db, user_id, workspace_id, scope)
    if owner is None:
        return []
    return [
        _proposal_view(db, row)
        for row in db.scalars(
            select(MemoryProposal)
            .where(MemoryProposal.scope_id == owner.id)
            .order_by(MemoryProposal.created_at.desc())
            .limit(200)
        )
    ]


def create_proposal(
    db, user_id, workspace_id, command: dto.ProposalCreate, *, source_actor="user"
) -> dto.Proposal:
    if source_actor not in {"user", "agent", "subagent"}:
        raise AppError("invalid_memory_actor", "记忆候选来源身份无效。", 403, "policy")

    def prepare(owned, scope, journal):
        _expected_generation(scope, command.expected_scope_generation)
        if command.document_id:
            document = _document(owned, user_id, workspace_id, str(command.document_id))
            if document.scope_id != scope.id or document.status == "forgotten":
                raise AppError("memory_scope_conflict", "候选目标已遗忘或不在所选范围。", 409)
            _check_version(document.storage_path, command.base_version)
        elif command.base_version != "missing":
            raise AppError("memory_version_conflict", "新文档候选的基础版本必须为 missing。", 409)
        identifier = new_id()
        root = _root(user_id, workspace_id, scope.scope)
        path = f"{root}/proposals/{identifier}.md"
        data = files.encode(
            identifier,
            command.title,
            scope.scope,
            scope.generation,
            command.source_refs,
            command.body,
        )
        proposal = MemoryProposal(
            id=identifier,
            user_id=user_id,
            workspace_id=scope.workspace_id,
            scope_id=scope.id,
            document_id=str(command.document_id) if command.document_id else None,
            title=command.title,
            storage_path=path,
            content_hash=files.digest(data),
            base_version=command.base_version,
            scope_generation=scope.generation,
            status="outcome_unknown",
            source_refs=command.source_refs,
            source_actor=source_actor,
        )
        owned.add(proposal)
        owned.flush()
        primary = files.stage(root, journal.id, 0, path, data)
        return {
            "generation": scope.generation,
            "files": [primary],
            "result_type": "proposal",
            "result_id": identifier,
            "proposal_id": identifier,
            "proposal_status": "pending",
        }

    return _write(
        db,
        user_id,
        workspace_id,
        command.scope,
        str(command.request_id),
        "proposal_create",
        {**command.model_dump(mode="json"), "source_actor": source_actor},
        prepare,
    )


def decide_proposal(
    db, user_id, workspace_id, proposal_id: str, command: dto.ProposalDecision
) -> dto.Proposal:
    scope_name = _record_scope(db, user_id, workspace_id, proposal_id, proposal=True)

    def prepare(owned, scope, journal):
        proposal = _proposal(owned, user_id, workspace_id, proposal_id)
        if proposal.version != command.expected_version or proposal.status != "pending":
            raise AppError(
                "memory_proposal_conflict", "候选已改变或处理，请重新查看。", 409, "conflict"
            )
        _expected_generation(scope, proposal.scope_generation)
        if command.approved:
            raw, current_hash = files.read(proposal.storage_path)
            if raw is not None and current_hash != proposal.content_hash:
                raise AppError(
                    "memory_candidate_changed",
                    "候选文件在批准前已改变，请重新提交候选。",
                    409,
                    "conflict",
                )
            if raw is None:
                raise AppError("memory_candidate_missing", "候选文件不存在，不能批准。", 409)
            _, body = files.decode(raw, proposal.id)
            document = (
                _document(owned, user_id, workspace_id, proposal.document_id)
                if proposal.document_id
                else None
            )
            plan = _document_plan(
                owned,
                scope,
                journal,
                user_id,
                workspace_id,
                proposal.title,
                body,
                proposal.source_refs,
                document=document,
                expected=proposal.base_version,
                result_type="proposal",
            )
            proposal.document_id = journal.document_id
            proposal.status = "outcome_unknown"
            plan.update(
                result_id=proposal.id,
                proposal_id=proposal.id,
                proposal_status="approved",
                approve_proposal=True,
            )
            return plan
        return {
            "generation": scope.generation,
            "files": [],
            "result_type": "proposal",
            "result_id": proposal.id,
            "proposal_id": proposal.id,
            "proposal_status": "rejected",
        }

    return _write(
        db,
        user_id,
        workspace_id,
        scope_name,
        str(command.request_id),
        "proposal_decide",
        {"proposal_id": proposal_id, **command.model_dump(mode="json")},
        prepare,
    )


def recover_writes(db, user_id, workspace_id, scope: dto.Scope) -> list[dto.WriteNotice]:
    with _writer(db, user_id, workspace_id, scope, "reconciliation") as owned:
        with owned.begin():
            _authorize(owned, user_id, workspace_id)
            owner = _scope(owned, user_id, workspace_id, scope)
            if owner is None:
                return []
            rows = list(
                owned.scalars(
                    select(MemoryWriteJournal).where(
                        MemoryWriteJournal.scope_id == owner.id,
                        MemoryWriteJournal.status.in_(UNRESOLVED),
                    )
                )
            )
            for row in rows:
                _reconcile(owned, row)
            return [_notice(row) for row in rows]


# Revision reads deliberately do not backfill old journal candidates or mutate files.
def _history_document(db, user_id, workspace_id, document_id):
    _authorize(db, user_id, workspace_id)
    row = _document(db, user_id, workspace_id, document_id)
    revisionfiles.assert_not_forgotten(db, row)
    return row, db.get(MemoryScope, row.scope_id), _document_view(db, row)


def _revision_row(db, document, version, *, generation=None):
    if (
        not isinstance(version, str)
        or len(version) != 64
        or any(c not in "abcdef0123456789" for c in version)
    ):
        raise AppError("memory_revision_invalid", "历史版本必须是明确的SHA256。", 422)
    query = select(MemoryRevision).where(
        MemoryRevision.document_id == document.id,
        MemoryRevision.user_id == document.user_id,
        MemoryRevision.scope_id == document.scope_id,
        MemoryRevision.content_hash == version,
        MemoryRevision.status == "available",
    )
    if generation is not None:
        query = query.where(MemoryRevision.scope_generation == generation)
    row = db.scalar(
        query.order_by(MemoryRevision.scope_generation.desc(), MemoryRevision.confirmed_at.desc())
    )
    if row is None:
        raise AppError(
            "memory_revision_not_found", "此版本没有已确认的可读快照；不会从旧候选导入。", 404
        )
    return row


def _revision_view(db, row, document, scope, current):
    data = None
    try:
        data = revisionfiles.read(row, document, scope)
    except (AppError, OSError):
        pass
    reason = None
    if data is None:
        reason = "snapshot_unavailable"
    elif row.scope_generation != scope.generation:
        reason = "scope_generation_changed"
    elif current.status != "active":
        reason = "current_document_not_active"
    journal = db.get(MemoryWriteJournal, row.journal_id)
    summary = dto.MemoryRevisionSummary(
        document_id=document.id,
        version=row.content_hash,
        scope_generation=row.scope_generation,
        title=data[0]["title"] if data else row.title,
        origin=row.origin,
        captured_at=row.confirmed_at,
        journal_id=row.journal_id,
        journal_status=journal.status if journal else "unknown",
        status="available" if data else "unavailable",
        restorable=reason is None,
        unavailable_reason=reason,
    )
    return summary, data


def list_revisions(db, user_id, workspace_id, document_id: str) -> dto.MemoryRevisionList:
    from sqlalchemy import func
    from sqlalchemy.orm import aliased

    document, scope, current = _history_document(db, user_id, workspace_id, document_id)
    # One visible item per real hash, preferring its most recently observed
    # generation; the command version is a file digest, not a revision-row ID.
    latest = aliased(
        MemoryRevision,
        select(MemoryRevision)
        .where(
            MemoryRevision.document_id == document.id,
            MemoryRevision.status == "available",
        )
        .distinct(MemoryRevision.content_hash)
        .order_by(
            MemoryRevision.content_hash,
            MemoryRevision.scope_generation.desc(),
            MemoryRevision.confirmed_at.desc(),
        )
        .subquery(),
    )
    rows = list(db.scalars(select(latest).order_by(latest.confirmed_at.desc()).limit(101)))
    summaries = [_revision_view(db, row, document, scope, current)[0] for row in rows[:100]]
    warnings = []
    if any(row.status == "unavailable" for row in summaries):
        warnings.append("部分已确认快照缺失或改变，未返回其正文或允许恢复。")
    if any(row.unavailable_reason == "scope_generation_changed" for row in summaries):
        warnings.append("较早世代只供人工比较；不能跨遗忘屏障恢复。")
    started = db.scalar(
        select(func.min(MemoryRevision.confirmed_at)).where(
            MemoryRevision.document_id == document.id,
            MemoryRevision.status == "available",
        )
    )
    return dto.MemoryRevisionList(
        document_id=document.id,
        current_version=current.version,
        current_status=current.status,
        scope_generation=scope.generation,
        history_complete=False,
        history_gap_reason="仅记录版本功能启用后被实际观察或确认发布的正文；未导入旧journal候选，也不保证记录每次外部编辑。",
        history_started_at=started,
        revisions=summaries,
        truncated=len(rows) > 100,
        warnings=warnings,
    )


def read_revision(
    db, user_id, workspace_id, document_id: str, version: str
) -> dto.MemoryRevisionView:
    document, scope, current = _history_document(db, user_id, workspace_id, document_id)
    row = _revision_row(db, document, version)
    summary, data = _revision_view(db, row, document, scope, current)
    if data is None:
        raise AppError(
            "memory_revision_unavailable", "历史正文缺失或改变；未返回未确认内容。", 409, "conflict"
        )
    return dto.MemoryRevisionView(
        **summary.model_dump(), body=data[1], source_refs=data[0]["source_refs"]
    )


def compare_revision(
    db, user_id, workspace_id, document_id: str, version: str
) -> dto.MemoryRevisionComparison:
    import difflib

    _, _, current = _history_document(db, user_id, workspace_id, document_id)
    if current.status != "active" or current.body is None:
        raise AppError(
            "memory_current_unavailable",
            "当前正文不处于可比较活动状态，请先核对。",
            409,
            "conflict",
        )
    target = read_revision(db, user_id, workspace_id, document_id, version)
    # Bound SequenceMatcher inputs and returned diff, not just HTTP output.
    limit, max_lines = 200_000, 4000
    old, new = (
        target.body[:limit].splitlines(keepends=True),
        current.body[:limit].splitlines(keepends=True),
    )
    truncated = (
        len(target.body) > limit
        or len(current.body) > limit
        or len(old) > max_lines
        or len(new) > max_lines
    )
    chunks, consumed = [], 0
    for chunk in difflib.unified_diff(
        old[:max_lines], new[:max_lines], fromfile=version, tofile=current.version
    ):
        if consumed + len(chunk) > limit:
            truncated = True
            break
        chunks.append(chunk)
        consumed += len(chunk)
    return dto.MemoryRevisionComparison(
        document_id=document_id,
        target_version=version,
        current_version=current.version,
        scope_generation=current.scope_generation,
        target_title=target.title,
        current_title=current.title,
        title_changed=target.title != current.title,
        target_source_refs=target.source_refs,
        current_source_refs=current.source_refs,
        diff="".join(chunks),
        truncated=truncated,
    )


def restore_document(
    db, user_id, workspace_id, document_id: str, command: dto.MemoryRestore
) -> dto.Document:
    scope_name = _record_scope(db, user_id, workspace_id, document_id)

    def prepare(owned, scope, journal):
        document = _document(owned, user_id, workspace_id, document_id)
        revisionfiles.assert_not_forgotten(owned, document)
        _expected_generation(scope, command.expected_scope_generation)
        current = _document_view(owned, document)
        if current.status != "active":
            raise AppError(
                "memory_current_unavailable",
                "仅活动、无待核对写入的文档可恢复旧正文。",
                409,
                "conflict",
            )
        _check_version(document.storage_path, command.expected_current_version)
        target = _revision_row(owned, document, command.target_version)
        if target.scope_generation != scope.generation:
            raise AppError(
                "memory_generation_conflict",
                "不能跨遗忘世代恢复历史；请明确新建所需内容。",
                409,
                "conflict",
            )
        metadata, body = revisionfiles.read(target, document, scope)
        plan = _document_plan(
            owned,
            scope,
            journal,
            user_id,
            workspace_id,
            metadata["title"],
            body,
            metadata["source_refs"],
            document=document,
            expected=command.expected_current_version,
        )
        plan["restored_from_version"] = command.target_version
        return plan

    return _write(
        db,
        user_id,
        workspace_id,
        scope_name,
        str(command.request_id),
        "restore",
        {"document_id": document_id, **command.model_dump(mode="json")},
        prepare,
    )
