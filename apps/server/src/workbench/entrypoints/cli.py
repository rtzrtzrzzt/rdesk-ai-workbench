import argparse
import ipaddress
import json
import os
import signal
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from workbench.composition import tables  # noqa: F401
from workbench.config import PROJECT_ROOT, get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import database_ready, engine, now
from workbench.infrastructure.native_process import capture, matches, terminate_owned
from workbench.infrastructure.vault import VaultClient, VaultError
from workbench.infrastructure.vault.private import check_private, private_directory, read_json


def migrate() -> None:
    from workbench.infrastructure.mutations import quiescence

    with quiescence() as boundary:
        # assert_held's proof SELECT opened a transaction. End that snapshot
        # before checkpoint setup builds concurrent indexes; the session-level
        # advisory gate remains held across COMMIT.
        boundary.connection.commit()
        _migrate()


def _migrate() -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "apps/server/migrations"))
    with engine().connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(82139703)"))
        connection.commit()
        try:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
            dsn = get_settings().database_dsn().replace("postgresql+psycopg://", "postgresql://", 1)
            with PostgresSaver.from_conn_string(dsn) as saver:
                saver.setup()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(82139703)"))
            connection.commit()


def runtime_directory() -> Path:
    try:
        base = private_directory(get_settings().ensure_data_dir())
        return private_directory(base / "runtime", create=True)
    except (VaultError, OSError):
        raise SystemExit("运行目录必须由当前系统账户独占；未修改现有进程记录。") from None


@contextmanager
def instance_lock():
    path = runtime_directory() / "launcher.lock"
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            stream.write(b"\0")
            stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def process_matches(record: dict) -> bool:
    return matches(record)


def state_path() -> Path:
    return runtime_directory() / "processes.json"


def read_state() -> dict:
    try:
        path = state_path()
        _check_state_file(path)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            value = json.loads(stream.read(1024 * 1024 + 1))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, VaultError):
        raise SystemExit("进程记录无法安全读取；原文件已保留，未停止或覆盖进程。") from None


def _check_state_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
        raise ValueError("Invalid process record file")
    if os.name == "nt":
        check_private(path)
    elif info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise ValueError("Process record file is not owned or is writable by others")


def write_state(state):
    """Publish complete 0600 state atomically, preserving legacy records as data."""
    path = state_path()
    if path.exists() or path.is_symlink():
        _check_state_file(path)
    fd, temporary = tempfile.mkstemp(prefix=".processes-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
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


def _url(settings):
    return settings.access_policy().primary_origin


def _http_verification(settings):
    context = ssl.create_default_context()
    if settings.ssl_certfile:
        context.load_verify_locations(cafile=str(settings.ssl_certfile))
    return context


def _owns_api_listener(record, settings):
    """A positive health reply alone does not identify the recorded API process."""
    if not process_matches(record):
        return False
    try:
        import psutil
    except ImportError:
        return False
    try:
        return any(
            connection.status == psutil.CONN_LISTEN
            and connection.laddr.port == settings.port
            and (
                (
                    settings.host == "localhost"
                    and ipaddress.ip_address(connection.laddr.ip).is_loopback
                )
                or connection.laddr.ip == settings.host
            )
            for connection in psutil.Process(record["pid"]).net_connections(kind="inet")
        ) and process_matches(record)
    except (psutil.Error, OSError, ValueError):
        return False


def _healthy_owned(state, client, settings, url):
    required = {"vault-broker", "api", "worker", "file-worker", "media-worker"}
    if not required.issubset(state) or not all(process_matches(state[key]) for key in required):
        return False
    if not _owns_api_listener(state["api"], settings):
        return False
    try:
        if not client.get(url + "/health/live").is_success or not database_ready():
            return False
        if not workers_ready(state):
            return False
        if (
            read_json(settings.data_dir / "vault" / "broker.json").get("pid")
            != state["vault-broker"]["pid"]
        ):
            return False
        VaultClient(settings.data_dir).status()  # Locked/uninitialized is still a ready broker.
        return True
    except (httpx.TransportError, VaultError, SQLAlchemyError, AppError):
        return False


def launch_component(component: str) -> dict:
    instance_id = str(uuid4())
    log_path = runtime_directory() / f"{component}.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "workbench.entrypoints.cli",
                component,
                "--instance-id",
                instance_id,
            ],
            cwd=PROJECT_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
    try:
        return {
            **capture(process.pid, instance_id),
            "instance_id": instance_id,
            "log": str(log_path),
        }
    except Exception:
        process.terminate()
        process.wait(timeout=5)
        raise


