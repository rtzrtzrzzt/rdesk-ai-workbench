"""Command preparation only; the host owns authorization, execution and termination."""

from __future__ import annotations

import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from workbench.contracts.errors import AppError

ProviderId = Literal["native_scoped", "linux_bwrap"]
SECRET_NAMES = frozenset(
    {
        ".git",
        ".ssh",
        ".aws",
        ".azure",
        ".gnupg",
        ".config",
        ".local",
        ".runtime",
        ".tools",
        ".venv",
        ".codex",
        ".agents",
        "database.json",
        "vault.key",
        "pgpass",
    }
)
REQUIRED_FLAGS = (
    "--bind-fd",
    "--unshare-user",
    "--unshare-pid",
    "--unshare-net",
    "--unshare-ipc",
    "--unshare-uts",
    "--disable-userns",
    "--cap-drop",
    "--clearenv",
    "--new-session",
    "--die-with-parent",
    "--remount-ro",
    "--size",
)
MAX_ENTRIES, MAX_DEPTH, SCAN_SECONDS = 10000, 64, 2.0
SAFE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def denied(code: str, message: str) -> AppError:
    return AppError(code, message, 403, "policy")


@dataclass(frozen=True)
class SandboxProfile:
    """Host-created policy. Never construct protected_roots from model input."""

    workspace_root: Path
    protected_roots: tuple[Path, ...] = ()
    network: Literal["none"] = "none"


@dataclass(frozen=True)
class SandboxCapability:
    provider: ProviderId
    platform: str
    architecture: str
    status: Literal["ready", "unsupported", "unavailable"]
    version: str | None
    reason_code: str | None
    reason: str
    filesystem_isolation: bool
    network_isolation: bool
    process_isolation: bool
    environment_filtered: bool = True
    hard_cpu_limit: bool = False
    hard_memory_limit: bool = False
    hard_workspace_disk_limit: bool = False
    cancellation: str = "host_owned_runner_required"
    reconciliation: str = "host_operation_ledger_required"
    probe_performed: bool = False
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class PreparedCommand:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    pass_fds: tuple[int, ...]
    capability: SandboxCapability
    sandbox_cwd: str | None
    notes: tuple[str, ...]
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            for descriptor in self.pass_fds:
                os.close(descriptor)

    def __enter__(self) -> PreparedCommand:
        if self._closed:
            raise RuntimeError("Prepared command is closed; prepare again in the runner.")
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class SandboxProvider(Protocol):
    def capability(self) -> SandboxCapability: ...

    def prepare_command(
        self, argv: list[str] | tuple[str, ...], cwd: Path, profile: SandboxProfile
    ) -> PreparedCommand: ...


