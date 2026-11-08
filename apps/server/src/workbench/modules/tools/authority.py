"""Frozen local file authority; external API grants remain a separate boundary."""

from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from workbench.modules.workspaces import Workspace


def is_local_owner(run) -> bool:
    return getattr(run, "execution_origin", "owner") == "owner" and not getattr(
        run, "execution_binding_id", None
    )


def full_access_enabled(run) -> bool:
    return is_local_owner(run) and (
        run.config_snapshot.get("runtime_options", {}).get("approval_mode") == "full_access"
    )


@dataclass(frozen=True)
class FileAuthority:
    root: Path
    host_access: bool
    # Delegated children retain their explicit paths, even under a full-access parent.
    allowed_paths: tuple[Path, ...] | None = None


def for_run(db, run) -> FileAuthority:
    root = Path(db.get(Workspace, run.workspace_id).root_path).resolve()
    paths = None
    if run.parent_run_id:
        paths = tuple(
            (root / path).resolve() for path in (run.config_snapshot.get("allowed_paths") or ["."])
        )
    return FileAuthority(root, full_access_enabled(run), paths)


# Bound around the complete operation, including rule discovery and preflight.
# asyncio.to_thread propagates it; every exit and LangGraph interrupt resets it.
current_file_authority: ContextVar[FileAuthority | None] = ContextVar(
    "file_authority", default=None
)
