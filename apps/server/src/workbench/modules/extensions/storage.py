"""Small immutable package snapshots; never follow package-supplied links."""

import hashlib
import os
import stat
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from workbench.contracts.errors import AppError
from workbench.modules.files.storage import storage_path

MAX_FILES = 5000
MAX_FILE_BYTES = 16 * 1024**2
MAX_PACKAGE_BYTES = 64 * 1024**2
IGNORED = {".git", "__pycache__", "node_modules", ".venv", ".cache"}


def relative_path(value: str) -> PurePosixPath:
    raw = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\0" in value
        or raw.is_absolute()
        or ".." in raw.parts
        or ":" in value
        or any(len(part) > 255 for part in raw.parts)
    ):
        raise AppError(
            "extension_path_invalid", "扩展包路径必须留在包内，不能包含越界或特殊入口。", 422
        )
    return raw


def contained(root: Path, value: str) -> Path:
    relative = relative_path(value)
    probe = root
    for part in relative.parts:
        probe /= part
        if (
            probe.is_symlink()
            or getattr(probe.lstat() if probe.exists() else None, "st_file_attributes", 0) & 0x400
        ):
            raise AppError("extension_link_denied", "扩展包暂不接收符号链接或重解析点。", 422)
    target = probe.resolve()
    if not target.is_relative_to(root.resolve()):
        raise AppError("extension_path_invalid", "扩展包路径越界。", 422)
    return target


@contextmanager
def staging():
    parent = storage_path("extensions/staging")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="import-", dir=parent) as directory:
        yield Path(directory)


def copy_directory(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise AppError("extension_source_missing", "请选择真实存在的扩展目录。", 422)
    source = source.resolve()
    if target.resolve().is_relative_to(source):
        raise AppError("extension_recursive_source", "不能把扩展缓存的父目录作为导入来源。", 422)
    total = count = 0
    for folder, children, names in os.walk(source, followlinks=False):
        children[:] = sorted(item for item in children if item not in IGNORED)
        for name in children + sorted(names):
            path = Path(folder) / name
            relative = path.relative_to(source).as_posix()
            checked = contained(source, relative)
            if checked.is_dir():
                continue
            info = checked.stat()
            if not stat.S_ISREG(info.st_mode):
                raise AppError("extension_file_type", "扩展包只能包含普通文件和目录。", 422)
            count += 1
            total += info.st_size
            if count > MAX_FILES or total > MAX_PACKAGE_BYTES or info.st_size > MAX_FILE_BYTES:
                raise AppError(
                    "extension_size_limit", "扩展包超过5000文件、单文件16MiB或总64MiB限制。", 413
                )
            destination = target / relative
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            # O_NOFOLLOW closes the common read-side symlink replacement window.
            descriptor = os.open(checked, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                with os.fdopen(descriptor, "rb", closefd=False) as incoming:
                    data = incoming.read(MAX_FILE_BYTES + 1)
                if len(data) != info.st_size:
                    raise AppError(
                        "extension_source_changed", "导入期间源文件变化，请重新导入。", 409
                    )
            finally:
                os.close(descriptor)
            destination.write_bytes(data)


def unpack_zip(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_PACKAGE_BYTES:
        raise AppError("extension_archive_invalid", "请选择不超过64MiB的ZIP扩展包。", 422)
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES:
                raise AppError("extension_size_limit", "ZIP包文件数量超过限制。", 413)
            total, seen = 0, set()
            for entry in entries:
                relative = relative_path(entry.filename.rstrip("/"))
                key = relative.as_posix().casefold()
                mode = (entry.external_attr >> 16) & 0xFFFF
                if key in seen or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                    raise AppError("extension_archive_entry", "ZIP包包含重复路径或特殊文件。", 422)
                seen.add(key)
                total += entry.file_size
                if total > MAX_PACKAGE_BYTES or entry.file_size > MAX_FILE_BYTES:
                    raise AppError("extension_size_limit", "ZIP解压后大小超过扩展包限制。", 413)
                destination = target / relative
                if entry.is_dir():
                    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with archive.open(entry) as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
                if len(data) != entry.file_size:
                    raise AppError("extension_archive_invalid", "ZIP条目长度不完整。", 422)
                destination.write_bytes(data)
    except (zipfile.BadZipFile, RuntimeError, OSError):
        raise AppError("extension_archive_invalid", "ZIP包无法完整读取，未安装。", 422) from None


def file_manifest(root: Path) -> dict[str, dict]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        checked = contained(root, path.relative_to(root).as_posix())
        data = checked.read_bytes()
        result[path.relative_to(root).as_posix()] = {
            "bytes": len(data),
            "digest": hashlib.sha256(data).hexdigest(),
        }
    return result


def publish(root: Path, user_id: str, identifier: str, digest: str) -> str:
    relative = f"extensions/packages/{user_id}/{identifier}/{digest}"
    target = storage_path(relative)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not target.exists():
        # All imports stage on this filesystem, so publication is an atomic rename.
        os.replace(root, target)
    return relative