def _path(value: Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise denied("sandbox_path_invalid", "沙箱路径必须是明确的绝对路径。")
    # lstat each component, including ancestors; do not silently resolve a user link.
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except OSError:
            raise denied("sandbox_path_invalid", "沙箱路径不存在或无法读取。") from None
        if stat.S_ISLNK(info.st_mode):
            raise denied("sandbox_symlink_denied", "沙箱路径不能经过符号链接。")
    if not path.is_dir():
        raise denied("sandbox_path_invalid", "沙箱工作目录必须是已有目录。")
    return path


def _inputs(argv, cwd, profile):
    if not isinstance(profile, SandboxProfile) or profile.network != "none":
        raise denied("sandbox_profile_invalid", "此沙箱配置仅支持禁用网络。")
    if (
        not isinstance(argv, (list, tuple))
        or not 1 <= len(argv) <= 256
        or any(not isinstance(arg, str) or "\x00" in arg or len(arg) > 65536 for arg in argv)
        or not argv[0]
        or argv[0].startswith("-")
        or sum(len(arg) for arg in argv) > 131072
    ):
        raise denied("sandbox_command_invalid", "命令参数为空、过长或格式无效。")
    root, directory = _path(profile.workspace_root), _path(cwd)
    if root == root.parent or not directory.is_relative_to(root):
        raise denied("sandbox_path_escape", "命令工作目录超出已授权工作区。")
    for protected in profile.protected_roots:
        protected = Path(protected)
        if not protected.is_absolute() or ".." in protected.parts:
            raise denied("sandbox_profile_invalid", "受保护目录配置无效。")
        # Resolve host-approved protected paths so alternate spellings cannot bypass a check.
        protected = protected.resolve()
        if root.is_relative_to(protected) or protected.is_relative_to(root):
            raise denied("sandbox_protected_path", "工作区与受保护的应用数据目录重叠。")
    return root, directory, tuple(argv)


def _sensitive(name):
    return name in SECRET_NAMES or name == ".env" or name.startswith(".env.")


def _check_tree(root_fd):
    """Bounded FD-relative check, not a lock against hostile concurrent host writers."""
    started, seen = time.monotonic(), 0
    device = os.fstat(root_fd).st_dev

    def visit(descriptor, depth):
        nonlocal seen
        if depth > MAX_DEPTH:
            raise denied("sandbox_scan_limit", "工作区目录层数超出沙箱检查范围。")
        with os.scandir(descriptor) as entries:
            for entry in entries:
                seen += 1
                if seen > MAX_ENTRIES or time.monotonic() - started > SCAN_SECONDS:
                    raise denied("sandbox_scan_limit", "工作区过大，请选择较小的任务目录。")
                info = entry.stat(follow_symlinks=False)
                if _sensitive(entry.name):
                    raise denied(
                        "sandbox_sensitive_path", "工作区含受保护文件，请选择不含凭据的任务目录。"
                    )
                if stat.S_ISLNK(info.st_mode):
                    raise denied("sandbox_symlink_denied", "工作区含符号链接，不能用于此沙箱配置。")
                if info.st_dev != device:
                    raise denied(
                        "sandbox_mount_denied", "工作区含其他文件系统，不能用于此沙箱配置。"
                    )
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(
                        entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                    )
                    try:
                        actual = os.fstat(child)
                        if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                            raise denied(
                                "sandbox_workspace_changed", "工作区在检查期间变化，请重新准备。"
                            )
                        visit(child, depth + 1)
                    finally:
                        os.close(child)
                elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise denied(
                        "sandbox_special_file", "工作区含硬链接或特殊文件，不能用于此沙箱配置。"
                    )

    visit(root_fd, 0)


def _open_directory(root):
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _mounts(root):
    # st_dev alone misses bind mounts on the same filesystem. A changing host mount table
    # still requires a trusted host; this read is not a mount namespace lock.
    with Path("/proc/self/mountinfo").open("rb") as stream:
        data = stream.read(2 * 1024**2 + 1)
    if len(data) > 2 * 1024**2:
        raise denied("sandbox_mount_unknown", "无法确认工作区挂载边界。")
    for line in data.decode("utf-8", errors="strict").splitlines():
        fields = line.split()
        if len(fields) < 6:
            raise denied("sandbox_mount_unknown", "无法确认工作区挂载边界。")
        point = Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4]))
        if point != root and point.is_relative_to(root):
            raise denied("sandbox_mount_denied", "工作区含嵌套挂载，不能用于此沙箱配置。")


def _system_mounts():
    result = ["--ro-bind", "/usr", "/usr"]
    info = Path("/usr").lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise denied("sandbox_system_layout", "系统只读目录布局未受支持。")
    for name in ("bin", "sbin", "lib", "lib64"):
        source = Path("/") / name
        if not source.exists():
            continue
        if source.is_symlink():
            target = source.resolve()
            if not target.is_relative_to(Path("/usr")):
                raise denied("sandbox_system_layout", "系统库目录链接布局未受支持。")
            result.extend(("--symlink", str(target).lstrip("/"), str(source)))
        else:
            info = source.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise denied("sandbox_system_layout", "系统库目录权限未受支持。")
            result.extend(("--ro-bind", str(source), str(source)))
    cache = Path("/etc/ld.so.cache")
    if cache.exists():
        info = cache.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise denied("sandbox_system_layout", "系统动态库配置未受支持。")
        result.extend(("--ro-bind", str(cache), str(cache)))
    return result


