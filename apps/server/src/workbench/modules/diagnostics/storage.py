"""Bounded stat-only accounting beneath verified descriptors; no file bodies."""

import os
import stat
import time
from pathlib import Path

from workbench.modules.diagnostics import dto

MAX_ENTRIES = 20000
MAX_SECONDS = 2
MAX_DEPTH = 32


def _open_absolute(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe root")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            following = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = following
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def measure(
    root: Path,
    user_id: str,
    workspace_ids: list[str],
    database_bytes: int | None,
    workspace_limit_reached: bool = False,
) -> dto.Storage:
    started = time.monotonic()
    categories = {
        name: {
            "bytes": 0,
            "files": 0,
            "seen": False,
            "partial": False,
            "skipped": 0,
            "reason": "missing",
        }
        for name in [
            "artifacts",
            "staging",
            "uploads",
            "memory",
            "command_logs",
            "logs",
            "browser_profiles",
            "backups",
            "restore_staging",
        ]
    }
    examined = 0
    limit = False
    total = free = available = None
    unsupported = not (
        hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "fstatvfs")
        and os.scandir in os.supports_fd
        and os.open in os.supports_dir_fd
    )
    descriptor = None
    try:
        if unsupported:
            raise NotImplementedError
        descriptor = _open_absolute(root)
        fs = os.fstatvfs(descriptor)
        total, free, available = (
            fs.f_frsize * value for value in (fs.f_blocks, fs.f_bfree, fs.f_bavail)
        )

        def budget():
            nonlocal limit
            limit = examined >= MAX_ENTRIES or time.monotonic() - started >= MAX_SECONDS
            return not limit

        def scan(fd, category, depth=0, logs_only=False):
            nonlocal examined
            item = categories[category]
            item["seen"] = True
            if not item["partial"]:
                item["reason"] = "ok"
            with os.scandir(fd) as entries:
                for entry in entries:
                    if not budget() or depth > MAX_DEPTH:
                        item.update(partial=True, reason="scan_limit")
                        return
                    examined += 1
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISDIR(info.st_mode) and not logs_only:
                            child = os.open(
                                entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                            )
                            try:
                                scan(child, category, depth + 1)
                            finally:
                                os.close(child)
                        elif stat.S_ISREG(info.st_mode) and (
                            not logs_only or entry.name.endswith(".log")
                        ):
                            item["bytes"] += info.st_size
                            item["files"] += 1
                        elif stat.S_ISLNK(info.st_mode):
                            item["skipped"] += 1
                            item.update(partial=True, reason="path_denied")
                    except OSError:
                        item["skipped"] += 1
                        item.update(partial=True, reason="permission_denied")

        def visit(parts, category, logs_only=False):
            if not budget():
                categories[category].update(partial=True, reason="scan_limit")
                return
            current = os.dup(descriptor)
            try:
                for part in parts:
                    following = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
                    )
                    os.close(current)
                    current = following
                scan(current, category, logs_only=logs_only)
            except FileNotFoundError:
                pass
            except OSError:
                categories[category].update(partial=True, reason="path_denied")
            finally:
                os.close(current)

        for workspace_id in workspace_ids:
            for leaf, category in [
                ("artifacts", "artifacts"),
                ("staging", "staging"),
                ("uploads", "uploads"),
                ("memory", "memory"),
                ("commands", "command_logs"),
            ]:
                visit(("workspaces", workspace_id, leaf), category)
        for parts, category, logs_only in [
            (("users", user_id, "memory"), "memory", False),
            (("logs",), "logs", False),
            (("runtime",), "logs", True),
            (("browser", "profiles"), "browser_profiles", False),
            (("maintenance", "backups"), "backups", False),
            (("maintenance", "restores"), "restore_staging", False),
        ]:
            visit(parts, category, logs_only)
        if workspace_limit_reached:
            for category in ("artifacts", "staging", "uploads", "memory", "command_logs"):
                categories[category].update(partial=True, reason="scan_limit")
    except (OSError, ValueError, NotImplementedError):
        for item in categories.values():
            item.update(partial=True, reason="unsupported" if unsupported else "path_denied")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    measured = [
        dto.StorageCategory(
            category=name,
            apparent_bytes=item["bytes"] if item["seen"] else None,
            file_count=item["files"] if item["seen"] else None,
            skipped_entries=item["skipped"],
            status="partial" if item["partial"] else "observed" if item["seen"] else "unknown",
            reason_code=item["reason"],
        )
        for name, item in categories.items()
    ]
    measured.append(
        dto.StorageCategory(
            category="database",
            apparent_bytes=database_bytes,
            status="observed" if database_bytes is not None else "unknown",
            source="pg_database_size",
            reason_code="database_scope" if database_bytes is not None else "query_unavailable",
        )
    )
    return dto.Storage(
        status="observed"
        if total is not None
        and not limit
        and not workspace_limit_reached
        and all(c.status == "observed" for c in measured)
        else "partial"
        if total is not None
        else "unavailable",
        data_volume_total_bytes=total,
        data_volume_free_bytes=free,
        data_volume_available_bytes=available,
        categories=measured,
        examined_entries=examined,
        elapsed_ms=round((time.monotonic() - started) * 1000, 3),
        reason_code="scan_limit"
        if limit or workspace_limit_reached
        else "ok"
        if total is not None
        else "unsupported"
        if unsupported
        else "path_denied",
        capacity_source="data_directory_fstatvfs" if total is not None else "unavailable",
    )
