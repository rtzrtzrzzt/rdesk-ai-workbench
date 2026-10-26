"""Private operator profile and a controller lock, shared by both script entry points."""

from __future__ import annotations

import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / ".local/launcher"
PROFILE = CONTROL / "profile.json"


def load_profile(selected=None):
    path = Path(selected).expanduser().absolute() if selected else PROFILE
    # An explicitly selected application instance must never inherit another
    # instance's saved database or frontend. --profile makes selection explicit.
    explicit_instance = any(
        key in os.environ for key in ("WORKBENCH_DATA_DIR", "WORKBENCH_DATABASE_URL")
    )
    if selected is None and explicit_instance:
        return {}
    if not path.exists():
        if selected:
            raise RuntimeError("指定的启动配置不存在。")
        return {}
    from workbench.infrastructure.vault.private import read_json

    profile = read_json(path)
    if not isinstance(profile, dict) or profile.get("version") != 1:
        raise RuntimeError("启动配置格式不受支持；原配置已保留。")
    allowed = {"version", "environment", "database", "search", "model", "model_timeout_seconds"}
    if set(profile) - allowed:
        raise RuntimeError("启动配置含有未知字段。")
    environment = profile.get("environment", {})
    allowed_env = {
        "WORKBENCH_DATA_DIR",
        "WORKBENCH_DATABASE_URL",
        "WORKBENCH_FRONTEND_DIR",
        "WORKBENCH_HOST",
        "WORKBENCH_PORT",
        "WORKBENCH_LAN_ENABLED",
        "WORKBENCH_PUBLIC_ORIGINS",
        "WORKBENCH_SECURE_COOKIES",
        "WORKBENCH_SSL_CERTFILE",
        "WORKBENCH_SSL_KEYFILE",
        "WORKBENCH_COMMAND_MAX_ACTIVE_PER_RUN",
        "WORKBENCH_COMMAND_MAX_ACTIVE_TOTAL",
    }
    if not isinstance(environment, dict) or any(
        key not in allowed_env or not isinstance(value, str) for key, value in environment.items()
    ):
        raise RuntimeError("启动配置中的应用环境无效。")
    for key in ("database", "search", "model"):
        if profile.get(key, "auto") not in {"auto", "managed", "external", "disabled", "legacy"}:
            raise RuntimeError(f"{key}服务管理模式无效。")
    if any(profile.get(key) == "legacy" for key in ("search", "model")):
        raise RuntimeError("legacy仅适用于项目既有开发数据库。")
    timeout = profile.get("model_timeout_seconds", 2400)
    if type(timeout) is not int or not 60 <= timeout <= 86400:
        raise RuntimeError("模型启动等待时间必须在60至86400秒之间。")
    for key, value in environment.items():
        os.environ.setdefault(key, value)
    return profile


def read_control(name):
    path = CONTROL / name
    if not path.exists():
        return {}
    from workbench.infrastructure.vault.private import read_json

    return read_json(path)


def write_control(name, value):
    from workbench.infrastructure.vault.private import write_json

    control_directory()
    write_json(CONTROL / name, value, exclusive=False)


def control_directory():
    from workbench.infrastructure.vault.private import private_directory

    if CONTROL.parent.is_symlink():
        raise RuntimeError("启动器状态目录不能通过符号链接访问。")
    CONTROL.parent.mkdir(mode=0o700, exist_ok=True)
    return private_directory(CONTROL, create=True)


@contextmanager
def controller_lock(wait=False):
    control_directory()
    path = CONTROL / "controller.lock"
    if path.is_symlink():
        raise RuntimeError("启动器锁文件不能是符号链接。")
    with path.open("a+b") as stream:
        if os.name != "nt":
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        started = time.monotonic()
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    if not stream.read(1):
                        stream.write(b"\0")
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if not wait:
                    raise RuntimeError(
                        "另一项启动或关闭操作正在进行，请查看启动窗口或运行status。"
                    ) from None
                if time.monotonic() - started > 300:
                    raise RuntimeError("现有启动尚未结束取消清理，请查看status与日志。") from None
                time.sleep(0.2)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
