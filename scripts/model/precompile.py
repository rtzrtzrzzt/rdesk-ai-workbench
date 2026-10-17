#!/usr/bin/env python3
"""Official offline kernel entrypoints, executed by modelctl's memory guard."""

import importlib.metadata as metadata
import json
import sys
import time

from modelctl import CONFIG, DATA, ROOT, now, write_json


def main():
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("Use: python3 scripts/model/modelctl.py precompile")
    import torch
    from flashinfer.jit.fused_moe import gen_cutlass_fused_moe_sm120_module

    assert metadata.version("sglang") == CONFIG["engine_version"]
    assert metadata.version("flashinfer-python") == "0.6.18"
    torch.cuda.init()
    spec = gen_cutlass_fused_moe_sm120_module()
    before = sorted(str(p.name) for p in spec.build_dir.glob("*.o"))
    print(
        json.dumps(
            {
                "at": now(),
                "stage": "fused_moe_120",
                "build_dir": str(spec.build_dir),
                "existing_objects": len(before),
                "model_loaded": False,
            }
        ),
        flush=True,
    )
    started = time.monotonic()
    spec.build(verbose=True, need_lock=True)
    library = spec.get_library_path()
    if not library.exists():
        raise RuntimeError("Official MoE build returned without a shared library")
    print(json.dumps({"at": now(), "stage": "gdn_decode_prefill"}), flush=True)
    # Numerical kernel warmup, using this checkpoint's actual dimensions.
    # No model weights or synthetic business/example results are loaded.
    from sglang.srt.layers.attention.linear.kernels.gdn_triton import TritonGDNKernel

    text = json.loads((ROOT / CONFIG["model_path"] / "config.json").read_text())["text_config"]
    h, hv = text["linear_num_key_heads"], text["linear_num_value_heads"]
    kdim, vdim = text["linear_key_head_dim"], text["linear_value_head_dim"]
    kernel = TritonGDNKernel()
    states = torch.zeros((1, hv, vdim, kdim), device="cuda", dtype=torch.float32)
    indices = torch.zeros((1,), device="cuda", dtype=torch.int64)
    packed = torch.zeros((1, 2 * h * kdim + hv * vdim), device="cuda", dtype=torch.bfloat16)
    gates = torch.zeros((1, hv), device="cuda", dtype=torch.bfloat16)
    log_decay = torch.zeros((hv,), device="cuda", dtype=torch.float32)
    output = kernel.packed_decode(
        packed,
        gates,
        gates,
        A_log=log_decay,
        dt_bias=log_decay,
        scale=kdim**-0.5,
        ssm_states=states,
        cache_indices=indices,
        num_v_heads=hv,
        head_v_dim=vdim,
    )
    assert torch.isfinite(output).all() and torch.count_nonzero(output) == 0
    tokens = CONFIG["chunked_prefill_size"]
    q = torch.zeros((1, tokens, h, kdim), device="cuda", dtype=torch.bfloat16)
    v = torch.zeros((1, tokens, hv, vdim), device="cuda", dtype=torch.bfloat16)
    g = torch.full((1, tokens, hv), -0.1, device="cuda", dtype=torch.float32)
    beta = torch.full_like(g, 0.5)
    boundaries = torch.tensor([0, tokens], device="cuda", dtype=torch.int32)
    output = kernel.extend(
        q, q, v, g, beta, ssm_states=states, cache_indices=indices, query_start_loc=boundaries
    )[0]
    assert torch.isfinite(output).all() and torch.count_nonzero(output) == 0
    torch.cuda.synchronize()
    result = {
        "checked_at": now(),
        "model_loaded": False,
        "status": "passed",
        "build_dir": str(spec.build_dir),
        "shared_library": str(library),
        "objects_before": len(before),
        "objects_after": len(list(spec.build_dir.glob("*.o"))),
        "elapsed_seconds": time.monotonic() - started,
        "gdn_dimensions": {
            "key_heads": h,
            "value_heads": hv,
            "key_dim": kdim,
            "value_dim": vdim,
            "prefill_tokens": tokens,
        },
        "gdn_packed_decode": "passed",
        "gdn_chunked_prefill": "passed",
    }
    write_json(DATA / "evidence/precompiled-kernels.json", result)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
