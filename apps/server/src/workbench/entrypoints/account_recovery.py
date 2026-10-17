"""Offline local-terminal owner recovery; no password argument or HTTP route."""

import getpass
import socket
import sys
import warnings

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.account_recovery import assert_resources_stopped
from workbench.infrastructure.database import engine
from workbench.infrastructure.mutations import quiescence
from workbench.infrastructure.vault.errors import VaultError
from workbench.modules.identity_service import recover_owner_password
from workbench.modules.processes.native import RunnerError


def _assert_listener_stopped(settings) -> None:
    host = {"0.0.0.0": "127.0.0.1", "localhost": "127.0.0.1", "::": "::1"}.get(
        settings.host, settings.host
    )
    try:
        with socket.create_connection((host, settings.port), timeout=1):
            raise SystemExit("配置端口仍有监听服务；请先核对并停止，未修改账号。")
    except ConnectionRefusedError:
        return
    except OSError:
        # A timeout, inaccessible address or invalid bind is not shutdown proof.
        raise SystemExit("无法确认配置端口已停止，未修改账号。") from None


def recover() -> None:
    from workbench.entrypoints.cli import instance_lock, read_state

    if not sys.stdin.isatty():
        raise SystemExit("账号恢复须在本机交互终端输入新密码，不能通过参数或管道传入口令。")
    settings = get_settings()
    first = second = None
    try:
        with instance_lock():
            if read_state():
                raise SystemExit("请先执行 stop 并确认所有受管组件已退出，再恢复账号。")
            _assert_listener_stopped(settings)
            # Do not hold a DB maintenance window while a person types. Recheck
            # all proofs inside the exclusive gate immediately before mutation.
            with Session(engine()) as db:
                assert_resources_stopped(db)
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                first = getpass.getpass("本机所有者新密码（12–1024字符）：")
                second = getpass.getpass("再次输入新密码：")
            if first != second:
                raise SystemExit("两次输入不一致，未修改账号。")
            with quiescence() as boundary, Session(engine()) as db, db.begin():
                boundary.assert_held()
                _assert_listener_stopped(settings)
                assert_resources_stopped(db)
                receipt = recover_owner_password(db, first, services_stopped=True)
        print(f"账号密码已恢复，旧会话已撤销：{receipt.sessions_revoked}；凭据保险库需独立解锁。")
    except AppError as error:
        raise SystemExit(error.message) from None
    except (getpass.GetPassWarning, EOFError):
        raise SystemExit("无法安全读取终端口令，未修改账号。") from None
    except SQLAlchemyError:
        raise SystemExit(
            "无法核对数据库或提交恢复，请先检查本实例数据库；未报告恢复成功。"
        ) from None
    except (OSError, ValueError, VaultError, RunnerError):
        raise SystemExit("原生资源记录无法安全核对，未修改账号。") from None
    finally:
        first = second = None
