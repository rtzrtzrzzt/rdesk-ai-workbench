"""Compose existing native controllers; model ownership stays in its dedicated guard."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import webbrowser
from uuid import uuid4

from launcher_config import (
    CONTROL,
    ROOT,
    control_directory,
    controller_lock,
    load_profile,
    read_control,
    write_control,
)
from launcher_progress import ModelLogProgress, Progress


def module(name, relative):
    if name in sys.modules:
        return sys.modules[name]
    path = ROOT / relative
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


class Cancelled(RuntimeError):
    pass


class Stack:
    def __init__(self, profile):
        from workbench.config import get_settings

        get_settings.cache_clear()
        self.settings = get_settings()
        self.profile = profile
        self.identifier = str(uuid4())
        self.started = set()
        self.model_process = None
        self.model_owner = None
        self.model_published = False
        self.cancelling = False
        self.progress = None
        self.log = None
        self.database = profile.get("database", "auto")
        self.search = profile.get("search", "auto")
        self.model = profile.get("model", "auto")
        if self.database == "auto":
            configuration = self.settings.database_configuration()
            self.database = (
                "managed"
                if not self.settings.database_url
                and configuration
                and configuration[0] == self.settings.data_dir / "database.json"
                and configuration[1].get("managed_postgres")
                else "external"
            )
        if self.search == "auto":
            self.search = (
                "managed" if (ROOT / ".local/search/settings.yml").is_file() else "external"
            )
        if self.model == "auto":
            self.model = (
                "managed" if (ROOT / "runtime/model/deployment.json").is_file() else "external"
            )

    def preflight(self):
        managed = (
            self.database in {"managed", "legacy"}
            or self.search == "managed"
            or self.model == "managed"
        )
        if managed and not sys.platform.startswith("linux"):
            raise RuntimeError(
                "当前平台缺少这套本地数据库、搜索或模型的受管运行配方；请先配置外部服务。应用入口共用，但不能在此平台直接运行Spark推理环境。"
            )
        if self.model == "managed":
            model = self.model_controller()
            model.engine_for(ROOT, model.CONFIG).launch_command()
            for path in (model.STATE, model.PRECOMPILE_STATE):
                phase, recorded = model.management_state(path)
                if phase == "unknown":
                    raise RuntimeError(
                        "模型存在归属或关闭状态未确认的记录，请查看模型运行说明；没有重置状态或重复加载。"
                    )
                if phase == "active" and (
                    recorded.get("boot_id") != model.boot_id()
                    or model.identity_status(
                        recorded.get("runner_pid"), recorded.get("runner_start")
                    )
                    != "match"
                ):
                    raise RuntimeError("模型监管进程未确认存活，请先核对原运行记录。")
            if model.management_state(model.PRECOMPILE_STATE)[0] == "active":
                raise RuntimeError("模型预编译仍在运行，请等待原操作结束。")
        frontend = self.settings.frontend_dir or ROOT / "apps/web/dist"
        if not (frontend / "index.html").is_file():
            raise RuntimeError(
                "已选前端尚未构建，请先运行 --install doctor 或 --rebuild-web doctor。"
            )

    def model_controller(self):
        return module("workbench_model_controller", "scripts/model/modelctl.py")

    def database_controller(self):
        result = module("workbench_postgres_controller", "scripts/dev/postgres.py")
        if self.database == "managed":
            result.configure_data_directory(self.settings.data_dir, read_only=True)
        return result

    def search_controller(self):
        return module("workbench_search_controller", "scripts/services/searxng.py")

    def cancelled(self):
        request = read_control("stop-request.json")
        return request.get("startup_id") == self.identifier

    def check_cancelled(self):
        if not self.cancelling and self.cancelled():
            raise Cancelled("已收到关闭请求，取消本次启动。")

    def command(self, command, *, timeout=90):
        self.check_cancelled()
        with self.log.open("ab", buffering=0) as output:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            started = time.monotonic()
            interrupted = False
            while process.poll() is None:
                try:
                    if not self.cancelling and self.cancelled():
                        interrupted = True
                    self.progress.update(
                        "正在取消，等待当前原生操作结束" if interrupted else "正在处理"
                    )
                    if time.monotonic() - started > timeout:
                        process.terminate()
                        process.wait(timeout=10)
                        raise RuntimeError(f"当前阶段超过等待时间，请查看日志：{self.log}")
                    time.sleep(0.2)
                except KeyboardInterrupt:
                    interrupted = True
            if interrupted:
                raise Cancelled("启动已取消。")
            if process.returncode:
                raise RuntimeError(f"当前阶段未完成，请查看日志：{self.log}")

    def database_arguments(self, action):
        args = [sys.executable, str(ROOT / "scripts/dev/postgres.py"), action]
        if self.database == "legacy" and action == "resume":
            args.append("--legacy")
        if self.database == "managed":
            args += ["--data-dir", str(self.settings.data_dir)]
        return args

    def start_database(self):
        from workbench.infrastructure.database import database_ready, engine

        if self.database == "disabled":
            raise RuntimeError("工作台需要已有PostgreSQL连接，数据库不能禁用。")
        if self.database in {"managed", "legacy"}:
            controller = self.database_controller()
            before = controller.owned_pid()
            if not before:
                self.started.add("database")
            self.command(self.database_arguments("resume"))
            engine.cache_clear()
        if not database_ready():
            raise RuntimeError("数据库尚未就绪；请检查已保存配置，未创建或切换数据库。")
        self.progress.update("连接已就绪", done=True)

    def start_search(self):
        if self.search != "managed":
            self.progress.update(
                "由已配置的外部搜索连接管理" if self.search == "external" else "未启用", done=True
            )
            return
        controller = self.search_controller()
        status = controller.status()
        if status["pid"]:
            if not status["service_responding"]:
                raise RuntimeError("已有搜索进程尚未就绪，请查看搜索日志；未重复启动。")
            self.progress.update("已在运行，复用现有服务", done=True)
            return
        self.started.add("search")
        self.command([sys.executable, str(ROOT / "scripts/services/searxng.py"), "resume"])
        status = controller.status()
        if not status["pid"] or not status["service_responding"]:
            raise RuntimeError("搜索服务未确认就绪。")
        self.progress.update("搜索服务已就绪", done=True)

    def start_model(self):
        if self.model != "managed":
            self.progress.update(
                "使用工作台中配置的模型连接" if self.model == "external" else "未托管本地模型",
                done=True,
            )
            return
        controller = self.model_controller()
        phase, state = controller.management_state(controller.STATE)
        if phase == "unknown":
            raise RuntimeError("模型启动归属未确认，未覆盖原记录。")
        if phase == "idle":
            self.progress.update("轮换旧加载缓存，保留模型权重", force=True)
            controller.cleanup_ple(1, False, True)
            with self.log.open("ab", buffering=0) as output:
                self.model_process = subprocess.Popen(
                    [
                        sys.executable,
                        str(ROOT / "scripts/model/modelctl.py"),
                        "serve",
                        "--fresh-ple",
                    ],
                    cwd=ROOT,
                    env=os.environ.copy(),
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            self.model_owner = (
                self.model_process.pid,
                controller.process_start(self.model_process.pid),
                controller.boot_id(),
            )
            self.started.add("model")
        logs = ModelLogProgress()
        began = time.monotonic()
        timeout = self.profile.get("model_timeout_seconds", 2400)
        while True:
            self.check_cancelled()
            phase, state = controller.management_state(controller.STATE)
            observed_owner = (
                state.get("runner_pid"),
                state.get("runner_start"),
                state.get("boot_id"),
            )
            if self.model_owner and observed_owner != self.model_owner:
                if self.model_process.poll() is not None:
                    raise RuntimeError(f"模型监管器未能建立本次启动记录，请查看日志：{self.log}")
            else:
                if self.model_owner:
                    self.model_published = True
                if phase == "unknown" or state.get("status") in {
                    "failed",
                    "stopped",
                    "complete",
                    "stopping",
                }:
                    raise RuntimeError(f"模型加载已停止或失败，请查看日志：{self.log}")
                controller.CONFIG.update(
                    json.loads((ROOT / "runtime/model/deployment.json").read_text())
                )
                if state.get("command") and state["command"] != controller.command():
                    raise RuntimeError("现有模型进程的实际启动参数与当前配置不一致，未重复加载。")
                if (
                    state.get("status") == "ready"
                    and phase == "active"
                    and controller.owned_alive(state)
                    and controller.health()
                ):
                    if (
                        state.get("boot_id") != controller.boot_id()
                        or controller.identity_status(
                            state.get("runner_pid"), state.get("runner_start")
                        )
                        != "match"
                        or state.get("command") != controller.command()
                        or state.get("model_revision") != controller.CONFIG["model_revision"]
                        or state.get("deployment_id") != controller.CONFIG["deployment_id"]
                    ):
                        raise RuntimeError("模型实际配置或监管归属未确认，未宣称启动成功。")
                    self.progress.update("本地模型已就绪", done=True)
                    return
                self.progress.update(logs.read(state.get("log_directory")))
            if time.monotonic() - began > timeout:
                raise RuntimeError("模型启动超过等待时间，请查看真实状态和日志；未自动重新加载。")
            time.sleep(1)

    def start_application(self):
        from workbench.entrypoints.cli import read_state

        if not read_state():
            self.started.add("application")
        self.command([sys.executable, "-m", "workbench.entrypoints.cli", "start", "--no-browser"])
        self.progress.update("API、Agent Worker、文件Worker、凭据服务已就绪", done=True)

    def stop_model(self, *, only_created=False):
        controller = self.model_controller()
        phase, state = controller.management_state(controller.STATE)
        if only_created:
            owner = (state.get("runner_pid"), state.get("runner_start"), state.get("boot_id"))
            if self.model_owner != owner:
                # A new runner may still be entering its reservation. Signal only
                # our exact child, never the older/replacement model state.
                if self.model_process and self.model_process.poll() is None:
                    controller.signal_identity(
                        self.model_owner[0], self.model_owner[1], signal.SIGTERM
                    )
                    self.model_process.wait(timeout=120)
                phase, observed = controller.management_state(controller.STATE)
                observed_owner = (
                    observed.get("runner_pid"),
                    observed.get("runner_start"),
                    observed.get("boot_id"),
                )
                if observed_owner == self.model_owner and not controller.closed_receipt(observed):
                    raise RuntimeError("本次模型启动尚无资源关闭凭证。")
                if self.model_published and observed_owner != self.model_owner:
                    raise RuntimeError("本次模型记录已被替代，关闭结果需核对。")
                return
        if phase == "unknown" and not only_created:
            recovery = controller.reconcile()
            if recovery["ready"] and recovery["records"]:
                self.progress.update("已核实系统重启，正在归档模型旧运行记录", force=True)
                controller.reconcile(apply=True)
                phase, state = controller.management_state(controller.STATE)
        if phase == "unknown":
            raise RuntimeError("模型资源归属或关闭状态未知，原记录保留。")
        if phase == "idle":
            return
        controller.request_stop(expected_owner=self.model_owner if only_created else None)
        began = time.monotonic()
        while time.monotonic() - began < 150:
            phase, state = controller.management_state(controller.STATE)
            if phase == "idle" and controller.closed_receipt(state):
                return
            self.progress.update("等待模型监管器回收资源")
            time.sleep(1)
        raise RuntimeError("模型资源回收尚未确认，请保留日志与状态记录。")

    def stop_component(self, name, *, only_created=False):
        if name == "application":
            from workbench.infrastructure.database import database_ready, engine

            # The application's final sweep checks durable command ownership in
            # PostgreSQL even after its process record is empty. Resume only the
            # verified existing cluster; the final database stage closes it again.
            if self.database in {"managed", "legacy"} and not database_ready():
                self.progress.update("临时恢复已有数据库，以确认后台任务已关闭", force=True)
                self.command(self.database_arguments("resume"))
                engine.cache_clear()
                if not database_ready():
                    raise RuntimeError("已有数据库无法连接，后台任务资源清理尚未确认。")
            self.command([sys.executable, "-m", "workbench.entrypoints.cli", "stop"], timeout=150)
        elif name == "model" and self.model == "managed":
            self.stop_model(only_created=only_created)
        elif name == "search" and self.search == "managed":
            controller = self.search_controller()
            if not controller.owned_process() and controller.responding():
                raise RuntimeError("搜索端口有未核实归属的服务，未发送停止信号。")
            self.command([sys.executable, str(ROOT / "scripts/services/searxng.py"), "stop"])
        elif name == "database" and self.database in {"managed", "legacy"}:
            self.command(self.database_arguments("stop"))

    def cancel_new(self):
        self.cancelling = True
        failures = []
        for name in ("application", "model", "search", "database"):
            if name not in self.started:
                continue
            try:
                label = {
                    "application": "工作台",
                    "model": "模型",
                    "search": "搜索",
                    "database": "数据库",
                }[name]
                self.progress.update(f"取消启动，正在清理本次新建的{label}服务", force=True)
                self.stop_component(name, only_created=True)
            except (Exception, KeyboardInterrupt):
                failures.append(name)
                break  # Keep dependent infrastructure when an executor is uncertain.
        if failures:
            raise RuntimeError("取消清理尚未全部确认，请查看status和日志。")

    def prepare_progress(self, action, total):
        control_directory()
        self.log = CONTROL / f"{action}-{self.identifier}.log"
        descriptor = os.open(self.log, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        self.progress = Progress(action, self.identifier, total)

    def start(self, *, open_browser=True):
        with controller_lock():
            self.prepare_progress("start", 5)
            print("AI工作台 · 启动全部已配置服务", flush=True)
            self.progress.begin("检查配置")
            try:
                self.preflight()
                self.progress.update("已选配置有效，沿用现有数据", done=True)
                for name, action in [
                    ("数据库", self.start_database),
                    ("搜索", self.start_search),
                    ("模型", self.start_model),
                    ("工作台", self.start_application),
                ]:
                    self.check_cancelled()
                    self.progress.begin(name)
                    action()
                self.progress.publish("ready")
                from workbench.entrypoints.cli import _url

                url = _url(self.settings)
                print(f"\n全部服务已就绪：{url}\n关闭此窗口后，服务继续在后台运行。", flush=True)
                if open_browser:
                    webbrowser.open(url)
                return 0
            except (KeyboardInterrupt, Cancelled):
                try:
                    self.cancel_new()
                except Exception as error:
                    self.progress.publish("failed", str(error))
                    print(f"取消清理尚未完成：{error}", flush=True)
                    return 1
                self.progress.publish("cancelled")
                print("本次启动已取消；此前运行的服务保留。", flush=True)
                return 130
            except Exception as error:
                self.progress.publish("failed", str(error))
                print(
                    f"启动未完成：{error}\n已启动的组件保留，可运行stop关闭，或检查后再次启动。",
                    flush=True,
                )
                return 1

    def stop(self):
        if not sys.platform.startswith("linux") and (
            self.database in {"managed", "legacy"}
            or self.search == "managed"
            or self.model == "managed"
        ):
            raise RuntimeError("当前平台没有此本地服务配方，请使用对应原生服务管理器。")
        state = read_control("state.json")
        if state.get("action") == "start" and state.get("status") == "running":
            write_control("stop-request.json", {"startup_id": state["id"]})
            print("已请求取消正在进行的启动，等待原生操作完成清理…", flush=True)
        with controller_lock(wait=True):
            self.prepare_progress("stop", 4)
            self.cancelling = True
            try:
                for label, name in [
                    ("工作台及Worker", "application"),
                    ("模型", "model"),
                    ("搜索", "search"),
                    ("数据库", "database"),
                ]:
                    self.progress.begin(label)
                    self.stop_component(name)
                    self.progress.update(
                        "已停止"
                        if name == "application" or getattr(self, name) in {"managed", "legacy"}
                        else "由外部服务管理器维护",
                        done=True,
                    )
                self.progress.publish("stopped")
                print("\n全部受管服务已关闭，对话、文件、配置和模型数据已保留。", flush=True)
                return 0
            except Exception as error:
                self.progress.publish("failed", str(error))
                print(f"关闭尚未全部确认：{error}", flush=True)
                return 1

    def status(self):
        from workbench.entrypoints.cli import read_state, workers_ready
        from workbench.infrastructure.database import database_ready
        from workbench.infrastructure.native_process import matches

        print("AI工作台 · 服务状态", flush=True)
        database_is_ready = database_ready()
        print("数据库：" + ("连接就绪" if database_is_ready else "未连接"))
        if self.search == "managed" and sys.platform.startswith("linux"):
            status = self.search_controller().status()
            print(
                "搜索：" + ("就绪" if status["pid"] and status["service_responding"] else "未就绪")
            )
        else:
            print("搜索：外部连接或未启用")
        if self.model == "managed" and sys.platform.startswith("linux"):
            controller = self.model_controller()
            phase, state = controller.management_state(controller.STATE)
            ready = (
                phase == "active"
                and state.get("status") == "ready"
                and controller.owned_alive(state)
                and controller.health()
            )
            print(
                "模型："
                + (
                    "就绪"
                    if ready
                    else "已停止"
                    if phase == "idle"
                    else "归属待核查"
                    if phase == "unknown"
                    else "正在加载或停止"
                )
            )
        else:
            print("模型：由工作台已配置连接提供")
        state = read_state()
        ready = (
            len(state) >= 4
            and all(matches(item) for item in state.values())
            and database_is_ready
            and workers_ready(state)
        )
        print(
            "工作台："
            + ("组件运行且Worker就绪" if ready else "已停止" if not state else "部分组件尚未就绪")
        )
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["start", "stop", "status", "doctor", "configure-database", "recover-account"],
    )
    parser.add_argument("--profile")
    parser.add_argument("--app-only", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "apps/server/src"))
    profile = load_profile(args.profile)
    if args.app_only or args.command not in {"start", "stop", "status"}:
        command = [sys.executable, "-m", "workbench.entrypoints.cli", args.command]
        if args.command == "start" and args.no_browser:
            command.append("--no-browser")
        return subprocess.call(command, cwd=ROOT)
    services = Stack(profile)
    if args.command == "start":
        return services.start(open_browser=not args.no_browser)
    return services.stop() if args.command == "stop" else services.status()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("操作已取消，请运行status查看实际服务状态。", flush=True)
        raise SystemExit(130) from None
    except Exception as error:
        print(f"服务控制未完成：{error}", flush=True)
        raise SystemExit(1) from None