def _bwrap_arguments(binary, descriptor, cwd, argv):
    return (
        binary,
        "--unshare-user",
        "--disable-userns",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-net",
        "--unshare-uts",
        "--cap-drop",
        "ALL",
        "--new-session",
        "--die-with-parent",
        "--clearenv",
        *_system_mounts(),
        "--proc",
        "/proc",
        "--remount-ro",
        "/proc",
        "--dev",
        "/dev",
        "--size",
        "67108864",
        "--perms",
        "0700",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/tmp/home",
        "--bind-fd",
        str(descriptor),
        "/workspace",
        "--remount-ro",
        "/",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "LC_ALL",
        "C.UTF-8",
        "--setenv",
        "HOME",
        "/tmp/home",
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--chdir",
        cwd,
        "--",
        *argv,
    )


class NativeScopedProvider:
    def capability(self):
        return SandboxCapability(
            "native_scoped",
            sys.platform,
            platform.machine(),
            "ready",
            None,
            None,
            "原生受管执行；不提供文件、网络或进程的操作系统隔离。",
            False,
            False,
            False,
        )

    def prepare_command(self, argv, cwd, profile):
        root, directory, arguments = _inputs(argv, cwd, profile)
        environment = dict(SAFE_ENV)
        if os.name == "nt":
            system = os.environ.get("SystemRoot", r"C:\Windows")
            environment = {"SystemRoot": system, "PATH": str(Path(system) / "System32")}
        environment.update(HOME=str(root), TMPDIR=str(root), TEMP=str(root), TMP=str(root))
        return PreparedCommand(
            arguments,
            directory,
            environment,
            (),
            self.capability(),
            None,
            ("cwd和环境约束不阻止命令访问其他宿主路径或网络。",),
        )


