#!/usr/bin/env python3
"""Project-private native PostgreSQL 16 for Ubuntu source development."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / ".tools/postgres"
PREFIX = TOOLS / "root"
STATE = ROOT / ".local/postgres"
DATA = STATE / "data"
CONFIG = ROOT / ".local/development/database.json"
PORT = 55432
ROLE = "ai"
ADMIN = "workbench_admin"
DATABASE = "workbench"
DATA_ROOT: Path | None = None


def _no_symlinks(path):
    probe = path
    while True:
        if probe.is_symlink():
            raise RuntimeError("Managed PostgreSQL paths cannot traverse symlinks.")
        if probe == probe.parent:
            return
        probe = probe.parent


def _private(path, *, directory=False):
    _no_symlinks(path)
    info = path.lstat()
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid_type or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError(
            "Managed PostgreSQL path ownership/type/private permissions are invalid."
        )
    if not directory and info.st_nlink != 1:
        raise RuntimeError("Managed PostgreSQL private files cannot have multiple hardlinks.")


def _read_json(path):
    _private(path)
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise RuntimeError("Managed PostgreSQL metadata exceeds its budget.")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RuntimeError("Managed PostgreSQL metadata is invalid; nothing was reset.") from None
    if not isinstance(value, dict):
        raise RuntimeError("Managed PostgreSQL metadata must be an object.")
    return value


def _port(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 65535:
        raise RuntimeError("Managed PostgreSQL port must be an integer in 1024..65535.")
    return value


def _managed_descriptor(root, port):
    return {
        "schema": 1,
        "state_dir": str(root / "postgres"),
        "tools_dir": str(root / "components/postgres"),
        "port": port,
        "role": ROLE,
        "admin": ADMIN,
    }


def _check_connection(path, root, port):
    value = _read_json(path)
    descriptor = value.get("managed_postgres")
    if (
        not isinstance(descriptor, dict)
        or type(descriptor.get("schema")) is not int
        or descriptor != _managed_descriptor(root, port)
    ):
        raise RuntimeError(
            "Existing database configuration is not this managed data-root instance."
        )
    try:
        parsed = urlsplit(value["database_url"])
        valid = (
            parsed.scheme == "postgresql"
            and parsed.hostname == "127.0.0.1"
            and parsed.port == port
            and parsed.username == ROLE
            and parsed.password
            and parsed.path == "/" + DATABASE
            and not parsed.query
            and not parsed.fragment
        )
    except (ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise RuntimeError("Managed database connection identity does not match its data root.")


def configure_data_directory(data_dir: Path, *, port: int | None = None, read_only=False):
    """Opt-in data-root layout. Import/default invocation keeps legacy paths untouched.

    A selected ephemeral port is a persisted choice, not a reservation; start
    still refuses an occupied port. Read-only mode never mkdirs or writes.
    """
    global DATA_ROOT, STATE, DATA, TOOLS, PREFIX, CONFIG, PORT
    root = Path(data_dir)
    requested_port = port
    if not root.is_absolute() or ".." in root.parts:
        raise RuntimeError("Managed PostgreSQL data_dir must be an absolute canonical path.")
    _no_symlinks(root)
    state, tools, config = root / "postgres", root / "components/postgres", root / "database.json"
    settings, data = state / "settings.json", state / "data"
    if port is not None:
        _port(port)
    for directory in (root, state, root / "components", tools):
        _no_symlinks(directory)
        if directory.exists():
            _private(directory, directory=True)
    for file in (settings, config):
        _no_symlinks(file)
    if data.exists():
        _private(data, directory=True)
    if not settings.exists():
        if read_only or data.exists() or config.exists() or (state / "passwords.json").exists():
            raise RuntimeError(
                "Settings missing; existing data/configuration will not be adopted or reset."
            )
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        state.mkdir(mode=0o700, exist_ok=True)
        if port is None:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
        descriptor, temporary = tempfile.mkstemp(prefix=".settings-", dir=state)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"schema": 1, "port": port}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, settings)
            except FileExistsError:
                pass  # A concurrent first preparation won; validate its selection below.
        finally:
            Path(temporary).unlink(missing_ok=True)
        directory = os.open(state, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    selected = _read_json(settings)
    if (
        set(selected) != {"schema", "port"}
        or type(selected.get("schema")) is not int
        or selected.get("schema") != 1
    ):
        raise RuntimeError("Unsupported managed settings; no metadata was rewritten.")
    chosen = _port(selected.get("port"))
    if requested_port is not None and requested_port != chosen:
        raise RuntimeError(
            "Requested port differs from this instance persisted port; no switch attempted."
        )
    if config.exists():
        _check_connection(config, root, chosen)
    if not read_only:
        for directory in (root / "components", tools):
            _no_symlinks(directory)
            if directory.exists():
                _private(directory, directory=True)
            else:
                directory.mkdir(mode=0o700)
    _no_symlinks(tools / "root")
    for file in (
        state / "passwords.json",
        state / "pgpass",
        state / "control.lock",
        state / "postgres.log",
        data / "PG_VERSION",
        data / "postmaster.pid",
        data / "postgresql.conf",
        data / "pg_hba.conf",
    ):
        _no_symlinks(file)
        if file.exists():
            _private(file)
    DATA_ROOT, STATE, DATA, TOOLS, CONFIG, PORT = root, state, data, tools, config, chosen
    PREFIX = tools / "root"
    return _managed_descriptor(root, chosen)


def run(args, *, env=None, input=None, cwd=None, sensitive=False):
    result = subprocess.run(
        [str(arg) for arg in args], input=input, text=True, capture_output=True, env=env, cwd=cwd
    )
    if result.returncode:
        detail = (
            "credential operation failed; details suppressed"
            if sensitive
            else result.stderr.strip()
        )
        raise RuntimeError(f"{Path(args[0]).name} failed ({result.returncode}): {detail}")
    return result.stdout.strip()


def private_write(path, content):
    if DATA_ROOT is not None:
        _no_symlinks(path)
        if path.exists():
            _private(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


def environment():
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
    triple = sysconfig.get_config_var("MULTIARCH")
    if not triple:
        raise RuntimeError("Cannot determine native library architecture.")
    libraries = [PREFIX / "usr/lib" / triple, PREFIX / "lib" / triple]
    env["LD_LIBRARY_PATH"] = ":".join(map(str, libraries))
    env["PGPASSFILE"] = str(STATE / "pgpass")
    env["PGCONNECT_TIMEOUT"] = "5"
    # In particular PGHOSTADDR can redirect a libpq connection even when -h is
    # explicit. Only the managed passfile/timeout survive inherited PG options.
    return env


def binaries():
    candidates = [PREFIX / "usr/lib/postgresql/16/bin", Path("/usr/lib/postgresql/16/bin")]
    pg_config = shutil.which("pg_config")
    if pg_config:
        candidate = Path(run([pg_config, "--bindir"]))
        if candidate not in candidates:
            candidates.append(candidate)
    for directory in candidates:
        if all((directory / name).is_file() for name in ("postgres", "initdb", "pg_ctl", "psql")):
            version = run([directory / "postgres", "--version"], env=environment())
            if version.startswith("postgres (PostgreSQL) 16."):
                return directory
    return None


def prepare():
    found = binaries()
    if not found:
        system = Path("/etc/os-release").read_text()
        if "ID=ubuntu\n" not in system or 'VERSION_ID="24.04"' not in system:
            raise RuntimeError(
                "Automatic preparation requires Ubuntu 24.04; install native PostgreSQL16 first."
            )
        packages = TOOLS / "packages"
        packages.mkdir(parents=True, exist_ok=True)
        PREFIX.mkdir(parents=True, exist_ok=True)
        # APT uses the host default repositories and verifies package metadata.
        # No package installation, system configuration or maintainer scripts run.
        selected = {}
        for name in ("postgresql-16", "postgresql-client-16"):
            policy = run(["apt-cache", "policy", name])
            candidate = next(
                (
                    line.split(":", 1)[1].strip()
                    for line in policy.splitlines()
                    if line.strip().startswith("Candidate:")
                ),
                None,
            )
            if not candidate or candidate == "(none)":
                raise RuntimeError(f"No default-source candidate is available for {name}.")
            selected[name] = candidate
        if len(set(selected.values())) != 1:
            raise RuntimeError(
                "Default-source PostgreSQL server/client versions differ; mixed install refused."
            )
        run(
            ["apt-get", "download", *[f"{name}={version}" for name, version in selected.items()]],
            cwd=packages,
        )
        records = []
        for package in sorted(packages.glob("*.deb")):
            name = run(["dpkg-deb", "-f", package, "Package"])
            version = run(["dpkg-deb", "-f", package, "Version"])
            if selected.get(name) != version:
                continue
            metadata = run(["dpkg-deb", "-f", package, "Package", "Version", "Architecture"])
            run(["dpkg-deb", "-x", package, PREFIX])
            records.append({"archive": package.name, "metadata": metadata})
        private_write(TOOLS / "packages.json", json.dumps(records, indent=2) + "\n")
        found = binaries()
    if not found:
        raise RuntimeError("PostgreSQL 16 binaries are unavailable after preparation.")
    env = environment()
    for executable in ("postgres", "initdb", "pg_ctl", "psql"):
        linked = run(["ldd", found / executable], env=env)
        if "not found" in linked:
            raise RuntimeError(
                f"{executable} has missing libraries; use default Ubuntu dependencies."
            )
    print(run([found / "postgres", "--version"], env=env))
    return found


def credentials():
    path = STATE / "passwords.json"
    if path.exists():
        if DATA_ROOT is not None:
            values = _read_json(path)
        else:
            os.chmod(path, 0o600)
            values = json.loads(path.read_text())
    elif (DATA / "PG_VERSION").exists():
        raise RuntimeError(
            "Existing cluster has no managed credentials; refusing to reset or replace it."
        )
    else:
        values = {"admin": secrets.token_urlsafe(32), "application": secrets.token_urlsafe(32)}
        private_write(path, json.dumps(values) + "\n")
    for name in ("admin", "application"):
        if (
            not isinstance(values.get(name), str)
            or not values[name]
            or any(char in values[name] for char in "'\n\\:")
        ):
            raise RuntimeError("Invalid managed credential format.")
    private_write(
        STATE / "pgpass",
        f"127.0.0.1:{PORT}:*:{ADMIN}:{values['admin']}\n"
        f"127.0.0.1:{PORT}:*:{ROLE}:{values['application']}\n",
    )
    return values


def owned_pid():
    pidfile = DATA / "postmaster.pid"
    if not pidfile.exists():
        return None
    lines = pidfile.read_text().splitlines()
    try:
        pid = int(lines[0])
        proc = Path("/proc") / str(pid)
        if not proc.exists():
            return None
        command = (proc / "cmdline").read_bytes().split(b"\0")
        if proc.stat().st_uid != os.getuid() or Path(lines[1]).resolve() != DATA.resolve():
            raise RuntimeError(
                "PID metadata does not belong to the project cluster; refusing process control."
            )
        if DATA_ROOT is not None and int(lines[3]) != PORT:
            raise RuntimeError(
                "Managed PID port differs from persisted settings; no process controlled."
            )
        decoded = [item.decode() for item in command if item]
        if not decoded or Path(decoded[0]).name != "postgres" or "-D" not in decoded:
            raise RuntimeError(
                "PID is not the project PostgreSQL process; refusing process control."
            )
        if Path(decoded[decoded.index("-D") + 1]).resolve() != DATA.resolve():
            raise RuntimeError(
                "PostgreSQL PID belongs to another data directory; refusing process control."
            )
        return pid
    except (IndexError, ValueError, FileNotFoundError):
        raise RuntimeError("Cannot verify project PostgreSQL process identity.") from None


def sql(bindir, statement, *, database=DATABASE, user=ROLE, sensitive=False):
    return run(
        [
            bindir / "psql",
            "-X",
            "-w",
            "-h",
            "127.0.0.1",
            "-p",
            str(PORT),
            "-U",
            user,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
        ],
        env=environment(),
        input=statement,
        sensitive=sensitive,
    )


def start(*, existing_only=False):
    if existing_only:
        bindir = binaries()
        if not bindir or not all(
            path.is_file() for path in (DATA / "PG_VERSION", STATE / "passwords.json", CONFIG)
        ):
            raise RuntimeError(
                "Existing managed database is incomplete; run explicit preparation. "
                "No components were downloaded or database initialized."
            )
    else:
        bindir = prepare()
    values = credentials()
    if not (DATA / "PG_VERSION").exists():
        password_file = STATE / ".initdb-password"
        private_write(password_file, values["admin"] + "\n")
        try:
            run(
                [
                    bindir / "initdb",
                    "-D",
                    DATA,
                    "-U",
                    ADMIN,
                    "--auth=scram-sha-256",
                    "--pwfile",
                    password_file,
                    "--encoding=UTF8",
                    "--locale=C.UTF-8",
                    "--no-instructions",
                ],
                env=environment(),
            )
        finally:
            password_file.unlink(missing_ok=True)
        # No Unix socket avoids path-length limits in long checkout paths.
        # LLVM JIT is unnecessary for development and needs no optional LLVM runtime.
        with (DATA / "postgresql.conf").open("a") as stream:
            stream.write(
                f"\n# Project-private development cluster\nlisten_addresses = '127.0.0.1'\n"
                f"port = {PORT}\nunix_socket_directories = ''\n"
                "password_encryption = 'scram-sha-256'\n"
                "jit = off\nshared_buffers = '128MB'\nmax_connections = 50\n"
            )
    if (DATA / "PG_VERSION").read_text().strip() != "16":
        raise RuntimeError(
            "Existing data directory is not PostgreSQL 16; no upgrade will be attempted."
        )
    if not owned_pid():
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", PORT))
            except OSError:
                raise RuntimeError(
                    f"127.0.0.1:{PORT} is occupied; no existing service will be stopped."
                ) from None
        log = STATE / "postgres.log"
        log.touch(mode=0o600, exist_ok=True)
        os.chmod(log, 0o600)
        run(
            [bindir / "pg_ctl", "-D", DATA, "-l", log, "-w", "-t", "30", "start"], env=environment()
        )
    if not owned_pid():
        raise RuntimeError(
            "PostgreSQL reported started but its project process could not be verified."
        )
    if existing_only:
        sql(bindir, "SELECT current_database(), current_user;")
        print("Existing managed PostgreSQL resumed; connection metadata retained.")
        return
    # Bootstrap only the project owner and empty database; application migrations are separate.
    exists = sql(
        bindir, f"SELECT 1 FROM pg_roles WHERE rolname='{ROLE}';", database="postgres", user=ADMIN
    )
    if exists != "1":
        sql(
            bindir,
            f"CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            f"NOREPLICATION PASSWORD '{values['application']}';",
            database="postgres",
            user=ADMIN,
            sensitive=True,
        )
    exists = sql(
        bindir,
        f"SELECT 1 FROM pg_database WHERE datname='{DATABASE}';",
        database="postgres",
        user=ADMIN,
    )
    if exists != "1":
        sql(bindir, f"CREATE DATABASE {DATABASE} OWNER {ROLE};", database="postgres", user=ADMIN)
    result = sql(
        bindir, "SELECT current_database(), current_user, inet_server_addr(), inet_server_port();"
    )
    connection = {
        "database_url": (
            f"postgresql://{ROLE}:{quote(values['application'], safe='')}"
            f"@127.0.0.1:{PORT}/{DATABASE}"
        )
    }
    if DATA_ROOT is not None:
        if CONFIG.exists():
            _check_connection(CONFIG, DATA_ROOT, PORT)
        connection["managed_postgres"] = _managed_descriptor(DATA_ROOT, PORT)
    private_write(CONFIG, json.dumps(connection, indent=2) + "\n")
    print(f"PostgreSQL running (PID {owned_pid()}); SQL: {result}")
    print(f"Application connection file: {CONFIG} (0600; credentials not displayed)")


def stop():
    pid = owned_pid()
    if not pid:
        print("Project PostgreSQL stopped.")
        return
    bindir = binaries()
    if not bindir:
        raise RuntimeError(
            "Project control binary unavailable; refusing to signal processes directly."
        )
    run([bindir / "pg_ctl", "-D", DATA, "-m", "fast", "-w", "-t", "30", "stop"], env=environment())
    print(f"Project PostgreSQL stopped (previous PID {pid}).")


def status():
    pid = owned_pid()
    if not pid:
        print("Project PostgreSQL stopped.")
        return 1
    bindir = binaries()
    if not bindir:
        raise RuntimeError("PostgreSQL control/client binaries unavailable.")
    result = sql(
        bindir, "SELECT current_database(), current_user, inet_server_addr(), inet_server_port();"
    )
    print(f"PostgreSQL running (PID {pid}); SQL: {result}")
    return 0


def doctor():
    """Read-only dependency and SQL checks; never downloads, initializes or starts."""
    bindir = binaries()
    if not bindir:
        raise RuntimeError("Native PostgreSQL 16 is not prepared; use prepare or ensure.")
    print(run([bindir / "postgres", "--version"], env=environment()))
    if status():
        return 1
    for path in (STATE / "passwords.json", STATE / "pgpass", CONFIG):
        if not path.is_file() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise RuntimeError(
                f"Managed credential file is missing or has unsafe ownership/permissions: {path}"
            )
    print(
        sql(
            bindir,
            "SELECT 'owner=' || pg_get_userbyid(datdba) FROM pg_database "
            "WHERE datname=current_database();",
        )
    )
    print(
        sql(
            bindir,
            "SELECT 'application_superuser=' || rolsuper::text FROM pg_roles "
            "WHERE rolname=current_user;",
        )
    )
    print(
        sql(
            bindir,
            "SELECT 'listen=' || current_setting('listen_addresses') || "
            "'; password_encryption=' || current_setting('password_encryption');",
        )
    )
    methods = sql(
        bindir,
        "SELECT DISTINCT auth_method FROM pg_hba_file_rules "
        "WHERE error IS NULL AND type IS NOT NULL;",
        user=ADMIN,
    )
    if methods != "scram-sha-256":
        raise RuntimeError("Cluster authentication rules are not exclusively SCRAM-SHA-256.")
    print("Credential files restricted; all cluster authentication rules use SCRAM-SHA-256.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("prepare", "start", "ensure", "resume", "stop", "status", "doctor")
    )
    parser.add_argument(
        "--data-dir", type=Path, help="explicit independent private application data root"
    )
    parser.add_argument(
        "--port", type=int, help="new data-root instance only; existing port cannot change"
    )
    parser.add_argument(
        "--legacy", action="store_true", help="explicitly resume this checkout's existing cluster"
    )
    args = parser.parse_args()
    if args.legacy and (args.action != "resume" or args.data_dir is not None):
        parser.error("--legacy is only valid for resume without --data-dir")
    if args.port is not None and args.data_dir is None:
        parser.error("--port requires explicit --data-dir; legacy port is never changed")
    if args.data_dir is not None:
        configure_data_directory(
            args.data_dir,
            port=args.port,
            read_only=args.action in ("doctor", "status", "stop", "resume"),
        )
    if os.getuid() == 0:
        parser.error("Run as the project user, never root.")
    if args.action in ("doctor", "status"):
        return doctor() if args.action == "doctor" else status()
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    with (STATE / "control.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "prepare":
            prepare()
        elif args.action in ("start", "ensure"):
            start()
        elif args.action == "resume":
            if args.data_dir is None and not args.legacy:
                raise RuntimeError("Resume requires an explicitly registered managed data root.")
            start(existing_only=True)
        elif args.action == "stop":
            stop()
        else:
            return status()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, json.JSONDecodeError) as error:
        print(f"PostgreSQL development control: {error}", file=sys.stderr)
        sys.exit(1)
