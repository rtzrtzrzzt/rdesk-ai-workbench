"""Focused regression checks; native GPU/UI acceptance is recorded separately."""

import asyncio
import importlib.util
import json
import sys
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace

import pytest
from workbench.contracts.errors import AppError
from workbench.modules.media import resources


@pytest.fixture
def flux_phase():
    script = Path(__file__).resolve().parents[1] / "scripts/media/flux_phase.py"
    spec = importlib.util.spec_from_file_location("flux_phase", script)
    phase = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(phase)
    return phase


def test_driver_device_name_stops_at_first_null_terminator(monkeypatch, flux_phase):
    driver = SimpleNamespace(
        CUresult=SimpleNamespace(CUDA_SUCCESS=0),
        CUdevice_attribute=SimpleNamespace(
            CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MAJOR="major",
            CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MINOR="minor",
        ),
        cuInit=lambda _: (0,),
        cuDeviceGet=lambda _: (0, 0),
        cuDeviceGetAttribute=lambda attribute, _: (0, 12 if attribute == "major" else 1),
        cuDeviceGetName=lambda *_: (0, b"NVIDIA GB10\0\0" + b" " * 64),
    )
    monkeypatch.setitem(sys.modules, "cuda", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "cuda.bindings", SimpleNamespace(driver=driver))
    assert flux_phase.cuda_device_info() == ("NVIDIA GB10", (12, 1))


def test_kernel_preparation_does_not_initialize_pytorch_or_synchronize(
    monkeypatch, tmp_path, capsys, flux_phase
):
    script = Path(flux_phase.__file__)
    phase = flux_phase
    request = tmp_path / "request.json"
    request.write_text("{}")
    monkeypatch.setattr(sys, "argv", [str(script), "--request", str(request), "--phase", "kernels"])
    monkeypatch.setattr(phase, "cuda_device_info", lambda: ("NVIDIA GB10", (12, 1)))

    class NoExecutionContext:
        def __getattr__(self, name):
            raise AssertionError(f"Compilation must not call torch.cuda.{name}")

    class Result(IntEnum):
        CUDA_SUCCESS = 0

    calls = []
    driver = SimpleNamespace(CUresult=Result, cuCtxGetCurrent=lambda: (Result.CUDA_SUCCESS, 0))
    modules = {
        "torch": SimpleNamespace(cuda=NoExecutionContext()),
        "cuda": SimpleNamespace(),
        "cuda.bindings": SimpleNamespace(driver=driver),
        "flashinfer": SimpleNamespace(),
        "flashinfer.gemm": SimpleNamespace(),
        "flashinfer.gemm.gemm_base": SimpleNamespace(
            get_gemm_sm120_module_cutlass_fp4=lambda: calls.append("gemm")
        ),
        "flashinfer.quantization": SimpleNamespace(),
        "flashinfer.quantization.fp4_quantization": SimpleNamespace(
            get_fp4_quantization_module=lambda architecture: calls.append(architecture)
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    # main sets native process environment; keep the test from changing the host.
    monkeypatch.setattr(phase.os, "environ", dict(phase.os.environ))
    phase.main()
    assert calls == ["121", "gemm"]
    assert phase.os.environ["FLASHINFER_CUDA_ARCH_LIST"] == "12.1"
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    completed = events[-1]
    assert completed["status"] == "completed"
    assert completed["execution_scope"] == "host_compile_and_load"
    assert completed["gpu_execution_verified"] is False
    assert completed["cuda_context_active"] is False
    assert "peak_allocated_bytes" not in completed and "peak_reserved_bytes" not in completed


def test_closed_cuda_oom_has_clear_error_without_relaunch(monkeypatch, tmp_path, flux_phase):
    message = flux_phase.failure_message(RuntimeError("CUDA error: out of memory"))
    (tmp_path / "stdout.log").write_text(
        json.dumps({"status": "failed", "message": message}) + "\n"
    )
    closed = {
        "owned_resources_closed": True,
        "supervisor_observation": "gone",
        "status": "failed",
        "exit_code": 1,
    }
    launches, stops = [], []
    monkeypatch.setattr(resources, "reserve", lambda *_: (tmp_path, "reserved"))
    monkeypatch.setattr(resources, "phase_plan", lambda *_: tmp_path / "plan.json")

    def launch(*args):
        launches.append(args)
        return SimpleNamespace(status="acknowledged", state=None)

    def stop(*args, **kwargs):
        stops.append(args)
        return SimpleNamespace(state=closed)

    monkeypatch.setattr(resources, "launch_phase", launch)
    monkeypatch.setattr(resources, "record_progress", lambda *_: None)
    monkeypatch.setattr(resources, "record_receipt", lambda *_: None)
    monkeypatch.setattr(resources, "close_unlaunched", lambda *_, **__: False)
    monkeypatch.setattr(resources.native, "read_state", lambda _: closed)
    monkeypatch.setattr(resources.native, "stop", stop)
    monkeypatch.setattr(
        resources.psutil, "virtual_memory", lambda: SimpleNamespace(available=24 * 1024**3)
    )
    with pytest.raises(AppError) as failure:
        asyncio.run(resources.run_phase("run", "kernels", {"headroom_gib": 9}))
    assert failure.value.code == "media_phase_failed" and not failure.value.retryable
    assert "CUDA内存申请失败" in failure.value.message
    assert "不会自动重复生成" in failure.value.message
    assert len(launches) == len(stops) == 1