class LinuxBubblewrapProvider:
    def __init__(self):
        self._cached = None
        self._binary = None
        self._lock = threading.Lock()

    def _cap(self, status, version, code, reason, *, probed=False):
        return SandboxCapability(
            "linux_bwrap",
            sys.platform,
            platform.machine(),
            status,
            version,
            code,
            reason,
            status == "ready",
            status == "ready",
            status == "ready",
            probe_performed=probed,
        )

    def capability(self):
        # One bounded real probe per provider instance; no kernel setting is changed.
        with self._lock:
            if self._cached is None:
                self._cached = self._probe()
            return self._cached

    def _probe(self):
        if sys.platform != "linux":
            return self._cap(
                "unsupported", None, "platform_unsupported", "此平台尚未实现 bubblewrap 隔离。"
            )
        binary = shutil.which("bwrap", path="/usr/bin:/bin")
        if not binary:
            return self._cap("unavailable", None, "component_missing", "未安装 bubblewrap。")
        version = None
        try:
            info = Path(binary).stat()
            if info.st_uid != 0 or info.st_mode & (0o022 | stat.S_ISUID | stat.S_ISGID):
                return self._cap(
                    "unsupported", None, "binary_permissions", "bubblewrap 安装权限不受支持。"
                )
            version_result = subprocess.run(
                [binary, "--version"], env=SAFE_ENV, capture_output=True, timeout=2, check=False
            )
            match = re.fullmatch(rb"bubblewrap ([0-9]+\.[0-9]+\.[0-9]+)\s*", version_result.stdout)
            version = match[1].decode() if match else None
            help_result = subprocess.run(
                [binary, "--help"], env=SAFE_ENV, capture_output=True, timeout=2, check=False
            )
            if not version or any(
                flag.encode() not in help_result.stdout for flag in REQUIRED_FLAGS
            ):
                return self._cap(
                    "unsupported", version, "flags_missing", "bubblewrap 缺少所需的安全参数。"
                )
            with tempfile.TemporaryDirectory(prefix="workbench-sandbox-probe-") as temporary:
                descriptor = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    result = subprocess.run(
                        _bwrap_arguments(binary, descriptor, "/workspace", ("/bin/true",)),
                        env=SAFE_ENV,
                        cwd="/",
                        pass_fds=(descriptor,),
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        timeout=3,
                        check=False,
                        start_new_session=True,
                    )
                finally:
                    os.close(descriptor)
            if result.returncode:
                diagnostic = result.stderr.lower()
                code = (
                    "namespace_denied"
                    if any(
                        term in diagnostic
                        for term in (
                            b"namespace",
                            b"uid map",
                            b"uidmap",
                            b"operation not permitted",
                        )
                    )
                    else "probe_failed"
                )
                reason = (
                    "本机内核或安全策略拒绝所需的命名空间，不能使用此沙箱。"
                    if code == "namespace_denied"
                    else "bubblewrap 隔离试运行失败，不能使用此沙箱。"
                )
                return self._cap("unsupported", version, code, reason, probed=True)
            self._binary = binary
            return self._cap(
                "ready",
                version,
                None,
                "隔离试运行通过；仅工作区映射可写，网络已隔离。",
                probed=True,
            )
        except subprocess.TimeoutExpired:
            return self._cap(
                "unsupported", version, "probe_timeout", "沙箱试运行超时，未启用。", probed=True
            )
        except (OSError, ValueError, AppError):
            return self._cap(
                "unsupported", version, "probe_unavailable", "无法完成沙箱安全检查，未启用。"
            )

    def prepare_command(self, argv, cwd, profile):
        root, directory, arguments = _inputs(argv, cwd, profile)
        capability = self.capability()
        if capability.status != "ready":
            raise AppError(
                "SANDBOX_UNAVAILABLE",
                capability.reason,
                409,
                "policy",
                details={"provider": "linux_bwrap", "reason_code": capability.reason_code},
            )
        descriptor = None
        try:
            info = root.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o022 or root == Path.home():
                raise denied(
                    "sandbox_workspace_permissions",
                    "请选择当前用户拥有且不可被其他用户写入的任务目录。",
                )
            system_paths = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc/ld.so.cache")
            if any(
                Path(protected).resolve().is_relative_to(Path(system).resolve())
                or Path(system).resolve().is_relative_to(Path(protected).resolve())
                for protected in profile.protected_roots
                for system in system_paths
                if Path(system).exists()
            ):
                raise denied(
                    "sandbox_protected_path", "受保护数据位于系统只读映射范围，此配置不可用。"
                )
            _mounts(root)
            descriptor = _open_directory(root)
            actual = os.fstat(descriptor)
            if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                raise denied("sandbox_workspace_changed", "工作区在准备期间变化，请重新准备。")
            _check_tree(descriptor)
            inner_cwd = str(Path("/workspace") / directory.relative_to(root))
            # Map only the executable path. Arguments and shell source remain unchanged.
            if Path(arguments[0]).is_absolute() and Path(arguments[0]).is_relative_to(root):
                arguments = (
                    str(Path("/workspace") / Path(arguments[0]).relative_to(root)),
                    *arguments[1:],
                )
            command = _bwrap_arguments(self._binary, descriptor, inner_cwd, arguments)
            return PreparedCommand(
                command,
                Path("/"),
                dict(SAFE_ENV),
                (descriptor,),
                capability,
                inner_cwd,
                (
                    "工作区映射为/workspace；宿主绝对路径参数不会自动改写。",
                    "无cgroup CPU/内存/工作区磁盘配额；超时和后代清理由宿主执行器负责。",
                ),
            )
        except AppError:
            if descriptor is not None:
                os.close(descriptor)
            raise
        except (OSError, UnicodeError):
            if descriptor is not None:
                os.close(descriptor)
            raise denied(
                "sandbox_workspace_changed", "工作区无法安全检查，请重新选择或准备。"
            ) from None


def provider_for(provider: ProviderId) -> SandboxProvider:
    if provider == "native_scoped":
        return NativeScopedProvider()
    if provider == "linux_bwrap":
        return LinuxBubblewrapProvider()
    raise denied("sandbox_provider_invalid", "未知的执行隔离提供者；未执行命令。")
