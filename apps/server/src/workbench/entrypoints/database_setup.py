"""TTY-only setup for an existing loopback PostgreSQL; never provisions a server."""

import getpass
import ipaddress
import json
import os
import stat
import sys
import warnings
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlencode

import psycopg
from workbench.config import get_settings
from workbench.infrastructure.vault.private import check_private, private_directory, write_json


class _SetupFailure(Exception):
    """Only fixed, safe user-facing messages belong here."""


def _path_check(path: Path) -> None:
    for candidate in (*reversed(path.parents), path):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise _SetupFailure("数据目录不能经过符号链接或重解析点；未保存配置。")
        if not stat.S_ISDIR(info.st_mode):
            raise _SetupFailure("数据目录路径包含非目录项目；未保存配置。")


def _check_target(directory: Path) -> Path:
    _path_check(directory)
    target = directory / "database.json"
    try:
        target.lstat()
    except FileNotFoundError:
        pass
    else:
        raise _SetupFailure("数据库配置已存在或路径被占用，请先核对；不会覆盖。")
    if directory.exists():
        private_directory(directory)
    runtime = directory / "runtime"
    if runtime.exists() or runtime.is_symlink():
        private_directory(runtime)
        lock = runtime / "launcher.lock"
        try:
            info = lock.lstat()
        except FileNotFoundError:
            pass
        else:
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or getattr(info, "st_file_attributes", 0) & 0x400
                or (os.name != "nt" and info.st_uid != os.geteuid())
            ):
                raise _SetupFailure("启动器锁文件无法确认安全；未保存配置。")
    return target


def _prepare_directory(directory: Path) -> None:
    missing = []
    cursor = directory
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for path in reversed(missing):
        private_directory(path, create=True)
    private_directory(directory)


def _no_override(settings) -> None:
    # Even an explicitly empty variable should be removed, not silently shadowed.
    if any(name.upper() == "WORKBENCH_DATABASE_URL" for name in os.environ):
        raise _SetupFailure("请先移除 WORKBENCH_DATABASE_URL 环境覆盖，再运行配置向导。")
    if settings.database_url is not None:
        raise _SetupFailure("当前设置包含数据库连接覆盖；未保存另一个连接。")


def _field(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(prompt + suffix + "：").strip()
    value = value or default
    if (
        not value
        or len(value.encode("utf-8")) > 63
        or not all(character.isprintable() for character in value)
    ):
        raise _SetupFailure("输入为空、过长或含控制字符；未保存配置。")
    return value


def _host(value: str) -> str:
    if value.lower() == "localhost":
        # A deterministic loopback alias avoids DNS/service configuration changing the destination.
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise _SetupFailure("本版仅允许回环 IP 或 localhost；未连接其他网络。") from None
    if not address.is_loopback or "%" in value:
        raise _SetupFailure("本版仅允许回环地址；未连接其他网络。")
    return str(address)


@contextmanager
def _without_pg_environment():
    # This entrypoint is an offline CLI, not a reusable threaded HTTP connection helper.
    # libpq environment/service overrides must not redirect the validated destination.
    saved = {name: value for name, value in os.environ.items() if name.upper().startswith("PG")}
    try:
        for name in saved:
            os.environ.pop(name, None)
        yield
    finally:
        for name in tuple(os.environ):
            if name.upper().startswith("PG"):
                os.environ.pop(name, None)
        os.environ.update(saved)


def _verify(host: str, port: int, database: str, user: str, password: str) -> str:
    try:
        with _without_pg_environment():
            with psycopg.connect(
                host=host,
                hostaddr=host,
                port=port,
                dbname=database,
                user=user,
                password=password,
                passfile=os.devnull,
                sslmode="disable",
                gssencmode="disable",
                connect_timeout=5,
                application_name="workbench-database-setup",
                options="-c default_transaction_read_only=on -c statement_timeout=5000 "
                "-c lock_timeout=5000",
            ) as connection:
                row = connection.execute(
                    "SELECT current_setting('server_version_num')::integer, "
                    "current_setting('server_version'), current_database(), current_user, "
                    "host(inet_server_addr()), inet_server_port(), "
                    "current_setting('transaction_read_only')"
                ).fetchone()
                if (
                    not row
                    or not isinstance(row[0], int)
                    or row[0] <= 0
                    or row[2] != database
                    or row[3] != user
                    or ipaddress.ip_address(row[4]) != ipaddress.ip_address(host)
                    or row[5] != port
                    or row[6] != "on"
                ):
                    raise _SetupFailure("数据库返回的身份或只读验证不一致；未保存配置。")
                # The server version is shown as a bounded printable field, never raw diagnostics.
                version = str(row[1])
                if len(version) > 128 or not all(character.isprintable() for character in version):
                    raise _SetupFailure("数据库版本响应无效；未保存配置。")
                connection.rollback()
                return version
    except _SetupFailure:
        raise
    except Exception:
        raise _SetupFailure(
            "无法连接或只读验证 PostgreSQL，请核对地址、角色和口令；未保存配置。"
        ) from None


def _database_url(host: str, port: int, database: str, user: str, password: str) -> str:
    authority = f"[{host}]" if ":" in host else host
    # These constraints persist the same numeric destination. Read-only options are probe-only.
    query = urlencode({"hostaddr": host, "sslmode": "disable", "gssencmode": "disable"})
    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{authority}:{port}/"
        f"{quote(database, safe='')}?{query}"
    )


