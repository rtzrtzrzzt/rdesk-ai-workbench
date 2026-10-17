"""Official native PG clients; mutations limited to this invocation's marked random databases."""

import importlib.util
import os
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from workbench.config import PROJECT_ROOT, get_settings
from workbench.contracts.errors import AppError


class NativePostgres:
    def __init__(self, bind):
        engine = getattr(bind, "engine", bind)
        source = make_url(engine.url)
        spec = importlib.util.spec_from_file_location(
            "_workbench_native_postgres", PROJECT_ROOT / "scripts/dev/postgres.py"
        )
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        settings = get_settings()
        configuration = settings.database_configuration()
        if configuration and configuration[0] == settings.data_dir / "database.json":
            managed = configuration[1].get("managed_postgres")
            if not isinstance(managed, dict) or managed.get("schema") != 1:
                raise AppError(
                    "backup_pg_unsupported", "此数据库未提供受管原生维护配置。", 422, "capability"
                )
            if (
                managed.get("state_dir") != str((settings.data_dir / "postgres").absolute())
                or managed.get("tools_dir")
                != str((settings.data_dir / "components/postgres").absolute())
                or managed.get("role") != "ai"
                or managed.get("admin") != "workbench_admin"
                or type(managed.get("port")) is not int
            ):
                raise AppError("backup_pg_configuration", "受管数据库目录或身份配置不一致。", 409)
            helper.configure_data_directory(settings.data_dir, port=managed["port"], read_only=True)
        if (
            source.host not in {"127.0.0.1", "localhost", "::1"}
            or source.port != helper.PORT
            or source.username != helper.ROLE
        ):
            raise AppError(
                "backup_pg_unsupported",
                "此维护适配仅支持项目私有原生 PostgreSQL；不更改外部实例。",
                422,
                "capability",
            )
        self.binaries = helper.binaries()
        self.env = helper.environment()
        passfile = Path(self.env["PGPASSFILE"])
        if (
            not self.binaries
            or not passfile.is_file()
            or passfile.stat().st_uid != os.getuid()
            or passfile.stat().st_mode & 0o077
        ):
            raise AppError(
                "backup_pg_unavailable",
                "私有 PostgreSQL 工具或受保护凭据不可用。",
                503,
                "dependency",
            )
        self.host, self.port = "127.0.0.1", helper.PORT
        self.role, self.admin, self.source_database = helper.ROLE, helper.ADMIN, source.database
        self.passfile, self.owned = str(passfile), {}
        with self.connect("postgres", admin=True) as connection:
            role = connection.execute(
                "SELECT rolcreatedb,rolsuper FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            owner = connection.execute(
                "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s",
                (self.source_database,),
            ).fetchone()
            if not role or not any(role) or not owner or owner[0] != self.role:
                raise AppError(
                    "backup_pg_permission",
                    "私有管理员缺少创建隔离库权限或源库不属于应用角色。",
                    503,
                    "permission",
                )
        self.client_version = subprocess.check_output(
            [str(self.binaries / "pg_dump"), "--version"], env=self.env, text=True
        ).strip()

    def connect(self, database, *, admin=False, autocommit=False):
        return psycopg.connect(
            host=self.host,
            port=self.port,
            dbname=database,
            user=self.admin if admin else self.role,
            passfile=self.passfile,
            connect_timeout=5,
            autocommit=autocommit,
        )

    def create_database(self, prefix):
        if prefix not in {"workbench_backup_", "workbench_restore_"}:
            raise AppError("maintenance_database_scope", "隔离数据库前缀无效。", 422)
        name, marker = prefix + uuid4().hex, "workbench-maintenance:" + uuid4().hex
        with self.connect("postgres", admin=True, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0").format(
                    sql.Identifier(name), sql.Identifier(self.role)
                )
            )
            try:
                connection.execute(
                    sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                        sql.Identifier(name), sql.Literal(marker)
                    )
                )
            except Exception:
                connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                raise
        self.owned[name] = marker
        return name

    def drop_database(self, name):
        marker = self.owned.get(name)
        if marker is None:
            raise AppError(
                "maintenance_database_scope", "拒绝清理本轮未创建的数据库。", 409, "permission"
            )
        with self.connect("postgres", admin=True, autocommit=True) as connection:
            actual = connection.execute(
                "SELECT shobj_description(oid,'pg_database'),pg_get_userbyid(datdba) "
                "FROM pg_database WHERE datname=%s",
                (name,),
            ).fetchone()
            if actual is None:
                self.owned.pop(name)
                return
            if actual != (marker, self.role):
                raise AppError(
                    "maintenance_database_scope",
                    "隔离数据库所有权标记改变，拒绝删除。",
                    409,
                    "permission",
                )
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )
        self.owned.pop(name)

    def _args(self, tool, database):
        return [
            str(self.binaries / tool),
            "--no-password",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--username",
            self.role,
            "--dbname",
            database,
        ]

    @staticmethod
    def _terminate(process):
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def _run(self, arguments, check):
        # An unnamed 0600 temporary descriptor keeps SQL diagnostics out of final backups/logs.
        with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                arguments, env=self.env, stdin=subprocess.DEVNULL, stdout=output, stderr=errors
            )
            try:
                while process.poll() is None:
                    check()
                    time.sleep(0.1)
                check()
                if process.returncode:
                    raise AppError(
                        "maintenance_pg_command_failed",
                        "PostgreSQL 备份/恢复命令失败；未输出含业务内容的原始诊断。",
                        502,
                        "dependency",
                    )
            finally:
                self._terminate(process)

    def clone_source(self, database, check):
        if database not in self.owned or not database.startswith("workbench_backup_"):
            raise AppError("maintenance_database_scope", "只允许向本次隔离临时库复制。", 409)
        dump_args = self._args("pg_dump", self.source_database) + [
            "--format=custom",
            "--no-owner",
            "--no-privileges",
        ]
        restore_args = self._args("pg_restore", database) + [
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            "--single-transaction",
        ]
        dump = restore = None
        with tempfile.TemporaryFile() as errors:
            try:
                dump = subprocess.Popen(
                    dump_args,
                    env=self.env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=errors,
                )
                restore = subprocess.Popen(
                    restore_args,
                    env=self.env,
                    stdin=dump.stdout,
                    stdout=subprocess.DEVNULL,
                    stderr=errors,
                )
                dump.stdout.close()
                while dump.poll() is None or restore.poll() is None:
                    check()
                    if restore.poll() not in {None, 0}:
                        break
                    time.sleep(0.1)
                check()
                if dump.poll() != 0 or restore.poll() != 0:
                    raise AppError(
                        "backup_pg_clone_failed",
                        "复制到隔离净化库失败；源数据库未修改。",
                        502,
                        "dependency",
                    )
            finally:
                self._terminate(restore)
                self._terminate(dump)

    def dump(self, database, path, check):
        if database not in self.owned:
            raise AppError("maintenance_database_scope", "最终导出仅来自本轮净化库。", 409)
        # Reserve an empty protected file; pg_dump opens/truncates this owned pathname.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        self._run(
            self._args("pg_dump", database)
            + ["--format=custom", "--no-owner", "--no-privileges", "--file", str(path)],
            check,
        )

    def validate_archive(self, path, check):
        self._run([str(self.binaries / "pg_restore"), "--list", str(path)], check)

    def restore(self, database, path, check):
        if database not in self.owned or not database.startswith("workbench_restore_"):
            raise AppError("maintenance_database_scope", "恢复仅允许本轮新建隔离数据库。", 409)
        self._run(
            self._args("pg_restore", database)
            + [
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                "--single-transaction",
                str(path),
            ],
            check,
        )
