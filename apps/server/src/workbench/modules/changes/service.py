"""Persist evidence at the real effect boundary; never infer changes from Git HEAD."""

import difflib
import hashlib
import json
import logging
import os
import stat
import time
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import event, or_, select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, new_id
from workbench.modules.changes import dto
from workbench.modules.changes.domain import ChangeRecord, CommandCapture
from workbench.modules.files.domain import Artifact
from workbench.modules.files.storage import publish_bytes, storage_path
from workbench.modules.runs.domain import Run
from workbench.modules.tools.domain import Operation

SNAPSHOT_LIMIT = 2 * 1024 * 1024
SCAN_FILES = 2000
SCAN_BYTES = 64 * 1024 * 1024
SCAN_SECONDS = 3
DIFF_LIMIT = 64 * 1024
DIFF_COMPUTE_BYTES = 512 * 1024
DIFF_COMPUTE_LINES = 5000
logger = logging.getLogger(__name__)


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _snapshot(db, run, identifier, side, data):
    if data is None or len(data) > SNAPSHOT_LIMIT:
        return None
    # Serialize publication and reference collection within this Run. Sharing
    # snapshots across Runs would couple their independent deletion lifetimes.
    db.scalar(select(Run.id).where(Run.id == run.id).with_for_update())
    content_hash = digest(data)
    existing = db.scalar(
        select(Artifact.id).where(
            Artifact.run_id == run.id,
            Artifact.kind == "change_snapshot",
            Artifact.status == "available",
            Artifact.content_hash == content_hash,
        )
    )
    if existing:
        return existing
    artifact_id = str(uuid5(UUID(identifier), side))
    relative = f"workspaces/{run.workspace_id}/changes/{artifact_id}/snapshot"
    target = storage_path(relative)
    # Snapshots are internal Artifacts, never tool-presentable/downloadable originals.
    publish_bytes(target, data)
    db.add(
        Artifact(
            id=artifact_id,
            user_id=run.user_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            display_name="change-snapshot",
            mime_type="application/octet-stream",
            byte_size=len(data),
            content_hash=content_hash,
            storage_path=relative,
            kind="change_snapshot",
            status="available",
            metadata_json={"internal": True},
        )
    )
    db.flush()
    return artifact_id


@event.listens_for(Session, "after_commit")
def _remove_released_snapshots(db):
    transaction = db.get_nested_transaction() or db.get_transaction()
    queues = db.info.get("released_change_snapshots", {})
    paths = queues.pop(transaction, [])
    if transaction.parent is not None:
        queues.setdefault(transaction.parent, []).extend(paths)
        return
    db.info.pop("released_change_snapshots", None)
    for path in paths:
        try:
            path.unlink(missing_ok=True)
            # Each snapshot has its own private directory.
            path.parent.rmdir()
        except OSError:
            logger.exception("Could not remove released change snapshot %s", path)


@event.listens_for(Session, "after_rollback")
def _cancel_snapshot_removal(db):
    transaction = db.get_nested_transaction() or db.get_transaction()
    db.info.get("released_change_snapshots", {}).pop(transaction, None)


@event.listens_for(Session, "after_transaction_end")
def _discard_snapshot_removal_queue(db, transaction):
    if transaction.parent is None:
        db.info.pop("released_change_snapshots", None)


def _release_unreferenced_snapshots(db, run):
    """The caller holds the Run lock; closed baselines are no longer evidence owners."""
    pending = set()
    for capture in db.scalars(select(CommandCapture).where(CommandCapture.run_id == run.id)):
        if capture.state == "pending":
            pending.update(
                item.get("artifact_id") for item in capture.baseline.values()
            )
        else:
            capture.baseline = {}
    db.flush()
    referenced = select(ChangeRecord.id).where(
        or_(
            ChangeRecord.before_artifact_id == Artifact.id,
            ChangeRecord.after_artifact_id == Artifact.id,
        )
    ).exists()
    for artifact in db.scalars(
        select(Artifact).where(
            Artifact.run_id == run.id,
            Artifact.kind == "change_snapshot",
            Artifact.status == "available",
            ~referenced,
        )
    ):
        if artifact.id not in pending:
            path = storage_path(artifact.storage_path)
            db.delete(artifact)
            # A rollback must leave persisted baselines readable. Random IDs
            # prevent a later publisher from reusing this deferred removal path.
            transaction = db.get_nested_transaction() or db.get_transaction()
            queues = db.info.setdefault("released_change_snapshots", {})
            queues.setdefault(transaction, []).append(path)


