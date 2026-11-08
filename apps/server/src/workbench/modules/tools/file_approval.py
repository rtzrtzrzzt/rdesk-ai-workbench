"""Cheap file preflight and narrow reuse of this Run's approved file creation."""

import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.contracts.execution import current_scope
from workbench.infrastructure.database import engine
from workbench.modules.changes.domain import ChangeRecord
from workbench.modules.conversations import Message
from workbench.modules.interactions import Approval
from workbench.modules.runs.domain import Run
from workbench.modules.tools import filesystem
from workbench.modules.tools.domain import Operation
from workbench.modules.workspaces import Workspace


def preflight(run_id, name, values):
    if name not in {"write_file", "apply_patch", "edit_files"}:
        return None
    from workbench.modules.access.service import authorize_tool

    with Session(engine()) as db:
        run = db.get(Run, run_id)
        scope = authorize_tool(db, run, name, values)
        root = Path(db.get(Workspace, run.workspace_id).root_path)
    token = current_scope.set(scope)
    try:
        if name == "edit_files":
            from workbench.modules.tools.file_queries import prepare_edits

            prepare_edits(root, values)
            return None
        return filesystem.prepare_file_change(root, values, patch=name == "apply_patch")
    except OSError:
        # Nothing has been submitted at this boundary; do not turn a failed
        # read into an unknown write outcome requiring reconciliation.
        raise AppError(
            "file_preflight_unavailable", "文件预检未能安全读取目标，本次尚未修改文件。", 409
        ) from None
    finally:
        current_scope.reset(token)


def rules_digest(run):
    rules = run.config_snapshot.get("context_manifest", {}).get("rules", [])
    versions = sorted((item["source_path"], item["version"]) for item in rules)
    return hashlib.sha256(json.dumps(versions, ensure_ascii=False).encode()).hexdigest()


def reusable_creation(db, run, name, prepared):
    """Never reuse an approval for an existing user file or a new user directive."""
    if (
        name not in {"write_file", "apply_patch"}
        or prepared is None
        or prepared.version == "missing"
        or not prepared.after.strip()
        or run.parent_run_id
        or getattr(run, "execution_binding_id", None)
    ):
        return None
    root = Path(db.get(Workspace, run.workspace_id).root_path).resolve()
    relative = prepared.path.relative_to(root)
    if any(part.startswith(".") for part in relative.parts) or relative.name in {
        "AGENTS.md",
        "SKILL.md",
    }:
        return None
    # A short, complete chain is required. Commands, other Runs and any unknown
    # effects fall back to model review even if their final bytes happen to match.
    rows = list(
        db.scalars(
            select(ChangeRecord)
            .where(
                ChangeRecord.workspace_id == run.workspace_id,
                ChangeRecord.path == relative.as_posix(),
            )
            .order_by(ChangeRecord.created_at, ChangeRecord.id)
            .limit(65)
        )
    )
    if not rows or len(rows) > 64 or rows[0].before_exists:
        return None
    previous = None
    creation = None
    for row in rows:
        operation = db.get(Operation, row.operation_id)
        if (
            row.run_id != run.id
            or row.execution_binding_id is not None
            or row.source != "managed"
            or row.attribution != "managed"
            or row.state != "confirmed"
            or row.gaps
            or not row.after_exists
            or not row.after_version
            or row.before_version != previous
            or row.before_exists != (previous is not None)
            or operation is None
            or operation.status != "succeeded"
            or operation.effect_status != "confirmed"
            or operation.tool_name not in {"write_file", "apply_patch"}
        ):
            return None
        if creation is None:
            creation = db.scalar(
                select(Approval).where(
                    Approval.operation_id == operation.id, Approval.status == "approved"
                )
            )
            from workbench.modules.tools.service import argument_digest

            if (
                creation is None
                or not creation.approved
                or creation.parameters_digest != argument_digest(operation.arguments)
                or (creation.review or {}).get("file_rules_digest") != rules_digest(run)
            ):
                return None
        previous = row.after_version
    if previous != prepared.version or db.scalar(
        select(Message.id)
        .where(
            Message.run_id == run.id,
            Message.role == "user",
            Message.created_at > creation.created_at,
        )
        .limit(1)
    ):
        return None
    return {
        "creation_approval_id": creation.id,
        "creation_change_id": rows[0].id,
        "last_change_id": rows[-1].id,
        "expected_version": prepared.version,
    }
