"""Explicit UTF-8 source selections frozen at publication, never live mounts."""

import errno
import json
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path, PurePosixPath
from uuid import UUID

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.vault import VaultError
from workbench.infrastructure.vault.private import private_directory, read_private
from workbench.modules import workspace_service
from workbench.modules.agent_services import dto
from workbench.modules.tools import filesystem

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_FILES = 512
MAX_ENTRIES = 4096
MAX_SECONDS = 5
MARKER = ".rdesk-source-snapshot.json"


def _fail(code="agent_source_invalid", message="来源选择无法安全冻结，请核对明确授权的文本路径。"):
    return AppError(code, message, 422, "policy")


def _selection(definition):
    value = (
        definition
        if isinstance(definition, dto.Definition)
        else dto.Definition.model_validate(definition)
    )
    paths = []
    for raw in value.source_paths:
        if (
            not raw
            or len(raw) > 4096
            or "\\" in raw
            or "\0" in raw
            or any(part == ".." for part in raw.split("/"))
        ):
            raise _fail()
        path = PurePosixPath(raw)
        if path.is_absolute() or str(path) == "." or ":" in path.parts[0] or MARKER in path.parts:
            raise _fail()
        canonical = path.as_posix()
        if canonical not in paths:
            paths.append(canonical)
    if bool(value.source_workspace_id) != bool(paths):
        raise _fail(message="来源项目与非空的明确相对路径列表必须同时提供；不能默认分享整个项目。")
    return value, sorted(paths)


def _plain_root(root):
    root = Path(root)
    if not root.is_absolute():
        raise _fail()
    for candidate in (root, *root.parents):
        if candidate.is_symlink():
            raise _fail("agent_source_symlink", "来源或快照路径不能经过符号链接。")
    if not root.is_dir():
        raise _fail()
    return root


def _authorized(root, relative):
    candidate = filesystem.authorized_path(root, relative)
    lexical = root / relative
    for item in (lexical, *lexical.parents):
        if item == root:
            break
        if item.is_symlink():
            raise _fail("agent_source_symlink", "明确分享的来源中包含符号链接，未分享任何部分。")
    if candidate != lexical:
        raise _fail("agent_source_symlink", "来源路径不能经过符号链接。")
    return candidate


def validate(db, owner_id, definition):
    try:
        value, paths = _selection(definition)
        if paths:
            workspace = workspace_service.get(db, owner_id, str(value.source_workspace_id))
            root = _plain_root(workspace.root_path)
            for relative in paths:
                info = _authorized(root, relative).lstat()
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise _fail()
        return {**value.model_dump(mode="json"), "source_paths": paths}
    except OSError:
        raise _fail("agent_source_unavailable", "明确选择的来源路径不存在或不可读取。") from None


def _deadline(until):
    if time.monotonic() > until:
        raise _fail("agent_source_budget", "来源快照超过时间预算，请缩小明确分享的路径。")


def _open_beneath(root_fd, relative, *, directory=False):
    """Anchor each path component to descriptors; symlink swaps cannot escape."""
    descriptors = []
    current = root_fd
    parts = PurePosixPath(relative).parts
    try:
        for part in parts[:-1]:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            descriptors.append(current)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if directory:
            flags |= os.O_DIRECTORY
        return os.open(parts[-1], flags, dir_fd=current)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _scan(root, root_fd, selected, until, *, authorize=True):
    files, directories, seen = {}, {}, set()
    pending = list(reversed(selected))
    total = 0
    while pending:
        _deadline(until)
        relative = pending.pop()
        if relative in seen:
            continue
        seen.add(relative)
        if len(seen) > MAX_ENTRIES:
            raise _fail("agent_source_budget", "来源扫描超过条目预算，请缩小分享范围。")
        candidate = _authorized(root, relative) if authorize else root / relative
        info = candidate.lstat()
        if stat.S_ISDIR(info.st_mode):
            directories[relative] = info
            descriptor = _open_beneath(root_fd, relative, directory=True)
            try:
                with os.scandir(descriptor) as entries:
                    children = []
                    for entry in entries:
                        _deadline(until)
                        if len(seen) + len(pending) + len(children) >= MAX_ENTRIES:
                            raise _fail(
                                "agent_source_budget", "来源扫描超过条目预算，请缩小分享范围。"
                            )
                        children.append(str(PurePosixPath(relative) / entry.name))
                pending.extend(reversed(sorted(children)))
            finally:
                os.close(descriptor)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            total += info.st_size
            if info.st_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
                raise _fail("agent_source_budget", "来源文件、总字节数或文件数量超过快照预算。")
            files[relative] = info
        else:
            raise _fail("agent_source_unsupported", "来源含链接或非常规文件，未分享任何部分。")
    return files, directories, total