def prepare_managed_change(db, operation, root, path, before, after, item_index=0):
    """Caller holds the file lock. Commit this intent before replacing the file."""
    existing = db.scalar(
        select(ChangeRecord).where(
            ChangeRecord.operation_id == operation.id, ChangeRecord.item_index == item_index
        )
    )
    if existing:
        # Observing the expected after bytes is insufficient proof of our prior write.
        raise AppError(
            "file_write_outcome_unknown",
            "此文件已有写入意图，请核对原操作，不能自动重放。",
            409,
            "outcome_unknown",
        )
    run = db.get(Run, operation.run_id)
    identifier = new_id()
    row = ChangeRecord(
        id=identifier,
        operation_id=operation.id,
        item_index=item_index,
        run_id=run.id,
        root_run_id=run.root_run_id or run.id,
        workspace_id=run.workspace_id,
        execution_binding_id=getattr(run, "execution_binding_id", None),
        path=str(path.resolve().relative_to(root.resolve())),
        before_version=digest(before),
        after_version=digest(after),
        before_exists=before is not None,
        after_exists=after is not None,
        before_artifact_id=_snapshot(db, run, identifier, "before", before),
        after_artifact_id=_snapshot(db, run, identifier, "after", after),
        source="managed",
        attribution="managed",
        state="intent",
        gaps=[],
    )
    db.add(row)
    db.flush()
    return row


def mark_applied(db, identifier):
    row = db.get(ChangeRecord, identifier)
    if row and row.state == "intent":
        row.state = "applied"


def confirm_operation(db, operation, result):
    """Called in the Operation.result transaction, including partial batch results."""
    if not isinstance(result, dict):
        return
    results = result.get("files", [result])
    acknowledged = {
        item.get("change_id")
        for item in results
        if isinstance(item, dict) and item.get("change_id")
    }
    rows = list(db.scalars(select(ChangeRecord).where(ChangeRecord.operation_id == operation.id)))
    changed = []
    for row in rows:
        if row.state == "applied" and row.id in acknowledged:
            row.state = "confirmed"
            changed.append(row)
    if changed:
        _emit(db, operation.run_id, changed)


def _emit(db, run_id, rows, *, captures=(), observation=False):
    from workbench.modules.runs.ownership import current_owner
    from workbench.modules.runs.service import emit

    # Resource reconciliation may observe a different run while checking global
    # command capacity. This evidence-only event does not authorize an execution.
    token = current_owner.set(None) if observation else None
    try:
        issues = [gap for row in [*rows, *captures] for gap in row.gaps]
        coverage = (
            "pending"
            if any(capture.state == "pending" for capture in captures)
            else "partial"
            if issues
            else "complete"
        )
        emit(
            db,
            run_id,
            "changes.updated",
            {
                "revision": _revision(rows, captures),
                "ids": [row.id for row in rows],
                "coverage": coverage,
            },
        )
    finally:
        if token is not None:
            current_owner.reset(token)


def _open_regular(root, path):
    """Do not follow a directory/file symlink swapped by the running command."""
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = path.relative_to(root).parts
        for component in parts[:-1]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
        )
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            os.close(file_descriptor)
            raise OSError("Change capture only reads regular files")
        return os.fdopen(file_descriptor, "rb")
    finally:
        os.close(descriptor)