def workers_ready(state: dict) -> bool:
    from workbench.modules.runs.domain import WorkerHeartbeat

    with Session(engine()) as db:
        for component, queue in (
            ("worker", "agent"),
            ("file-worker", "files"),
            ("media-worker", "media"),
        ):
            if component not in state or not process_matches(state[component]):
                return False
            heartbeat = db.get(WorkerHeartbeat, state[component]["instance_id"])
            if (
                not heartbeat
                or heartbeat.queue != queue
                or (now() - heartbeat.last_seen_at).total_seconds() > 30
            ):
                return False
    return True


def start(open_browser: bool) -> None:
    settings = get_settings()
    policy = settings.access_policy()
    policy.validate_tls_files()
    with instance_lock():
        state = read_state()
        url = _url(settings)
        with httpx.Client(
            trust_env=False, timeout=1, verify=_http_verification(settings)
        ) as client:
            if _healthy_owned(state, client, settings, url):
                print(f"已复用当前 AI 工作台：{url}")
                if open_browser:
                    webbrowser.open(url)
                return
            if state:
                raise SystemExit(
                    "已有进程记录但当前实例未全部健康确认；记录已保留。"
                    "请先查看 status/日志并用 stop 核对停止，未迁移、重启或覆盖当前任务。"
                )
            try:
                if client.get(url + "/health/live").is_success:
                    raise SystemExit(f"端口已有未核实归属的服务，未迁移或启动：{url}")
            except httpx.TransportError:
                pass
            if not database_ready():
                configuration = settings.database_configuration()
                if settings.database_url or not configuration:
                    raise SystemExit(
                        "数据库未配置或显式连接暂不可用。首次安装请显式准备数据库；"
                        "已有外部实例请先恢复连接，未自动创建或切换数据库。"
                    )
                arguments = [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts/dev/postgres.py"),
                    "resume",
                ]
                if configuration[0] != settings.data_dir / "database.json" or not configuration[
                    1
                ].get("managed_postgres"):
                    raise SystemExit("配置的外部数据库暂不可用；未自动创建或切换数据库。")
                arguments += ["--data-dir", str(settings.data_dir)]
                subprocess.run(
                    arguments,
                    check=True,
                )
                engine.cache_clear()
            migrate()
            for component in ("vault-broker", "api", "worker", "file-worker", "media-worker"):
                if component in state and process_matches(state[component]):
                    continue
                if component == "api":
                    try:
                        response = client.get(url + "/health/live")
                        if response.is_success:
                            raise SystemExit(f"端口已有服务，请先确认其归属：{url}")
                    except httpx.TransportError:
                        pass
                state[component] = launch_component(component)
                write_state(state)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if _healthy_owned(state, client, settings, url):
                    print(f"AI 工作台：{url}")
                    if open_browser:
                        webbrowser.open(url)
                    return
                for component, record in state.items():
                    if not process_matches(record):
                        raise SystemExit(f"{component} 启动失败，请查看 {record['log']}")
                time.sleep(0.2)
            raise SystemExit("API、后台 Worker 或凭据服务尚未就绪，请查看原生启动日志。")