def _read(root_fd, relative, expected, until, *, text=True):
    descriptor = _open_beneath(root_fd, relative)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_dev, before.st_ino) != (expected.st_dev, expected.st_ino)
        ):
            raise _fail("agent_source_changed", "来源在快照准备期间变化，请重新发布。")
        chunks, size = [], 0
        while True:
            _deadline(until)
            chunk = os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise _fail("agent_source_budget", "来源单文件超过2MiB快照预算。")
        after = os.fstat(descriptor)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise _fail("agent_source_changed", "来源在快照准备期间变化，请重新发布。")
        content = b"".join(chunks)
        if text:
            try:
                content.decode("utf-8")
            except UnicodeDecodeError:
                raise _fail(
                    "agent_source_encoding", "来源快照仅支持UTF-8文本和代码，未分享任何部分。"
                ) from None
            if b"\0" in content:
                raise _fail("agent_source_binary", "来源快照不接受二进制内容，未分享任何部分。")
        return content
    finally:
        os.close(descriptor)


def _verify_stable(root_fd, entries, until):
    for relative, expected in entries.items():
        _deadline(until)
        descriptor = _open_beneath(root_fd, relative, directory=stat.S_ISDIR(expected.st_mode))
        try:
            observed = os.fstat(descriptor)
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(expected, field) != getattr(observed, field) for field in fields):
                raise _fail("agent_source_changed", "来源在快照准备期间变化，请重新发布。")
        finally:
            os.close(descriptor)


def _canonical_parent():
    base = private_directory(get_settings().ensure_data_dir())
    api = private_directory(base / "agent-api", create=True)
    return private_directory(api / "source-revisions", create=True)


def _expected(revision_id, value, paths):
    return {
        "schema": 1,
        "revision_id": str(UUID(str(revision_id))),
        "source_workspace_id": str(value.source_workspace_id),
        "source_paths": paths,
    }


def _marker(target, expected):
    _plain_root(target)
    if stat.S_IMODE(target.stat().st_mode) != 0o500:
        raise _fail("agent_source_snapshot_invalid", "现有来源快照的只读状态无法确认。")
    location = target / MARKER
    if stat.S_IMODE(location.lstat().st_mode) != 0o400:
        raise _fail("agent_source_snapshot_invalid", "现有来源快照标记无法确认。")
    marker = json.loads(read_private(location))
    if (
        not isinstance(marker, dict)
        or any(marker.get(key) != item for key, item in expected.items())
        or type(marker.get("file_count")) is not int
        or not 0 <= marker["file_count"] <= MAX_FILES
        or type(marker.get("byte_count")) is not int
        or not 0 <= marker["byte_count"] <= MAX_TOTAL_BYTES
    ):
        raise _fail("agent_source_snapshot_invalid", "现有来源快照标记与发布版本不一致。")
    return marker


def _discard(stage):
    if stage.exists():
        for directory, _, _ in os.walk(stage):
            Path(directory).chmod(0o700)
        shutil.rmtree(stage)