def configure() -> None:
    """Interactively publish one new private database.json; SystemExit is always sanitized."""
    if not sys.stdin.isatty():
        raise SystemExit("数据库配置只接受本机交互终端；不能从管道读取口令。")
    publishing = False
    try:
        settings = get_settings()
        _no_override(settings)
        directory = settings.data_dir.absolute()
        if ".." in directory.parts:
            raise _SetupFailure("数据目录必须是明确路径；未保存配置。")
        _check_target(directory)
        _prepare_directory(directory)
        # Lazy import avoids a CLI import cycle; share its exact launcher lock and state protocol.
        from workbench.entrypoints.cli import instance_lock, read_state

        with instance_lock():
            _check_target(directory)
            if read_state():
                raise _SetupFailure(
                    "存在启动器进程记录（含未知或已退出记录）；请先核对，未保存配置。"
                )
            print("配置已有的本机 PostgreSQL；不会创建数据库、迁移、修改角色或启动应用。")
            print("仅限回环连接，使用专用数据库和最小权限应用角色；本地连接不启用 TLS。")
            host = _host(_field("数据库主机", "127.0.0.1"))
            raw_port = _field("端口", "5432")
            if (
                not raw_port.isascii()
                or not raw_port.isdecimal()
                or not 1 <= int(raw_port) <= 65535
            ):
                raise _SetupFailure("数据库端口必须在 1..65535；未保存配置。")
            port = int(raw_port)
            database, user = _field("应用专用数据库名"), _field("应用专用角色名")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                password = getpass.getpass("数据库口令（不回显）：")
            if not password or len(password) > 4096 or "\x00" in password:
                raise _SetupFailure("数据库口令为空、过长或格式无效；未保存配置。")
            version = _verify(host, port, database, user, password)
            print("只读验证通过，PostgreSQL " + version + "。")
            print(
                "目标："
                + host
                + ":"
                + str(port)
                + "；数据库="
                + json.dumps(database, ensure_ascii=False)
                + "；角色="
                + json.dumps(user, ensure_ascii=False)
            )
            print("准备保存到：" + json.dumps(str(directory / "database.json"), ensure_ascii=False))
            print("此检查不证明迁移权限、完整应用兼容性或受管备份能力。")
            if input("输入“保存”确认，其他输入取消：").strip() != "保存":
                print("已取消，未保存数据库配置。")
                return
            _no_override(settings)
            target = _check_target(directory)
            if read_state():
                raise _SetupFailure("启动器记录已变化；未保存配置。")
            publishing = True
            write_json(
                target,
                {"database_url": _database_url(host, port, database, user, password)},
                exclusive=True,
            )
            check_private(target)
            print("数据库配置已保存。应用尚未启动。")
    except _SetupFailure as error:
        raise SystemExit(str(error)) from None
    except (KeyboardInterrupt, EOFError):
        message = (
            "保存结果未确认，请核对受保护的 database.json；不会覆盖现有文件。"
            if publishing
            else "已取消，未保存数据库配置。"
        )
        raise SystemExit(message) from None
    except Exception:
        message = (
            "保存结果未确认，请核对受保护的 database.json；不会覆盖现有文件。"
            if publishing
            else "数据库配置无法安全完成，请核对目录权限与启动器状态；未保存配置。"
        )
        raise SystemExit(message) from None


if __name__ == "__main__":
    configure()
