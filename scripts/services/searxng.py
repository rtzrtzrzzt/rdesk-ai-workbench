#!/usr/bin/env python3
"""Project-private native SearXNG developer service; never installs system packages."""

import argparse
import fcntl
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / ".runtime/search-env"
SRC = ENV / "src"
VENV = ENV / "venv"
PYTHON = VENV / "bin/python"
STATE = ROOT / ".local/search"
CONFIG = STATE / "settings.yml"
PID = STATE / "service.json"
REVISION = "2a6fc07be3760543c7b6d1d1bfe62f5765d5d9d6"
URL = "http://127.0.0.1:8888"


def run(args, **kwargs):
    task_env = os.environ.copy()
    task_env["UV_CACHE_DIR"] = str(ENV / "cache")
    subprocess.run([str(arg) for arg in args], check=True, env=task_env, **kwargs)


def private_json(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def prepare():
    ENV.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE, 0o700)
    for folder in ("tmp", "cache", "config", "data"):
        (STATE / folder).mkdir(exist_ok=True, mode=0o700)
    if not (SRC / ".git").exists():
        run(["git", "clone", "--depth", "1", "https://github.com/searxng/searxng.git", SRC])
    head = subprocess.check_output(["git", "-C", str(SRC), "rev-parse", "HEAD"], text=True).strip()
    if head != REVISION:
        if subprocess.check_output(
            ["git", "-C", str(SRC), "status", "--porcelain"], text=True
        ).strip():
            raise RuntimeError(
                "Upstream checkout has local modifications; refusing to replace them."
            )
        run(["git", "-C", SRC, "fetch", "--depth", "1", "origin", REVISION])
        run(["git", "-C", SRC, "checkout", "--detach", REVISION])
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(
            "uv is required for independent binary-wheel installation; no system install attempted."
        )
    if not PYTHON.exists():
        run([uv, "venv", "--python", "3.12", VENV])
    stamp = ENV / "prepared-revision.txt"
    if not stamp.exists() or stamp.read_text().strip() != REVISION:
        run(
            [
                uv,
                "pip",
                "install",
                "--python",
                PYTHON,
                "--index-url",
                "https://pypi.org/simple",
                "--only-binary",
                ":all:",
                "-r",
                SRC / "requirements.txt",
                "setuptools",
                "wheel",
                "pybind11",
            ]
        )
        run(
            [
                uv,
                "pip",
                "install",
                "--python",
                PYTHON,
                "--index-url",
                "https://pypi.org/simple",
                "--no-build-isolation",
                "--no-deps",
                "-e",
                SRC,
            ]
        )
        frozen = subprocess.check_output([uv, "pip", "freeze", "--python", str(PYTHON)], text=True)
        (ENV / "installed-requirements.txt").write_text(frozen)
        stamp.write_text(REVISION + "\n")
    if not CONFIG.exists():
        private_json(
            CONFIG,
            {
                "use_default_settings": True,
                "general": {"debug": False, "instance_name": "AI Workbench local search"},
                "server": {
                    "bind_address": "127.0.0.1",
                    "port": 8888,
                    "secret_key": secrets.token_urlsafe(48),
                    "limiter": False,
                    "public_instance": False,
                    "image_proxy": False,
                },
                "search": {"formats": ["html", "json"]},
                "valkey": {"url": False},
                "outgoing": {"request_timeout": 8, "max_request_timeout": 15},
            },
        )
    os.chmod(CONFIG, 0o600)


def identity(pid):
    try:
        path = Path(f"/proc/{pid}")
        cmdline = (path / "cmdline").read_bytes().split(b"\0")
        if cmdline[:3] != [os.fsencode(PYTHON), b"-m", b"searx.webapp"]:
            return None
        # /proc stat's comm may contain spaces/parentheses; parse from its final ')'.
        return (path / "stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def owned_process():
    try:
        data = json.loads(PID.read_text())
        return data["pid"] if identity(data["pid"]) == data["start_ticks"] else None
    except (OSError, ValueError, KeyError):
        return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def responding():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(URL + "/config", timeout=2) as response:
            body = response.read(2 * 1024 * 1024)
            return isinstance(json.loads(body).get("engines"), list)
    except (OSError, ValueError, urllib.error.URLError):
        return False


def status():
    pid = owned_process()
    return {
        "installed": (ENV / "prepared-revision.txt").exists(),
        "source_revision": REVISION,
        "endpoint": URL,
        "pid": pid,
        "status": "running" if pid else "stopped",
        "service_responding": responding() if pid else False,
        "search_verified": False,
    }


def start():
    prepare()
    if owned_process():
        return status()
    # Never take over or terminate an unrelated listener.
    import socket

    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 8888))
        except OSError:
            raise RuntimeError(
                "127.0.0.1:8888 is occupied; no unrelated process was changed."
            ) from None
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SEARXNG_")
        and key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    }
    env.update(
        {
            "SEARXNG_SETTINGS_PATH": str(CONFIG),
            "TMPDIR": str(STATE / "tmp"),
            "XDG_CACHE_HOME": str(STATE / "cache"),
            "XDG_CONFIG_HOME": str(STATE / "config"),
            "XDG_DATA_HOME": str(STATE / "data"),
            "PYTHONUNBUFFERED": "1",
        }
    )
    log_path = STATE / "service.log"
    with open(log_path, "ab") as log:
        os.chmod(log_path, 0o600)
        process = subprocess.Popen(
            [str(PYTHON), "-m", "searx.webapp"],
            cwd=SRC,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    ticks = None
    for _ in range(20):
        ticks = identity(process.pid)
        if ticks or process.poll() is not None:
            break
        time.sleep(0.05)
    if not ticks:
        if process.poll() is None:
            process.terminate()
        raise RuntimeError(
            "Could not establish private process identity; inspect .local/search/service.log."
        )
    private_json(PID, {"pid": process.pid, "start_ticks": ticks})
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "Private SearXNG exited; inspect .local/search/service.log. "
                "This is not a search acceptance result."
            )
        if responding():
            return status()
        time.sleep(0.3)
    raise RuntimeError(
        "Private process started but did not respond within 45 seconds; inspect status/log."
    )


def stop():
    pid = owned_process()
    if pid:
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 10
        while identity(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if identity(pid):
            raise RuntimeError("Private process has not stopped; no SIGKILL was sent.")
    PID.unlink(missing_ok=True)
    return status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["prepare", "start", "ensure", "stop", "status", "doctor"]
    )
    command = parser.parse_args().command
    if command in {"status", "doctor"}:
        print(json.dumps(status()))
        return
    ENV.mkdir(parents=True, exist_ok=True)
    with open(ENV / "service.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if command == "prepare":
            prepare()
            result = status()
        elif command == "stop":
            result = stop()
        else:
            result = start()
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