def _seal(stage, marker):
    _write(stage / MARKER, json.dumps(marker, ensure_ascii=False).encode("utf-8"))
    for directory, _, files in os.walk(stage, topdown=False):
        for filename in files:
            (Path(directory) / filename).chmod(0o400)
        Path(directory).chmod(0o500)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write(path, content):
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _publish(stage, target, expected):
    try:
        os.rename(stage, target)
    except OSError as error:
        if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
            raise
        _marker(target, expected)
    descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return _marker(target, expected)


def prepare_revision(db, owner_id, revision_id, definition):
    """Freeze once after revision flush, before the publication transaction commits."""
    value, paths = _selection(definition)
    if not paths:
        return None
    stage = None
    try:
        until = time.monotonic() + MAX_SECONDS
        expected = _expected(revision_id, value, paths)
        parent = _canonical_parent()
        target = parent / expected["revision_id"]
        if target.exists() or target.is_symlink():
            return _marker(target, expected)
        workspace = workspace_service.get(db, owner_id, str(value.source_workspace_id))
        root = _plain_root(workspace.root_path)
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            files, directories, _ = _scan(root, root_fd, paths, until)
            stage = Path(tempfile.mkdtemp(prefix=".source-stage-", dir=parent))
            total = 0
            for relative in sorted(directories):
                (stage / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
            for relative, info in sorted(files.items()):
                content = _read(root_fd, relative, info, until)
                total += len(content)
                if total > MAX_TOTAL_BYTES:
                    raise _fail("agent_source_budget", "来源总字节数超过快照预算。")
                output = stage / relative
                output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                _write(output, content)
            _verify_stable(root_fd, {**directories, **files}, until)
        finally:
            os.close(root_fd)
        marker = {**expected, "file_count": len(files), "byte_count": total}
        _seal(stage, marker)
        _deadline(until)
        return _publish(stage, target, expected)
    except AppError:
        raise
    except (OSError, ValueError, VaultError):
        raise _fail(
            "agent_source_snapshot_failed", "来源快照未能完整冻结，未发布任何部分。"
        ) from None
    finally:
        if stage is not None:
            _discard(stage)


def materialize(db, namespace, revision_id, definition):
    """Copy the published canonical snapshot; retries only verify the marker."""
    value, paths = _selection(definition)
    scope = {"source_revision": str(revision_id) if paths else None, "source_paths": paths}
    if not paths:
        return scope
    stage = None
    try:
        until = time.monotonic() + MAX_SECONDS
        expected = _expected(revision_id, value, paths)
        canonical = _canonical_parent() / expected["revision_id"]
        frozen = _marker(canonical, expected)
        workspace = workspace_service.get(db, namespace.owner_user_id, namespace.workspace_id)
        root = _plain_root(workspace.root_path)
        parent = private_directory(root / "_sources", create=True)
        target = parent / expected["revision_id"]
        if target.exists() or target.is_symlink():
            if _marker(target, expected) != frozen:
                raise _fail("agent_source_snapshot_invalid", "会话来源快照与发布版本不一致。")
            return scope
        descriptor = os.open(canonical, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            files, directories, _ = _scan(canonical, descriptor, paths, until, authorize=False)
            stage = Path(tempfile.mkdtemp(prefix=".source-stage-", dir=parent))
            total = 0
            for relative in sorted(directories):
                (stage / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
            for relative, info in sorted(files.items()):
                content = _read(descriptor, relative, info, until, text=False)
                total += len(content)
                output = stage / relative
                output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                _write(output, content)
            _verify_stable(descriptor, {**directories, **files}, until)
        finally:
            os.close(descriptor)
        if len(files) != frozen["file_count"] or total != frozen["byte_count"]:
            raise _fail("agent_source_snapshot_invalid", "发布快照的完整性计数无法确认。")
        _seal(stage, frozen)
        _deadline(until)
        _publish(stage, target, expected)
        return scope
    except AppError:
        raise
    except (OSError, ValueError, VaultError):
        raise _fail(
            "agent_source_snapshot_failed", "来源快照未能完整准备，未分享任何部分。"
        ) from None
    finally:
        if stage is not None:
            _discard(stage)
