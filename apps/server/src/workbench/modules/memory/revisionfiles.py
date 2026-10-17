"""Confirmed snapshots only. PostgreSQL indexes proof/identity; Markdown owns content."""

from sqlalchemy import select

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import new_id, now

from . import files
from .domain import MemoryDocument, MemoryRevision, MemoryScope, MemoryTombstone

TRACKING = 1


def assert_not_forgotten(db, document):
    if document.status == "forgotten" or db.scalar(
        select(MemoryTombstone.id).where(MemoryTombstone.document_id == document.id)
    ):
        raise AppError(
            "memory_forgotten", "已遗忘文档及其历史不可读取或恢复；请明确新建。", 410, "policy"
        )


def _metadata(raw, document, scope, version):
    if raw is None or files.digest(raw) != version:
        raise AppError(
            "memory_revision_unavailable",
            "历史快照缺失或已改变，不能作为可恢复版本。",
            409,
            "conflict",
        )
    metadata, body = files.decode(raw, document.id)
    if metadata["scope"] != scope.scope or metadata["generation"] > scope.generation:
        raise AppError(
            "memory_revision_identity", "历史快照范围或世代身份不一致。", 409, "conflict"
        )
    return metadata, body


def snapshot(db, document, scope, journal, raw, version, origin):
    """Caller proves raw was an actual current file or acknowledged publication."""
    assert_not_forgotten(db, document)
    metadata, _ = _metadata(raw, document, scope, version)
    root = document.storage_path.rsplit("/topics/", 1)[0]
    if root == document.storage_path:
        raise AppError("memory_revision_identity", "文档路径不符合受管主题存储。", 409, "policy")
    path = f"{root}/.revisions/{document.id}/{scope.generation}/{version}.md"
    existing = db.scalar(
        select(MemoryRevision).where(
            MemoryRevision.document_id == document.id,
            MemoryRevision.scope_generation == scope.generation,
            MemoryRevision.content_hash == version,
        )
    )
    if existing and existing.status != "available":
        raise AppError("memory_forgotten", "此历史版本已被屏蔽，不能重新登记。", 410, "policy")
    try:
        files.snapshot(path, raw)
    except FileExistsError:
        saved, saved_version = files.read(path)
        if saved is None or saved_version != version:
            raise AppError(
                "memory_revision_conflict", "不可变历史快照已变化，未覆盖。", 409, "conflict"
            ) from None
    if existing:
        if existing.storage_path != path:
            raise AppError("memory_revision_identity", "历史索引路径不一致。", 409, "policy")
        return existing
    row = MemoryRevision(
        id=new_id(),
        user_id=document.user_id,
        workspace_id=document.workspace_id,
        scope_id=scope.id,
        document_id=document.id,
        scope_generation=scope.generation,
        content_hash=version,
        storage_path=path,
        title=metadata["title"],
        origin=origin,
        status="available",
        journal_id=journal.id,
        confirmed_at=now(),
    )
    db.add(row)
    db.flush()
    return row


def before(db, document, scope, journal, entry):
    if not entry.get("base"):
        return
    raw, version = files.read(entry["base"])
    if raw is None or version != entry["before"]:
        raise AppError(
            "memory_revision_base_invalid", "实际基础版本快照不可确认，停止写入。", 409, "conflict"
        )
    try:
        _metadata(raw, document, scope, version)
    except AppError:
        return  # Explicit repair of malformed MD cannot manufacture valid history.
    snapshot(db, document, scope, journal, raw, version, "observed_current")


def after(db, journal, *, proven=False):
    tracking = journal.plan.get("revision_tracking", {})
    if tracking.get("version") != TRACKING or not journal.document_id:
        return  # Never import legacy journal/candidate files as history.
    if not proven and not tracking.get("publish_confirmed"):
        return
    document = db.get(MemoryDocument, journal.document_id)
    scope = db.get(MemoryScope, journal.scope_id)
    assert_not_forgotten(db, document)
    if scope.generation != journal.plan.get("generation"):
        raise AppError(
            "memory_generation_conflict", "旧世代写入不能登记为当前历史。", 409, "conflict"
        )
    entry = next(
        (item for item in journal.plan.get("files", []) if item["path"] == document.storage_path),
        None,
    )
    if not entry or entry["after"] == "missing":
        return
    try:
        raw, version = files.read(entry.get("candidate") or document.storage_path)
    except (AppError, OSError):
        # A damaged candidate is not evidence; an independently matching target
        # can still confirm the acknowledged publication.
        raw, version = None, "missing"
    if raw is None or version != entry["after"]:
        raw, version = files.read(document.storage_path)
    if raw is None or version != entry["after"]:
        raise AppError(
            "memory_revision_after_unconfirmed",
            "已确认发布的正文快照不可核对，保留待对账。",
            409,
            "outcome_unknown",
        )
    snapshot(db, document, scope, journal, raw, version, "managed_publish")


def observed_current(db, journal):
    """A conflict's current file is evidence; an abandoned candidate is not."""
    if (
        journal.plan.get("revision_tracking", {}).get("version") != TRACKING
        or not journal.document_id
    ):
        return
    document, scope = (
        db.get(MemoryDocument, journal.document_id),
        db.get(MemoryScope, journal.scope_id),
    )
    if document.status == "forgotten" or scope.generation != journal.plan.get("generation"):
        return
    raw, version = files.read(document.storage_path)
    if raw is None:
        return
    try:
        _metadata(raw, document, scope, version)
    except AppError:
        return
    snapshot(db, document, scope, journal, raw, version, "observed_current")


def read(row, document, scope):
    raw, version = files.read(row.storage_path)
    if version != row.content_hash:
        raise AppError("memory_revision_unavailable", "历史快照缺失或已改变。", 409, "conflict")
    return _metadata(raw, document, scope, row.content_hash)


def revoke(db, document):
    """Revoke before FS cleanup, so any partial cleanup still hides all bodies."""
    entries, blocked = [], False
    for row in db.scalars(select(MemoryRevision).where(MemoryRevision.document_id == document.id)):
        row.status = "forgotten"
        row.version += 1
        try:
            _, current = files.read(row.storage_path)
        except (AppError, OSError):
            blocked = True
            continue
        if current == "missing":
            continue
        if current != row.content_hash:
            blocked = True
            continue
        # Do not duplicate forgotten snapshots into new journal base files.
        entries.append(
            {
                "path": row.storage_path,
                "before": current,
                "after": "missing",
                "base": None,
                "candidate": None,
            }
        )
    return entries, blocked