def stop(*, force=False) -> None:
    with instance_lock():
        state = read_state()
        failures = []
        components = (
            "api",
            "worker",
            "file-worker",
            "media-worker",
            "browser-companion",
            "desktop-companion",
            "vault-broker",
        )
        for component in (*components, *sorted(set(state) - set(components))):
            record = state.get(component)
            if component not in state:
                continue
            if component not in components:
                failures.append(f"{component}: 未识别组件，原记录已保留，未发送信号。")
                continue
            try:
                result = terminate_owned(record, timeout=15, force=force)
            except Exception:
                failures.append(f"{component}: 原生停止无法确认，记录已保留。")
                continue
            if result.status != "confirmed":
                failures.append(f"{component}: 身份或退出未确认，记录已保留（{result.reason}）。")
                continue
            del state[component]
            write_state(state)
        # Close admission and stop executors before the final durable-resource
        # sweep. A command launched during worker shutdown must be included.
        from workbench.modules.automation import runtime as automation_runtime
        from workbench.modules.processes.service import stop_all

        try:
            automation_runtime.stop()
        except Exception:
            failures.append("automation: 可选原生执行器关闭未确认，保留独立记录。")
        try:
            if any(not row.owned_resources_closed for row in stop_all()):
                failures.append("commands: 长命令资源关闭尚未全部确认，真实记录已保留。")
        except Exception:
            failures.append("commands: 无法核对长命令清理，未按名称或裸PID终止进程。")
        try:
            from workbench.modules.media.resources import stop_all as stop_media

            if not stop_media():
                failures.append("media: 本机生成进程尚未全部确认关闭，原回执与预留已保留。")
        except Exception:
            failures.append("media: 无法核对生成进程清理，未影响独立主模型。")
        if failures:
            raise SystemExit("工作台停止尚未全部确认：\n" + "\n".join(failures))
        write_state(state)
        print("工作台 API 与 Worker 已停止；独立模型和数据库保留。")


def recover_account() -> None:
    from workbench.entrypoints.account_recovery import recover

    recover()


def main() -> None:
    parser = argparse.ArgumentParser(prog="workbench")
    parser.add_argument(
        "command",
        choices=[
            "start",
            "stop",
            "status",
            "doctor",
            "configure-database",
            "recover-account",
            "migrate",
            "api",
            "worker",
            "file-worker",
            "media-worker",
            "vault-broker",
            "browser-companion",
            "desktop-companion",
            "settings-catalog",
        ],
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--instance-id", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--query", default="")
    parser.add_argument("--schemas", action="store_true")
    args = parser.parse_args()
    if args.command in {"worker", "file-worker", "media-worker"} and args.instance_id:
        os.environ["WORKBENCH_WORKER_ID"] = args.instance_id
    if args.command == "settings-catalog":
        from workbench.modules.configuration.catalog import discover

        print(json.dumps(discover(args.query, args.schemas), ensure_ascii=False, indent=2))
    elif args.command == "migrate":
        migrate()
        print("应用迁移和 PostgreSQL 检查点已就绪。")
    elif args.command == "start":
        start(not args.no_browser)
    elif args.command == "stop":
        stop(force=args.force)
    elif args.command == "recover-account":
        recover_account()
    elif args.command == "configure-database":
        from workbench.entrypoints.database_setup import configure

        configure()
    elif args.command in {"status", "doctor"}:
        state = read_state()
        print(
            json.dumps(
                {
                    "database_ready": database_ready(),
                    "components": {
                        key: {
                            "running": process_matches(value),
                            "identity": "matched"
                            if process_matches(value)
                            else "unconfirmed_or_exited",
                            "log": value.get("log") if isinstance(value, dict) else None,
                        }
                        for key, value in state.items()
                    },
                    "url": _url(get_settings()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "api":
        import uvicorn

        uvicorn.run(
            "workbench.entrypoints.api:app",
            **get_settings().access_policy().uvicorn_kwargs,
        )
    elif args.command == "worker":
        from workbench.entrypoints.worker import main as worker_main

        worker_main()
    elif args.command == "file-worker":
        from workbench.entrypoints.file_worker import main as file_worker_main

        file_worker_main()
    elif args.command == "media-worker":
        from workbench.entrypoints.media_worker import main as media_worker_main

        media_worker_main()
    elif args.command == "vault-broker":
        from workbench.infrastructure.vault.broker import VaultBroker

        broker = VaultBroker(get_settings().ensure_data_dir())
        signal.signal(signal.SIGTERM, lambda *_: broker.stopping.set())
        signal.signal(signal.SIGINT, lambda *_: broker.stopping.set())
        broker.serve(cleanup_stale=True)
    elif args.command in {"browser-companion", "desktop-companion"}:
        raise SystemExit(f"{args.command} CLI 尚未接线，未启动任何组件。")


if __name__ == "__main__":
    main()
