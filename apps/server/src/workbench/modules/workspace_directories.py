"""Authenticated, read-only folder selection on the workbench host."""

import heapq
import os
from pathlib import Path

from workbench.contracts.base import PublicResponse
from workbench.contracts.errors import AppError

PAGE_SIZE = 200


class DirectoryEntry(PublicResponse):
    name: str
    path: str


class DirectoryListing(PublicResponse):
    path: str
    parent_path: str | None
    entries: list[DirectoryEntry]
    places: list[DirectoryEntry]
    next_cursor: str | None


def _sort_key(name: str):
    return name.casefold(), name


def _folders(entries, show_hidden, after):
    for entry in entries:
        if (not show_hidden and entry.name.startswith(".")) or (
            after is not None and _sort_key(entry.name) <= _sort_key(after)
        ):
            continue
        try:
            if entry.is_dir():
                yield {"name": entry.name, "path": entry.path}
        except OSError:
            continue


def browse(path: str | None = None, *, show_hidden=False, after: str | None = None) -> dict:
    try:
        directory = Path(path) if path else Path.home()
        if not directory.is_absolute():
            raise AppError("invalid_directory", "请选择已有文件夹。", 422)
        directory = directory.resolve(strict=True)
        with os.scandir(directory) as entries:
            selected = heapq.nsmallest(
                PAGE_SIZE + 1,
                _folders(entries, show_hidden, after),
                key=lambda item: _sort_key(item["name"]),
            )
    except PermissionError as error:
        raise AppError(
            "directory_forbidden", "没有权限打开此文件夹，请选择其他位置。", 403
        ) from error
    except (OSError, ValueError, RuntimeError) as error:
        raise AppError(
            "directory_unavailable", "文件夹不存在或无法打开，请选择其他位置。", 422
        ) from error
    home = Path.home()
    places = []
    for name, location in (
        ("主目录", home),
        ("桌面", home / "Desktop"),
        ("文档", home / "Documents"),
        ("外部设备", Path("/media")),
        ("挂载目录", Path("/mnt")),
        ("文件系统", Path(directory.anchor)),
    ):
        if location.is_dir():
            places.append({"name": name, "path": str(location)})
    return {
        "path": str(directory),
        "parent_path": str(directory.parent) if directory.parent != directory else None,
        "entries": selected[:PAGE_SIZE],
        "places": places,
        "next_cursor": selected[PAGE_SIZE - 1]["name"] if len(selected) > PAGE_SIZE else None,
    }
