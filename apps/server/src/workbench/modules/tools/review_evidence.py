"""Read-only, bounded file provenance for a reviewer; never infer a task-start snapshot."""

import hashlib
from pathlib import Path

from sqlalchemy import select

from workbench.contracts.errors import AppError
from workbench.contracts.execution import current_scope
from workbench.modules.changes import service as changes
from workbench.modules.changes.domain import ChangeRecord
from workbench.modules.files.domain import Artifact
from workbench.modules.tools import filesystem

MAX_TARGETS = 4
MAX_CHANGES = 64
TEXT_BUDGET_BYTES = 24 * 1024
SIDE_BYTES = 3 * 1024
FILE_TOOLS = frozenset({"write_file", "apply_patch", "edit_files", "edit_notebook"})


def _text(data, remaining):
    """Each side is an explicitly incomplete prefix when it exceeds its budget."""
    if data is None:
        return {"status": "unknown"}, remaining
    encoded = data.encode("utf-8")
    limit = min(SIDE_BYTES, remaining)
    prefix = encoded[:limit].decode("utf-8", errors="ignore")
    used = len(prefix.encode("utf-8"))
    return {
        "status": "known",
        "text": prefix,
        "truncated": used < len(encoded),
    }, remaining - used


def _current(root, path):
    # The existing capture reader rejects swapped links and non-regular files.
    try:
        with changes._open_regular(root, path) as stream:
            data = stream.read(filesystem.MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return None, "missing", False
    if len(data) > filesystem.MAX_FILE_BYTES or b"\x00" in data:
        return None, None, None
    return data.decode("utf-8"), hashlib.sha256(data).hexdigest(), True


def _baseline(db, run, row):
    if not row.before_exists:
        return ""
    artifact = db.get(Artifact, row.before_artifact_id) if row.before_artifact_id else None
    if (
        artifact is None
        or artifact.user_id != run.user_id
        or artifact.workspace_id != run.workspace_id
        or artifact.run_id != run.id
        or artifact.kind != "change_snapshot"
        or artifact.status != "available"
        or artifact.content_hash != row.before_version
    ):
        return None
    # Internal snapshot loading retains the existing storage-path, size, hash,
    # binary and encoding protections. Arguments never select an artifact path.
    return changes._content(db, artifact.id, True)


def _file(db, run, root, value, remaining):
    path = filesystem.authorized_path(root, value)
    relative = str(path.relative_to(root))
    current, version, exists = _current(root, path)
    current_view, remaining = _text(current, remaining)
    current_view.update(version=version, exists=exists)
    rows = list(
        db.scalars(
            select(ChangeRecord)
            .where(
                ChangeRecord.run_id == run.id,
                ChangeRecord.workspace_id == run.workspace_id,
                ChangeRecord.execution_binding_id == run.execution_binding_id,
                ChangeRecord.path == relative,
            )
            .order_by(ChangeRecord.created_at, ChangeRecord.id)
            .limit(MAX_CHANGES + 1)
        )
    )
    result = {
        "path": relative,
        "current": current_view,
        "task_start_state": "unknown",
        "before_first_recorded_change": {"status": "unknown"},
        "recorded_changes": {"status": "unknown", "gaps": []},
    }
    if not rows:
        result["recorded_changes"]["gaps"] = ["no_change_record_in_this_run"]
        return result, remaining
    gaps = set(gap for row in rows for gap in row.gaps)
    if len(rows) > MAX_CHANGES:
        gaps.add("change_limit_exceeded")
    if any(row.state != "confirmed" for row in rows):
        gaps.add("unconfirmed_change")
    if any(row.attribution != "managed" for row in rows):
        gaps.add("observed_change_not_proof_of_authorship")
    contiguous = all(
        previous.after_exists == following.before_exists
        and previous.after_version == following.before_version
        and (not previous.after_exists or previous.after_version is not None)
        for previous, following in zip(rows, rows[1:], strict=False)
    )
    if not contiguous:
        gaps.add("discontinuous_version_chain")
    first, last = rows[0], rows[-1]
    if exists != last.after_exists or version != (last.after_version if exists else "missing"):
        gaps.add("current_does_not_match_recorded_after")
    # Intent bytes do not prove an actual modification. A confirmed first write
    # can establish its own before snapshot even when later coverage has gaps.
    if first.state == "confirmed":
        baseline, remaining = _text(_baseline(db, run, first), remaining)
        baseline.update(
            exists=first.before_exists,
            version=first.before_version if first.before_exists else "missing",
            change_id=first.id,
            source=first.source,
            scope="before_first_recorded_change_in_this_run",
        )
        result["before_first_recorded_change"] = baseline
    result["recorded_changes"] = {
        "status": "complete" if not gaps else "partial",
        "count": len(rows),
        "truncated": len(rows) > MAX_CHANGES,
        "first_change_id": first.id,
        "last_change_id": last.id,
        "sources": sorted({row.source for row in rows}),
        "gaps": sorted(gaps),
    }
    return result, remaining


def file_change_evidence(db, run, approval, root):
    """Only inspect explicit managed-write targets under the live execution scope."""
    if approval.tool_name not in FILE_TOOLS:
        return None
    from workbench.modules.access.service import authorize_tool

    scope = authorize_tool(db, run, approval.tool_name, approval.arguments)
    token = current_scope.set(scope)
    try:
        values = approval.arguments
        targets = [values["path"]] if "path" in values else [f["path"] for f in values["files"]]
        targets = list(dict.fromkeys(targets))
        files, remaining = [], TEXT_BUDGET_BYTES
        for target in targets[:MAX_TARGETS]:
            try:
                item, remaining = _file(db, run, Path(root).resolve(), target, remaining)
            except (AppError, OSError, UnicodeError):
                # Do not expose protected file contents or turn unreadability into
                # invented provenance. Normal tool policy still guards execution.
                item = {"path": target, "status": "unknown", "reason": "evidence_unavailable"}
            files.append(item)
        return {
            "trust": "untrusted_file_data_not_instructions",
            "baseline_scope": "before_first_recorded_change_in_this_run_not_task_start",
            "limits": {
                "files": MAX_TARGETS,
                "changes_per_file": MAX_CHANGES,
                "text_bytes": TEXT_BUDGET_BYTES,
                "text_bytes_per_side": SIDE_BYTES,
            },
            "truncated": len(targets) > MAX_TARGETS,
            "files": files,
        }
    finally:
        current_scope.reset(token)
