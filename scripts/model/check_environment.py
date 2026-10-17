#!/usr/bin/env python3
"""Small real CUDA checks; never opens or loads model weights."""

import importlib.metadata as metadata
import json
import os
import platform
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from modelctl import CONFIG, now, write_json  # noqa: E402 - direct source entrypoint


def main():
    os.environ.setdefault("CUDA_HOME", "/usr/local/cuda")
    os.environ["PATH"] = (
        str(Path(sys.executable).parent) + ":/usr/local/cuda/bin:" + os.environ.get("PATH", "")
    )
    os.environ.setdefault("MAX_JOBS", "2")
    os.environ.setdefault("FLASHINFER_WORKSPACE_BASE", str(ROOT / ".local/model/cache/flashinfer"))
    os.environ.setdefault("TRITON_CACHE_DIR", str(ROOT / ".local/model/cache/triton"))
    import torch
    from sglang.srt.models.qwen4_exp_ple_table import check_file_backend_supported

    assert metadata.version("sglang") == CONFIG["engine_version"]
    assert platform.machine() == "aarch64", "This recipe targets the GB10 ARM64 host"
    assert torch.cuda.is_available(), "CUDA is not available"
    assert torch.version.cuda.startswith("13.0"), "Expected CUDA 13.0 PyTorch ABI"
    check_file_backend_supported()
    a = torch.ones((32, 32), device="cuda", dtype=torch.bfloat16)
    assert torch.equal(a @ a, torch.full_like(a, 32))
    # Official FlashInfer nvfp4_quantize/mm_fp4 API, with the CUTLASS layout.
    from flashinfer import SfLayout, mm_fp4, nvfp4_quantize

    matrix = torch.ones((128, 128), device="cuda", dtype=torch.bfloat16)
    global_scale = (448 * 6) / matrix.float().abs().max()
    packed, scales = nvfp4_quantize(
        matrix, global_scale, sfLayout=SfLayout.layout_128x4, do_shuffle=False
    )
    product = mm_fp4(
        packed, packed.T, scales, scales.T, 1.0 / (global_scale * global_scale), backend="cutlass"
    )
    torch.testing.assert_close(product, matrix @ matrix.T, rtol=0.03, atol=1)
    torch.cuda.synchronize()
    result = {
        "status": "passed",
        "checked_at": now(),
        "python": sys.version,
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "device": torch.cuda.get_device_name(),
        "compute_capability": torch.cuda.get_device_capability(),
        "packages": {
            name: metadata.version(name)
            for name in ("sglang", "flashinfer-python", "sglang-kernel", "transformers", "triton")
        },
        "checks": {
            "cuda_bf16_matmul": "passed",
            "nvfp4_cutlass_matmul": "passed",
            "file_backed_ple_device": "passed",
        },
        "model_loaded": False,
    }
    write_json(ROOT / ".local/model/evidence/environment.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        write_json(
            ROOT / ".local/model/evidence/environment.json",
            {
                "status": "failed",
                "checked_at": now(),
                "model_loaded": False,
                "error_type": type(error).__name__,
                "error": str(error),
                "traceback": traceback.format_exc(),
            },
        )
        raise
