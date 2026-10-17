import hashlib
import os
import tempfile
import time
from pathlib import Path

from workbench.config import get_settings
from workbench.contracts.errors import AppError


def storage_path(relative: str) -> Path:
    root = get_settings().ensure_data_dir().resolve()
    raw = root.joinpath(relative)
    probe = root
    for part in Path(relative).parts:
        probe = probe / part
        if probe.is_symlink():
            raise AppError(
                "artifact_symlink_denied", "文件存储路径不能经过符号链接。", 409, "policy"
            )
    target = raw.resolve()
    if not target.is_relative_to(root) or target == root:
        raise AppError("artifact_path_invalid", "文件存储位置无效。", 409, "policy")
    return target


def publish_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".publishing-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def file_digest(path: Path, *, deadline: float | None = None) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("File verification exceeded its time budget")
            result.update(chunk)
    return result.hexdigest()