def _capture_entry(db, run, root, path, identifier, budget, deadline):
    """Bound both file IO and immutable storage; skipped content stays unknown."""
    info = path.stat(follow_symlinks=False)
    if info.st_size > budget:
        return {"exists": True, "version": None, "artifact_id": None}, 0, ["byte_budget"]
    before = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    hasher = hashlib.sha256()
    chunks = [] if info.st_size <= SNAPSHOT_LIMIT else None
    size = 0
    with _open_regular(root, path) as stream:
        opened = os.fstat(stream.fileno())
        if before != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        ):
            return {"exists": True, "version": None, "artifact_id": None}, 0, ["concurrent_write"]
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            size += len(chunk)
            if size > budget or time.monotonic() > deadline:
                return {"exists": True, "version": None, "artifact_id": None}, size, ["scan_budget"]
            hasher.update(chunk)
            if chunks is not None:
                chunks.append(chunk)
    after = path.stat(follow_symlinks=False)
    if before != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        return {"exists": True, "version": None, "artifact_id": None}, size, ["concurrent_write"]
    content = b"".join(chunks) if chunks is not None else None
    artifact = _snapshot(db, run, identifier, "content", content)
    gaps = [] if content is not None else ["snapshot_size_limit"]
    return {"exists": True, "version": hasher.hexdigest(), "artifact_id": artifact}, size, gaps


def scan(db, run, root):
    """Only currently authorized regular files; symlinks and runtime trees are excluded."""
    from workbench.modules.tools.filesystem import authorized_path

    root = Path(root).absolute()
    if root.resolve() != root or root.is_symlink() or not root.is_dir():
        raise AppError("change_root_unavailable", "执行目录已改变，无法完整采集。", 409)
    manifest, gaps, consumed = {}, [], 0
    deadline = time.monotonic() + SCAN_SECONDS
    for directory, children, names in os.walk(root, followlinks=False):
        visible_children = []
        for child in sorted(children):
            path = Path(directory) / child
            try:
                authorized_path(root, str(path))
            except AppError:
                continue
            if path.is_symlink():
                gaps.append("symlink_excluded")
            else:
                visible_children.append(child)
        children[:] = visible_children
        for name in sorted(names):
            path = Path(directory) / name
            if name.startswith((".workbench-write-", ".publishing-")):
                continue
            try:
                authorized_path(root, str(path))
            except AppError:
                continue
            if path.is_symlink() or not path.is_file():
                gaps.append("non_regular_file_excluded")
                continue
            if len(manifest) >= SCAN_FILES or time.monotonic() > deadline:
                return manifest, sorted(set(gaps + ["scan_budget"]))
            try:
                item, size, issues = _capture_entry(
                    db, run, root, path, new_id(), max(0, SCAN_BYTES - consumed), deadline
                )
            except (OSError, ValueError):
                item, size, issues = (
                    {"exists": True, "version": None, "artifact_id": None},
                    0,
                    ["file_unreadable"],
                )
            manifest[str(path.relative_to(root))] = item
            consumed += size
            gaps.extend(issues)
    return manifest, sorted(set(gaps))


def begin_command(db, operation, root):
    """Persist and commit this baseline before process dispatch, including PTYs."""
    existing = db.scalar(select(CommandCapture).where(CommandCapture.operation_id == operation.id))
    if existing:
        return existing
    run = db.scalar(select(Run).where(Run.id == operation.run_id).with_for_update())
    manifest, gaps = scan(db, run, root)
    capture = CommandCapture(
        operation_id=operation.id,
        run_id=run.id,
        workspace_id=run.workspace_id,
        root_path=str(Path(root).resolve()),
        baseline=manifest,
        # Native scoped commands share their directory with editors and other tasks.
        gaps=sorted(set(gaps + ["shared_directory_observation"])),
        state="pending",
    )
    db.add(capture)
    db.flush()
    _emit(db, run.id, [], captures=[capture], observation=True)
    return capture


def _begin_command_safely(identifier, root):
    """Capture errors never resend or change the command's business outcome."""
    with Session(engine()) as db:
        operation = db.get(Operation, identifier)
        try:
            begin_command(db, operation, root)
            db.commit()
        except (OSError, ValueError, AppError) as error:
            db.rollback()
            capture = db.scalar(
                select(CommandCapture).where(CommandCapture.operation_id == identifier)
            )
            if capture is None:
                run = db.get(Run, operation.run_id)
                db.add(
                    CommandCapture(
                        operation_id=identifier,
                        run_id=run.id,
                        workspace_id=run.workspace_id,
                        root_path=str(Path(root).resolve()),
                        baseline={},
                        state="pending",
                        gaps=["baseline_unavailable", type(error).__name__],
                    )
                )
                db.commit()


