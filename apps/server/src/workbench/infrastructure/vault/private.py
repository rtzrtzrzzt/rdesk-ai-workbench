"""Private paths and atomic writes; neither this module nor imports touch disk."""

import json
import os
import stat
import tempfile
from pathlib import Path

from workbench.infrastructure.vault.errors import VaultError


def private_directory(path: Path, *, create: bool = False) -> Path:
    if not path.exists() and create:
        if os.name == "nt":
            from workbench.infrastructure.vault.windows_acl import create_private_directory

            create_private_directory(path)
        else:
            path.mkdir(mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise VaultError("INSECURE_PRIVATE_PATH", "vault目录不得是符号链接或其他类型。")
    check_private(path)
    return path


def check_private(path: Path) -> None:
    if os.name == "nt":
        from workbench.infrastructure.vault.windows_acl import check_private as check_acl

        check_acl(path)
        if getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise VaultError("INSECURE_PRIVATE_PATH", "vault不接受重解析点。")
    else:
        info = path.lstat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise VaultError("INSECURE_PRIVATE_PERMISSIONS", "vault路径必须由当前账户独占。")


def read_private(path: Path, maximum: int = 65536) -> bytes:
    check_private(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum or info.st_nlink != 1:
            raise VaultError("INVALID_PRIVATE_FILE", "vault私有文件类型、大小或链接不符合要求。")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(maximum + 1)
    finally:
        os.close(descriptor)


def write_private(path: Path, data: bytes, *, exclusive: bool = True) -> None:
    private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".vault-write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            try:
                os.link(temporary, path)
            except FileExistsError:
                raise VaultError("ALREADY_INITIALIZED", "现有vault文件不会被覆盖。") from None
        else:
            if path.exists():
                check_private(path)
            os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(read_private(path))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except VaultError:
        raise
    except Exception:
        raise VaultError("INVALID_VAULT_FILE", "vault元数据无效，拒绝生成替代主密钥。") from None


def write_json(path: Path, value: dict, *, exclusive: bool = True) -> None:
    write_private(path, json.dumps(value, separators=(",", ":")).encode(), exclusive=exclusive)
