"""Actual Linux/process-visible readings; unavailable values stay null. No new dependency."""

import csv
import io
import os
import platform
import shutil
import subprocess
from pathlib import Path

from workbench.config import get_settings
from workbench.infrastructure.database import now
from workbench.modules.analytics import dto


def _memory():
    values = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            parts = value.split()
            if parts and parts[0].isdigit() and parts[1:] == ["kB"]:
                values[key] = int(parts[0]) * 1024
    except (OSError, ValueError):
        pass
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    return dto.MemoryHardware(
        status="available" if total is not None and available is not None else "unavailable",
        source="Linux /proc/meminfo",
        total_bytes=total,
        available_bytes=available,
        used_bytes=total - available
        if total is not None and available is not None and total >= available
        else None,
        swap_total_bytes=values.get("SwapTotal"),
        swap_free_bytes=values.get("SwapFree"),
        description="系统可见内存；已用=MemTotal−MemAvailable，包含内核回收语义；不叠加 GPU 内存。",
    )


def _cpu():
    try:
        load = os.getloadavg()
    except (OSError, AttributeError):
        load = (None, None, None)
    try:
        accessible = len(os.sched_getaffinity(0))
    except (OSError, AttributeError):
        accessible = None
    logical = os.cpu_count()
    return dto.CPUHardware(
        status="available" if logical is not None else "unavailable",
        source="os.cpu_count / sched_getaffinity / getloadavg",
        logical_cpus=logical,
        accessible_cpus=accessible,
        load_1m=load[0],
        load_5m=load[1],
        load_15m=load[2],
        utilization_percent=None,
        description="load 为 1/5/15 分钟任务均值，不是 CPU 使用率；未采样时间差。",
    )


def _number(value):
    try:
        number = float(value.strip())
        if not 0 <= number < 1e15:
            return None
        return number
    except ValueError:
        return None


def _gpu():
    command = shutil.which("nvidia-smi")
    devices = []
    if command:
        try:
            result = subprocess.run(
                [
                    command,
                    "--query-gpu=index,name,utilization.gpu,memory.total,memory.used,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            if len(result.stdout) > 64 * 1024:
                raise ValueError("GPU output limit")
            for row in csv.reader(io.StringIO(result.stdout)):
                if len(row) != 6 or len(devices) >= 64:
                    raise ValueError("GPU protocol")
                index, name, utilization, total, used, temperature = (
                    value.strip() for value in row
                )
                unified = "GB10" in name.upper()
                total, used = _number(total), _number(used)
                utilization = _number(utilization)
                devices.append(
                    dto.GPUDevice(
                        index=int(index),
                        name=name[:200],
                        utilization_percent=utilization
                        if utilization is not None and utilization <= 100
                        else None,
                        memory_total_bytes=int(total * 1024**2) if total is not None else None,
                        memory_used_bytes=int(used * 1024**2) if used is not None else None,
                        temperature_celsius=_number(temperature),
                        memory_model="unified" if unified else "unknown",
                        system_memory_overlap=True if unified else None,
                    )
                )
        except (OSError, ValueError, subprocess.SubprocessError):
            devices = []
    return dto.GPUHardware(
        status="available" if devices else "unavailable",
        source="local nvidia-smi / NVML (2 second timeout)",
        devices=devices,
        description="GB10 为统一内存，与 RAM 重叠；不可用计数器为 null，不相加内存。"
        if devices
        else "nvidia-smi 未安装、失败、超时或未返回设备；GPU 读数不可用。",
    )


def _storage():
    path = get_settings().data_dir.absolute()
    try:
        while not path.exists() and path != path.parent:
            path = path.parent
        disk = shutil.disk_usage(path)
        status, total, used, free = "available", disk.total, disk.used, disk.free
    except OSError:
        status, total, used, free = "unavailable", None, None, None
    return dto.StorageHardware(
        status=status,
        source="shutil.disk_usage / statvfs",
        path=str(path),
        total_bytes=total,
        used_bytes=used,
        free_bytes=free,
        description="数据目录所在文件系统的容量，不是本项目文件占用；目录不存在时读取最近存在的祖先文件系统。",
    )


def snapshot() -> dto.HardwareSnapshot:
    linux = platform.system() == "Linux"
    return dto.HardwareSnapshot(
        sampled_at=now(),
        platform=platform.system(),
        architecture=platform.machine(),
        scope="visible_to_server",
        memory=_memory(),
        cpu=_cpu(),
        gpu=_gpu(),
        storage=_storage(),
        notes=[
            "仅报告服务进程可见的系统/设备读数，不代表容器或其他用户的专属配额。",
            "系统内存与 GPU 统一内存可能重叠；没有相加的总占用或虚构温度/速度。",
        ]
        + ([] if linux else ["当前平台不是 Linux；/proc 指标可能不可用。"]),
    )