def begin_command_safely(identifier, root):
    try:
        _begin_command_safely(identifier, root)
    except Exception:
        # A failed observer must not turn a real command into a failed/retriable
        # tool call. Missing baseline is surfaced as unknown by the query path.
        logger.exception("Could not persist command change baseline for %s", identifier)


def finish_command(db, command):
    """Only a proven resource closure ends a persisted capture; never stop resources here."""
    if not command.resources_closed:
        return
    # Match the run-before-ledger lock order used by process reconciliation and
    # event sequencing. Snapshot FK locks must not be upgraded concurrently.
    run = db.scalar(select(Run).where(Run.id == command.run_id).with_for_update())
    capture = db.scalar(
        select(CommandCapture)
        .where(CommandCapture.operation_id == command.operation_id)
        .with_for_update()
    )
    if capture is None or capture.state != "pending":
        return
    manifest, issues = scan(db, run, capture.root_path)
    gaps = sorted(set(capture.gaps + issues))
    # If either tree walk was cut short, an unseen path does not prove deletion/addition.
    incomplete_before = bool(set(capture.gaps) & {"baseline_unavailable", "scan_budget"})
    incomplete_after = "scan_budget" in issues
    rows = []
    missing = {"exists": False, "version": None, "artifact_id": None}
    for path in sorted(set(capture.baseline) | set(manifest)):
        before, after = capture.baseline.get(path), manifest.get(path)
        if before is None and incomplete_before or after is None and incomplete_after:
            continue
        before, after = before or missing, after or missing
        if before["exists"] == after["exists"] and before["version"] == after["version"]:
            if before["version"] is None:
                gaps = sorted(set(gaps + ["content_unavailable"]))
            continue
        identifier = new_id()
        row = ChangeRecord(
            id=identifier,
            operation_id=command.operation_id,
            item_index=len(rows),
            run_id=run.id,
            root_run_id=run.root_run_id or run.id,
            workspace_id=run.workspace_id,
            execution_binding_id=getattr(run, "execution_binding_id", None),
            path=path,
            before_version=before["version"],
            after_version=after["version"],
            before_exists=before["exists"],
            after_exists=after["exists"],
            before_artifact_id=before["artifact_id"],
            after_artifact_id=after["artifact_id"],
            source="command",
            attribution="observed",
            state="confirmed",
            gaps=gaps,
        )
        db.add(row)
        rows.append(row)
    capture.state, capture.gaps = "closed", gaps
    db.flush()
    _release_unreferenced_snapshots(db, run)
    _emit(db, run.id, rows, captures=[capture], observation=True)


def _finish_command_safely(identifier):
    from workbench.modules.processes.domain import CommandExecution

    with Session(engine()) as db:
        command = db.get(CommandExecution, identifier)
        try:
            finish_command(db, command)
            db.commit()
        except (OSError, ValueError, AppError):
            db.rollback()
            capture = db.scalar(
                select(CommandCapture).where(CommandCapture.operation_id == identifier)
            )
            if capture and capture.state == "pending":
                capture.gaps = sorted(set(capture.gaps + ["final_capture_unavailable"]))
                # Remain pending: a later explicit reconciliation can retry observation.
                db.commit()


def finish_command_safely(identifier):
    try:
        _finish_command_safely(identifier)
    except Exception:
        logger.exception("Could not persist command change closure for %s", identifier)


def _content(db, identifier, exists):
    if not exists:
        return ""
    artifact = db.get(Artifact, identifier) if identifier else None
    if artifact is None or artifact.kind != "change_snapshot":
        return None
    try:
        path = storage_path(artifact.storage_path)
        if path.stat().st_size > SNAPSHOT_LIMIT:
            return None
        data = path.read_bytes()
        if digest(data) != artifact.content_hash or b"\x00" in data:
            return None
        return data.decode("utf-8")
    except (OSError, UnicodeError, AppError):
        return None


