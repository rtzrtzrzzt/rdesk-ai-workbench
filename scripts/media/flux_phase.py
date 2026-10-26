#!/usr/bin/env python3
"""One isolated FLUX3 phase. Launched only through the owned native supervisor.

Uses BFL's Apache-2.0 reference bfc8889a4497f846416f253a3972a1941ac698b9.
The flow-map update below follows flux3/sampling.py; model math/weights stay native.
No model is downloaded or remote Python loaded by this entry point.
"""

import argparse
import json
import os
import sys
import time
import traceback
from functools import partial
from pathlib import Path


def report(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


def failure_message(error):
    message = str(error)
    if "out of memory" in message.lower() and "cuda" in message.lower():
        return (
            "CUDA内存申请失败（OOM）；系统可用内存不代表CUDA可分配额度。"
            "本次任务不会自动重复生成，详细错误见本机阶段诊断日志。"
        )
    return message[:2000]


def save_tensors(values, path):
    from safetensors.torch import save_file

    temporary = str(path) + ".partial"
    save_file({key: value.detach().cpu().contiguous() for key, value in values.items()}, temporary)
    os.replace(temporary, path)


def cuda_device_info():
    """Query the driver without creating a PyTorch execution context."""
    from cuda.bindings import driver

    def checked(result):
        error, *values = result
        if error != driver.CUresult.CUDA_SUCCESS:
            raise RuntimeError(f"CUDA设备查询失败：{error.name}")
        return values

    checked(driver.cuInit(0))
    (device,) = checked(driver.cuDeviceGet(0))
    (major,) = checked(
        driver.cuDeviceGetAttribute(
            driver.CUdevice_attribute.CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MAJOR, device
        )
    )
    (minor,) = checked(
        driver.cuDeviceGetAttribute(
            driver.CUdevice_attribute.CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MINOR, device
        )
    )
    (name,) = checked(driver.cuDeviceGetName(128, device))
    return name.decode("utf-8").split("\0", 1)[0], (major, minor)


def prepare_kernels():
    device, capability = cuda_device_info()
    if capability[0] != 12:
        raise RuntimeError("This NVFP4 deployment requires a verified Blackwell CUDA device")
    # FlashInfer otherwise asks PyTorch to initialize CUDA just to select the
    # compilation target. Explicitly use the actual device and the same cache key.
    os.environ["FLASHINFER_CUDA_ARCH_LIST"] = f"{capability[0]}.{capability[1]}"
    started = time.monotonic()
    report(
        phase="kernels",
        status="running",
        device=device,
        compute_capability=list(capability),
        execution_scope="host_compile_and_load",
    )
    from flashinfer.gemm.gemm_base import get_gemm_sm120_module_cutlass_fp4
    from flashinfer.quantization.fp4_quantization import get_fp4_quantization_module

    get_fp4_quantization_module(f"{capability[0]}{capability[1]}")
    report(phase="kernels", status="running", compiled="nvfp4_quantization")
    get_gemm_sm120_module_cutlass_fp4()
    report(phase="kernels", status="running", compiled="cutlass_nvfp4_gemm")
    # Compilation/library registration launches no tensor operations. A CUDA
    # synchronization here needlessly materializes GPU resources and can OOM.
    # Actual encode/sample/decode still synchronize and report GPU peaks below.
    from cuda.bindings import driver

    error, context = driver.cuCtxGetCurrent()
    report(
        phase="kernels",
        status="completed",
        seconds=time.monotonic() - started,
        execution_scope="host_compile_and_load",
        gpu_execution_verified=False,
        cuda_context_active=int(context) != 0 if error == driver.CUresult.CUDA_SUCCESS else None,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=["kernels", "encode", "references", "sample", "decode"], required=True
    )
    args = parser.parse_args()
    request = json.loads(args.request.read_text())
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / ".runtime/media-source/src"))
    # The owned supervisor deliberately strips the shell PATH. CUDA extension
    # builds still need the locked environment's Ninja executable.
    os.environ["PATH"] = str(root / ".runtime/media-env/bin") + os.pathsep + os.defpath
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["HF_DEACTIVATE_ASYNC_LOAD"] = "1"
    os.environ["MAX_JOBS"] = "1"
    cache = root / ".runtime/media-cache"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ["FLASHINFER_WORKSPACE_BASE"] = str(cache)
    os.environ["TORCH_EXTENSIONS_DIR"] = str(cache / "torch-extensions")
    os.environ["TRITON_CACHE_DIR"] = str(cache / "triton")
    os.environ.setdefault("CUDA_MODULE_LOADING", "LAZY")
    os.environ.setdefault("CUDA_HOME", "/usr/local/cuda")
    if args.phase == "kernels":
        prepare_kernels()
        return
    # A phase starts with an empty CUDA context, not a second CPU weight copy.
    import torch

    if not torch.cuda.is_available() or torch.cuda.get_device_capability()[0] != 12:
        raise RuntimeError("This NVFP4 deployment requires a verified Blackwell CUDA device")
    device = "cuda:0"
    total = torch.cuda.get_device_properties(0).total_memory
    torch.cuda.set_per_process_memory_fraction(min(1.0, request["allocator_bytes"] / total))
    torch.set_num_threads(4)
    torch.set_grad_enabled(False)
    # CPU mmap views and CUDA copies use the same physical RAM on UMA. Keep
    # the upstream math/loader and bound staging with safetensors 0.8 pread.
    # The pinned Transformers API does not expose this backend selection;
    # the adapter only changes aliases in this disposable phase process.
    import flux3.checkpoint as checkpoint_module
    import safetensors
    import safetensors.torch

    checkpoint_module.load_file = partial(safetensors.torch.load_file, backend="pread")
    started = time.monotonic()
    output = Path(request["output_directory"])
    weights = Path(request["model_directory"])
    report(
        phase=args.phase,
        status="running",
        device=torch.cuda.get_device_name(),
        compute_capability=list(torch.cuda.get_device_capability()),
        weight_loading_backend="pread",
    )

    if args.phase == "encode":
        import transformers.modeling_utils as modeling_utils
        from flux3.text_encoder import MAX_LENGTH, TextEncoder
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

        modeling_utils.safe_open = partial(safetensors.safe_open, backend="pread")

        encoder = TextEncoder.__new__(TextEncoder)
        encoder.device = device
        encoder.processor = AutoProcessor.from_pretrained(
            weights / "text_encoder", local_files_only=True
        )
        encoder.processor.tokenizer.padding_side = "right"
        formatted = encoder.processor.apply_chat_template(
            [{"role": "user", "content": request["prompt"]}],
            tokenize=False,
            add_generation_prompt=True,
        )
        count = len(encoder.processor.tokenizer(formatted)["input_ids"])
        if count > MAX_LENGTH:
            raise ValueError(f"Caption has {count} tokens; this encoder supports {MAX_LENGTH}")
        encoder.model = Qwen3VLForConditionalGeneration.from_pretrained(
            weights / "text_encoder",
            dtype=torch.bfloat16,
            device_map={"": device},
            local_files_only=True,
            trust_remote_code=False,
        ).eval()
        tokens, identifiers = encoder.encode(request["prompt"])
        save_tensors({"text": tokens, "text_ids": identifiers}, output / "encoded.safetensors")
        report(phase=args.phase, prompt_tokens=count, encoded_tokens=tokens.shape[0])

    elif args.phase == "references":
        from flux3.autoencoder import AutoEncoder
        from flux3.checkpoint import load_checkpoint
        from flux3.pipeline import prepare_multi_reference, prepare_reference, scaled_ids, to_tensor
        from PIL import Image

        with torch.device("meta"):
            model = AutoEncoder()
        model = load_checkpoint(model, str(weights / "ae.safetensors"), device, torch.float32)
        tokens, identifiers = [], []
        for index, path in enumerate(request["references"]):
            with Image.open(path) as original:
                original.load()
                image = (
                    prepare_multi_reference(original)
                    if len(request["references"]) > 1
                    else prepare_reference(original)[0]
                )
            data, ids = model.encode(to_tensor(image).to(device))
            ids = scaled_ids(ids)
            ids[:, 3] = -(1 + index)
            tokens.append(data.to(torch.bfloat16))
            identifiers.append(ids)
            report(phase=args.phase, completed=index + 1, total=len(request["references"]))
        save_tensors(
            {"tokens": torch.cat(tokens), "ids": torch.cat(identifiers)},
            output / "references.safetensors",
        )

    elif args.phase == "sample":
        from flux3.autoencoder import latent_ids, latent_shape
        from flux3.checkpoint import is_flow_map, load_checkpoint
        from flux3.dit import IN_CHANNELS, DiT
        from flux3.pipeline import pack, scaled_ids
        from flux3.sampling import timesteps
        from safetensors.torch import load_file

        checkpoint = str(weights / "variants/nvfp4")
        if not is_flow_map(checkpoint):
            raise ValueError("Expected the distilled NVFP4 flow-map checkpoint")
        if request["attention_backend"] == "torch_sdpa":
            # Explicit deployment option, recorded with the output. The same
            # attention operation uses PyTorch's supported dispatcher on GB10.
            import flux3.dit as dit_module
            import torch.nn.functional as functional

            def attention(q, k, v, bounds):
                _, heads, length, dimension = q.shape
                result = torch.zeros(length, heads * dimension, device=q.device, dtype=q.dtype)
                for start, end in bounds:
                    if start != end:
                        part = functional.scaled_dot_product_attention(
                            q[:, :, start:end], k[:, :, start:end], v[:, :, start:end]
                        )
                        result[start:end] = part[0].transpose(0, 1).reshape(end - start, -1)
                return result

            dit_module.packed_attention = attention
        with torch.device("meta"):
            model = DiT(True)
        model = load_checkpoint(model, checkpoint, device, torch.bfloat16)
        from flux3.nvfp4 import NVFP4Linear

        quantized = sum(isinstance(module, NVFP4Linear) for module in model.modules())
        if not quantized:
            raise RuntimeError("The model did not load fused NVFP4 linears")
        encoded = load_file(str(output / "encoded.safetensors"), device=device)
        condition = None
        if request["references"]:
            references = load_file(str(output / "references.safetensors"), device=device)
            condition = references["tokens"], references["ids"]
        height, width = latent_shape(request["height"], request["width"])
        noise = torch.randn(
            (height * width, IN_CHANNELS["image"]),
            generator=torch.Generator().manual_seed(request["seed"]),
        )
        noise = noise.to(torch.bfloat16).float().to(device)
        ids = latent_ids(height, width, device)
        packed = pack(
            [[(encoded["text"], encoded["text_ids"]), condition, (noise, scaled_ids(ids))]]
        )
        schedule = timesteps(request["steps"], 16.0)
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            for step in range(len(schedule) - 1):
                current = schedule[step].item()
                delta = (schedule[step + 1] - schedule[step]).item()
                packed.timesteps["image"].fill_(current)
                target = schedule[step + 1].reshape(1).to(device)
                packed.data["image"] = packed.data["image"] + delta * model(
                    packed, timesteps_ref=target
                )
                torch.cuda.synchronize()
                report(
                    phase=args.phase, step=step + 1, total=request["steps"], nvfp4_linears=quantized
                )
        save_tensors({"tokens": packed.data["image"], "ids": ids}, output / "sampled.safetensors")

    else:
        from flux3.autoencoder import AutoEncoder
        from flux3.checkpoint import load_checkpoint
        from flux3.pipeline import to_pil
        from PIL.PngImagePlugin import PngInfo
        from safetensors.torch import load_file

        with torch.device("meta"):
            model = AutoEncoder()
        model = load_checkpoint(model, str(weights / "ae.safetensors"), device, torch.float32)
        sampled = load_file(str(output / "sampled.safetensors"), device=device)
        with torch.inference_mode():
            image = to_pil(model.decode(sampled["tokens"], sampled["ids"]))
        metadata = PngInfo()
        metadata.add_text(
            "workbench_generation",
            json.dumps(
                {
                    "model": "FLUX 3 Image Klein",
                    "precision": "NVFP4",
                    "seed": request["seed"],
                    "steps": request["steps"],
                    "prompt": request["prompt"],
                    "attention_backend": request["attention_backend"],
                },
                ensure_ascii=False,
            ),
        )
        temporary = output / "result.partial.png"
        image.save(temporary, format="PNG", pnginfo=metadata)
        os.replace(temporary, output / "result.png")
    torch.cuda.synchronize()
    report(
        phase=args.phase,
        status="completed",
        seconds=time.monotonic() - started,
        peak_allocated_bytes=torch.cuda.max_memory_allocated(),
        peak_reserved_bytes=torch.cuda.max_memory_reserved(),
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Private runner logs retain a useful diagnostic without any secrets.
        report(status="failed", error_type=type(error).__name__, message=failure_message(error))
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1) from None
