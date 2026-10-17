#!/usr/bin/env python3
"""Native source bootstrap; daily startup never installs or upgrades packages."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
PYPI = "https://pypi.org/simple"
NPM = "https://registry.npmjs.org"
UV_VERSION = "0.9.25"


class BootstrapError(RuntimeError):
    pass


def runtime_python():
    return ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def probe(command, *, cwd=ROOT, timeout=10, env=None):
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        return (
            completed.stdout.decode("utf-8", "replace").strip()
            if completed.returncode == 0
            else None
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def version(value):
    match = re.search(r"(?:^|\s|v)(\d+)\.(\d+)\.(\d+)", value or "")
    return tuple(map(int, match.groups())) if match else None


def python_version(path):
    output = probe(
        [str(path), "-I", "-c", "import sys; print('.'.join(map(str,sys.version_info[:3])))"]
    )
    return version(output)


def toolchain(explicit_python=None, *, need_web=True):
    candidates = (
        [explicit_python]
        if explicit_python
        else [runtime_python(), shutil.which("python3.12"), sys.executable]
    )
    python = next(
        (
            str(item)
            for item in candidates
            if item and (python_version(item) or (0,))[:2] == (3, 12)
        ),
        None,
    )
    local_uv = ROOT / ".tools" / "bootstrap" / ("Scripts/uv.exe" if os.name == "nt" else "bin/uv")
    uv = str(local_uv) if local_uv.is_file() else shutil.which("uv")
    uv_ver = version(probe([uv, "--version"])) if uv else None
    node = shutil.which("node") if need_web else None
    node_ver = version(probe([node, "--version"])) if node else None
    npm = shutil.which("npm") if need_web else None
    npm_command = None
    if npm and node:
        if os.name == "nt":
            for base in (Path(npm).parent, Path(node).parent):
                cli = base / "node_modules" / "npm" / "bin" / "npm-cli.js"
                if cli.is_file():
                    npm_command = [node, str(cli)]
                    break
        else:
            npm_command = [npm]
    npm_ver = (
        version(probe(npm_command + ["--version", "--logs-max=0", "--update-notifier=false"]))
        if npm_command
        else None
    )
    return {
        "python": python,
        "python_version": python_version(python) if python else None,
        "uv": uv,
        "uv_version": uv_ver,
        "node": node,
        "node_version": node_ver,
        "npm": npm_command,
        "npm_version": npm_ver,
    }


def ensure_locks():
    for path in (
        ROOT / "uv.lock",
        ROOT / "pyproject.toml",
        WEB / "package-lock.json",
        WEB / "package.json",
    ):
        if not path.is_file():
            raise BootstrapError(
                "Source delivery is incomplete: a required project lock/manifest is missing."
            )


def instructions(tool):
    if tool == "python":
        print("需要 Python 3.12；从 https://www.python.org/downloads/ 安装本机架构的官方版本。")
        if os.name == "nt":
            print("可手动执行：winget install --id Python.Python.3.12 --exact --scope user")
        elif sys.platform.startswith("linux"):
            print(
                "Ubuntu 24.04 原生包：python3.12、python3.12-venv"
                "（由用户/管理员明确安装，本脚本不运行sudo）。"
            )
    elif tool == "uv":
        print("需要 uv >=0.9.25；可手动建立项目私有工具环境（不修改全局Python）：")
        print(
            "  python3.12 -m venv .tools/bootstrap"
            if os.name != "nt"
            else "  py -3.12 -m venv .tools\\bootstrap"
        )
        binary = (
            ".tools\\bootstrap\\Scripts\\python.exe"
            if os.name == "nt"
            else ".tools/bootstrap/bin/python"
        )
        print(f"  {binary} -m pip install --index-url {PYPI} uv=={UV_VERSION}")
        print("官方来源：https://docs.astral.sh/uv/getting-started/installation/")
    else:
        print(
            "前端构建需要 Node.js >=22.12.0 及随附npm>=9.5；推荐使用官方LTS安装包：https://nodejs.org/en/download"
        )
        print(
            "只把已授权安装的Node bin目录加入本次进程PATH；"
            "不会搜索旧项目、下载工具链或修改全局PATH。"
        )
        print("npm ci --engine-strict 会另外校验当前平台锁定依赖的兼容范围。")


def sanitized(value):
    lines = []
    for line in value.splitlines():
        # Diagnostics never echo environment, connection DSNs or credential-like
        # lines. This is bounded operational output, not a generic data scrubber.
        if re.search(
            r"password|passwd|api[_-]?key|authorization|secret|WORKBENCH_[A-Z_]+\s*=", line, re.I
        ):
            lines.append("[credential-related diagnostic omitted]")
            continue
        line = re.sub(
            r"postgres(?:ql)?(?:\+\w+)?://\S+", "[database connection omitted]", line, flags=re.I
        )
        line = re.sub(r"https?://[^/\s@]+@\S+", "[credential URL omitted]", line, flags=re.I)
        lines.append(line)
    return "\n".join(lines)


def installer_environment():
    environment = dict(os.environ)
    for key in list(environment):
        upper = key.upper()
        if upper.startswith(
            ("WORKBENCH_", "PG", "PIP_", "UV_INDEX", "UV_EXTRA", "NPM_CONFIG_")
        ) or upper in {
            "UV_PROJECT_ENVIRONMENT",
            "UV_PROJECT",
            "UV_PYTHON",
            "UV_FIND_LINKS",
            "VIRTUAL_ENV",
            "PYTHONHOME",
            "PYTHONPATH",
            "NPM_TOKEN",
            "NODE_AUTH_TOKEN",
        }:
            environment.pop(key, None)
    environment.update(
        {
            "UV_DEFAULT_INDEX": PYPI,
            "UV_PYTHON_DOWNLOADS": "never",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "NPM_CONFIG_REGISTRY": NPM,
            "NPM_CONFIG_AUDIT": "false",
            "NPM_CONFIG_FUND": "false",
        }
    )
    return environment


def run_step(name, command, *, cwd=ROOT, env=None, timeout=900):
    print(f"[RUN] {name}", flush=True)
    # Package managers may print user-level proxy/registry diagnostics. Keep raw
    # streams in an unnamed temporary file and never persist/echo their contents.
    with tempfile.TemporaryFile() as output:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise BootstrapError(f"{name} 超时；未启动应用。请在本机单独运行该工具检查。") from None
        except OSError:
            raise BootstrapError(f"{name} 无法执行；请核对本机工具链。") from None
    if completed.returncode:
        raise BootstrapError(
            f"{name} 失败（exit={completed.returncode}）；锁文件不应被改写。"
            "请在本机按 installation/README 中的精确命令诊断；原始工具输出未回显。"
        )
    print(f"[OK] {name}")


def database_state(python):
    if not python.is_file() or (python_version(python) or (0,))[:2] != (3, 12):
        return "runtime_missing"
    code = (
        "try:\n from workbench.infrastructure.database import database_ready\n"
        " print('ready' if database_ready() else 'not_ready')\n"
        "except Exception:\n print('runtime_unavailable')"
    )
    result = probe([str(python), "-I", "-B", "-c", code], timeout=8)
    return (
        result if result in {"ready", "not_ready", "runtime_unavailable"} else "runtime_unavailable"
    )


def postgres_instructions():
    print("PostgreSQL 尚未就绪；没有停止、清空或修改任何其他数据库。")
    if sys.platform.startswith("linux"):
        print("Ubuntu 24.04 可显式准备项目私有原生PG16：")
        print("  ./start.sh --prepare-database doctor")
        print("仅该助手支持的平台可用；其他Linux使用已授权的原生PostgreSQL实例。")
    elif os.name == "nt":
        print(
            "Windows原生安装：https://www.postgresql.org/download/windows/ "
            "（官方链接的EDB安装器）；使用独立应用数据库和最小权限角色。"
        )
    else:
        print(
            "macOS原生安装：https://www.postgresql.org/download/macosx/ ；"
            "使用独立应用数据库和最小权限角色。"
        )
    print(
        "已有实例请通过主Launcher受保护配置设置连接；本脚本不要求将DSN/口令粘贴到终端。首次设置/数据库安装向导与签名发行包仍由主Launcher交付。"
    )


def check(tools, *, rebuild_web=False):
    failed = False
    rules = [
        ("Python3.12", tools["python_version"], lambda v: v[:2] == (3, 12), "python"),
        ("uv>=0.9.25", tools["uv_version"], lambda v: v >= (0, 9, 25), "uv"),
    ]
    if rebuild_web or not (WEB / "dist/index.html").is_file():
        rules.extend(
            [
                ("Node>=22.12", tools["node_version"], lambda v: v >= (22, 12, 0), "node"),
                ("npm>=9.5", tools["npm_version"], lambda v: v >= (9, 5, 0), "node"),
            ]
        )
    else:
        print("[INFO] Node/npm: not required; using included frontend bundle")
    for label, value, valid, hint in rules:
        good = bool(value and valid(value))
        print(
            f"[{'OK' if good else 'MISSING'}] {label}"
            + (": " + ".".join(map(str, value)) if value else "")
        )
        if not good:
            failed = True
            instructions(hint)
    current = runtime_python()
    state = database_state(current)
    print(f"[INFO] project runtime: {'present' if current.is_file() else 'not installed'}")
    bundle = "present" if (WEB / "dist" / "index.html").is_file() else "not built"
    print(f"[INFO] frontend static bundle: {bundle}")
    print(f"[INFO] PostgreSQL: {state}")
    if state != "ready":
        postgres_instructions()
    return 1 if failed or state != "ready" or not (WEB / "dist" / "index.html").is_file() else 0


def install(tools, *, rebuild_web=False):
    for key, predicate, hint in [
        ("python_version", lambda v: v[:2] == (3, 12), "python"),
        ("uv_version", lambda v: v >= (0, 9, 25), "uv"),
    ]:
        value = tools[key]
        if not value or not predicate(value):
            instructions(hint)
            raise BootstrapError("工具链未满足要求；没有执行安装。")
    if rebuild_web or not (WEB / "dist/index.html").is_file():
        require_web_tools(tools)
    environment = installer_environment()
    environment["UV_PROJECT_ENVIRONMENT"] = str(ROOT / ".venv")
    run_step(
        "锁定Python应用环境",
        [
            tools["uv"],
            "sync",
            "--locked",
            "--python",
            tools["python"],
            "--no-python-downloads",
            "--default-index",
            PYPI,
        ],
        env=environment,
    )
    if rebuild_web or not (WEB / "dist/index.html").is_file():
        build_web(tools)
    else:
        print("[OK] 使用随包前端产物；没有运行 Node/npm 或改写 dist")


def require_web_tools(tools):
    for key, predicate in (
        ("node_version", lambda v: v >= (22, 12, 0)),
        ("npm_version", lambda v: v >= (9, 5, 0)),
    ):
        if not tools[key] or not predicate(tools[key]):
            instructions("node")
            raise BootstrapError("显式前端构建缺少工具链；未运行 npm。")


def build_web(tools):
    require_web_tools(tools)
    environment = installer_environment()
    run_step(
        "锁定前端依赖",
        tools["npm"] + ["ci", "--registry", NPM, "--engine-strict", "--no-audit", "--no-fund"],
        cwd=WEB,
        env=environment,
    )
    run_step("前端静态构建", tools["npm"] + ["run", "build"], cwd=WEB, env=environment)


def prepare_database():
    release = Path("/etc/os-release")
    supported = (
        sys.platform.startswith("linux")
        and platform.machine().lower() in {"aarch64", "arm64"}
        and release.is_file()
        and "ID=ubuntu\n" in release.read_text(encoding="utf-8")
        and 'VERSION_ID="24.04"' in release.read_text(encoding="utf-8")
    )
    if not supported:
        postgres_instructions()
        raise BootstrapError("当前平台未验收私有 PG 自动准备；请配置官方原生 PostgreSQL。")
    python = runtime_python()
    if not python.is_file():
        raise BootstrapError("准备数据库前请先显式 --install 安装项目运行环境。")
    if os.environ.get("WORKBENCH_DATABASE_URL"):
        raise BootstrapError(
            "已显式配置外部数据库连接，未切换或覆盖它；请由主 Launcher 核对原生连接配置。"
        )
    selected = probe(
        [
            str(python),
            "-I",
            "-B",
            "-c",
            "from workbench.config import get_settings; print(get_settings().data_dir.absolute())",
        ]
    )
    if not selected or "\n" in selected or not Path(selected).is_absolute():
        raise BootstrapError("未能确定独立应用数据目录；没有准备数据库。")
    run_step(
        "显式准备独立应用数据目录的私有 PostgreSQL16",
        [str(python), str(ROOT / "scripts/dev/postgres.py"), "ensure", "--data-dir", selected],
        timeout=900,
    )


def run_backend(command, no_browser):
    python = runtime_python()
    if not python.is_file() or (command == "start" and not (WEB / "dist" / "index.html").is_file()):
        raise BootstrapError(
            "首次源码安装尚未完成；请运行 start.sh --install doctor"
            "（Windows: .\\start.ps1 --install doctor）。日常启动不会隐式重装。"
        )
    if (python_version(python) or (0,))[:2] != (3, 12):
        raise BootstrapError("项目venv不是Python3.12；请显式重新运行锁定安装。")
    # The Launcher is the authority for database startup: it may resume an
    # existing verified private cluster, and rejects absent/external instances.
    arguments = [str(python), "-m", "workbench.entrypoints.cli", command]
    if command == "start" and no_browser:
        arguments.append("--no-browser")
    if command in {"configure-database", "recover-account"}:
        # Password prompts must keep the caller's terminal. Capturing stdout would
        # hide the prompts and a fixed launcher timeout could interrupt publication.
        try:
            return subprocess.run(arguments, cwd=ROOT, check=False).returncode
        except KeyboardInterrupt:
            return 130
        except OSError:
            raise BootstrapError("项目Python环境无法执行；请核对权限和锁定安装。") from None
    try:
        result = subprocess.run(
            arguments,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise BootstrapError("Launcher尚未完成；请用status核对原实例，不能自动重放启动。") from None
    except OSError:
        raise BootstrapError("项目Python环境无法执行；请核对权限并使用显式锁定安装修复。") from None
    text = result.stdout.decode("utf-8", "replace")
    if len(text) > 65536:
        text = text[-65536:]
    print(sanitized(text), end="\n" if not text.endswith("\n") else "")
    return result.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        choices=("start", "stop", "status", "doctor", "configure-database", "recover-account"),
        default="start",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="read-only source/tool/runtime/database checks; never install/start/migrate",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="explicit locked uv sync; reuse included frontend unless missing/--rebuild-web",
    )
    parser.add_argument(
        "--python", help="explicit installed Python3.12 interpreter; no Python downloads"
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--rebuild-web",
        action="store_true",
        help="explicit locked npm ci/build; --check only checks its prerequisites",
    )
    parser.add_argument(
        "--prepare-database",
        action="store_true",
        help="explicit project-private native PG16 ensure on verified Ubuntu24.04 ARM64",
    )
    args = parser.parse_args(argv)
    if args.check and (args.install or args.prepare_database):
        parser.error("--check cannot install or prepare a database")
    ensure_locks()
    if args.check or args.install or args.rebuild_web:
        tools = toolchain(
            args.python, need_web=args.rebuild_web or not (WEB / "dist/index.html").is_file()
        )
        if args.check:
            return check(tools, rebuild_web=args.rebuild_web)
        if args.install:
            install(tools, rebuild_web=args.rebuild_web)
        elif args.rebuild_web:
            build_web(tools)
    if args.prepare_database:
        prepare_database()
    return run_backend(args.command, args.no_browser)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BootstrapError as exc:
        print(f"Native bootstrap: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