def _line_changes(before, after):
    if not _diffable(before, after):
        return None, None
    added = removed = 0
    for tag, a, b, c, d in difflib.SequenceMatcher(
        a=before.splitlines(keepends=True), b=after.splitlines(keepends=True), autojunk=False
    ).get_opcodes():
        if tag in {"replace", "insert"}:
            added += d - c
        if tag in {"replace", "delete"}:
            removed += b - a
    return added, removed


def _diffable(before, after):
    return (
        before is not None
        and after is not None
        and len(before.encode()) + len(after.encode()) <= DIFF_COMPUTE_BYTES
        and len(before.splitlines()) + len(after.splitlines()) <= DIFF_COMPUTE_LINES
    )


def _revision(rows, captures):
    values = [(row.id, row.state, row.version, row.gaps) for row in rows]
    values += [(row.id, row.state, row.version, row.gaps) for row in captures]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:24]


def _aggregate(db, rows):
    files, gaps, groups = [], [], {}
    for row in rows:
        gaps.extend(row.gaps)
        if row.state != "confirmed":
            gaps.append("unconfirmed_write")
        if row.state == "intent":
            continue  # An intended replacement is not evidence that bytes were written.
        groups.setdefault((row.workspace_id, row.path), []).append(row)
    chains = {}
    for (_, path), records in sorted(groups.items()):
        first, last = records[0], records[-1]
        contiguous = all(
            previous.after_exists == current.before_exists
            and previous.after_version == current.before_version
            and (not previous.after_exists or previous.after_version is not None)
            for previous, current in zip(records, records[1:], strict=False)
        )
        if not contiguous:
            gaps.append("discontinuous_version_chain")
        if (
            contiguous
            and first.before_exists == last.after_exists
            and first.before_version == last.after_version
            and (not first.before_exists or first.before_version is not None)
        ):
            continue
        before = _content(db, first.before_artifact_id, first.before_exists)
        after = _content(db, last.after_artifact_id, last.after_exists)
        added, removed = _line_changes(before, after) if contiguous else (None, None)
        if added is None:
            gaps.append("diff_unavailable")
        files.append(
            dto.ChangedFile(
                id=last.id,
                path=path,
                kind="added"
                if not first.before_exists
                else "deleted"
                if not last.after_exists
                else "modified",
                added_lines=added,
                removed_lines=removed,
                before_version=first.before_version,
                after_version=last.after_version,
                attribution="managed"
                if all(row.attribution == "managed" for row in records)
                else "observed",
            )
        )
        chains[last.id] = (first, last, contiguous)
    return files, sorted(set(gaps)), chains


def summary_for_visible(db, visible_operation_ids, run_id=None):
    identifiers = list(visible_operation_ids)
    query = select(ChangeRecord).where(ChangeRecord.operation_id.in_(identifiers))
    capture_query = select(CommandCapture).where(CommandCapture.operation_id.in_(identifiers))
    if run_id:
        scope = _run_tree(select(Run.id).where(Run.id == run_id))
        query = query.where(ChangeRecord.run_id.in_(select(scope.c.id)))
        capture_query = capture_query.where(CommandCapture.run_id.in_(select(scope.c.id)))
    rows = list(db.scalars(query.order_by(ChangeRecord.created_at, ChangeRecord.id)))
    captures = list(
        db.scalars(capture_query.order_by(CommandCapture.created_at, CommandCapture.id))
    )
    files, gaps, _ = _aggregate(db, rows)
    gaps = sorted(set(gaps + [gap for capture in captures for gap in capture.gaps]))
    pending = any(capture.state == "pending" for capture in captures)
    captured = {row.operation_id for row in rows} | {row.operation_id for row in captures}
    operations = select(Operation).where(Operation.id.in_(identifiers))
    if run_id:
        operations = operations.where(Operation.run_id.in_(select(scope.c.id)))
    missing = any(
        operation.tool_name
        in {
            "write_file",
            "apply_patch",
            "edit_files",
            "edit_notebook",
            "start_command",
            "exec_command",
        }
        and operation.effect_status != "not_started"
        and operation.id not in captured
        for operation in db.scalars(operations)
    )
    if missing:
        gaps = sorted(set(gaps + ["change_evidence_unavailable"]))
    coverage = "pending" if pending else "unknown" if missing else "partial" if gaps else "complete"
    known = not (set(gaps) - {"shared_directory_observation"}) and all(
        file.added_lines is not None and file.removed_lines is not None for file in files
    )
    return dto.ChangeSetView(
        revision=_revision(rows, captures),
        coverage=coverage,
        gaps=gaps,
        files=files,
        totals=dto.ChangeTotals(
            files=len(files),
            added_lines=sum(file.added_lines for file in files) if known else None,
            removed_lines=sum(file.removed_lines for file in files) if known else None,
        ),
    )


def _visible_ids(db, user_id, thread_id):
    from workbench.modules.runs.activity import visible_operations

    # Permission and branch-prefix rules are enforced before adding descendants.
    identifiers = [
        operation.id for operation in db.scalars(visible_operations(db, user_id, thread_id))
    ]
    owned = select(Run.id).where(Run.thread_id == thread_id, Run.user_id == user_id)
    tree = _run_tree(owned)
    identifiers += list(
        db.scalars(
            select(Operation.id)
            .join(Run)
            .where(Operation.run_id.in_(select(tree.c.id)), Run.user_id == user_id)
        )
    )
    return sorted(set(identifiers))


def _run_tree(seed):
    tree = seed.cte("visible_change_runs", recursive=True)
    return tree.union(select(Run.id).where(Run.parent_run_id == tree.c.id, Run.kind == "agent"))


def view(db, user_id, thread_id, run_id=None):
    from workbench.modules.runs.activity import visible_operations

    result = summary_for_visible(db, _visible_ids(db, user_id, thread_id), run_id)
    inherited_spawn = (
        visible_operations(db, user_id, thread_id)
        .join(Run)
        .where(Run.thread_id != thread_id, Operation.tool_name == "spawn_agent")
    )
    if run_id:
        tree = _run_tree(select(Run.id).where(Run.id == run_id))
        inherited_spawn = inherited_spawn.where(Operation.run_id.in_(select(tree.c.id)))
    if db.scalar(inherited_spawn.limit(1)) is not None:
        result.gaps = sorted(set(result.gaps + ["inherited_child_scope_unavailable"]))
        if result.coverage == "complete":
            result.coverage = "partial"
        result.totals.added_lines = result.totals.removed_lines = None
    return result


def details(db, user_id, thread_id, change_id, run_id=None):
    identifiers = _visible_ids(db, user_id, thread_id)
    query = select(ChangeRecord).where(ChangeRecord.operation_id.in_(identifiers))
    if run_id:
        scope = _run_tree(select(Run.id).where(Run.id == run_id))
        query = query.where(ChangeRecord.run_id.in_(select(scope.c.id)))
    rows = list(db.scalars(query.order_by(ChangeRecord.created_at, ChangeRecord.id)))
    _, gaps, chains = _aggregate(db, rows)
    if change_id not in chains:
        raise AppError("change_not_found", "变更不存在或不属于当前会话可见历史。", 404)
    first, last, contiguous = chains[change_id]
    before = _content(db, first.before_artifact_id, first.before_exists)
    after = _content(db, last.after_artifact_id, last.after_exists)
    if not _diffable(before, after) or not contiguous:
        return dto.ChangeDetails(path=last.path, coverage="partial")
    output = "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{last.path}",
            tofile=f"b/{last.path}",
        )
    )
    truncated = len(output.encode()) > DIFF_LIMIT
    if truncated:
        output = output.encode()[:DIFF_LIMIT].decode("utf-8", errors="ignore")
    return dto.ChangeDetails(
        path=last.path,
        diff=output,
        truncated=truncated,
        coverage="partial" if gaps or truncated else "complete",
    )
