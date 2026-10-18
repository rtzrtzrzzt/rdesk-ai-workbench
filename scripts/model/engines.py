"""Engine launch/probe contracts, independent of host process supervision.

Importing this module never imports an inference runtime or opens model tensors.
Only SGLang has a managed launch recipe for this checkpoint. Other engines must
already run outside this guard and are probed through their declared endpoint.
"""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _flag(config: dict, key: str) -> bool:
    value = config.get(key, False)
    if type(value) is not bool:
        raise ValueError(f"{key} must be a JSON boolean")
    return value


@dataclass(frozen=True)
class ReadinessProbe:
    url: str
    model_identifier: str | None = None

    def check(self, open_http) -> bool:
        with open_http(self.url, timeout=2) as response:
            if response.status != 200:
                return False
            if self.model_identifier is None:
                return True
            # A live OpenAI server may serve another model. Do not accept it as
            # readiness for this deployment, or make a generation health check.
            payload = response.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                return False
            body = json.loads(payload)
            entries = body.get("data") if isinstance(body, dict) else None
            return isinstance(entries, list) and any(
                isinstance(item, dict) and item.get("id") == self.model_identifier
                for item in entries
            )


class SGLangDriver:
    def __init__(self, root: Path, config: dict):
        self.root, self.config = root, config

    def launch_command(self) -> list[str]:
        c = self.config
        if c.get("management_mode", "managed") != "managed":
            raise ValueError("Use an external engine driver for external management")
        python = self.root / c["python"]
        if not python.is_file() or not os.access(python, os.X_OK):
            raise RuntimeError("Configured SGLang Python executable is not installed")
        model = self.root / c["model_path"]
        model_config = json.loads((model / "config.json").read_text())
        if model_config.get("architectures") != ["Qwen4ExpForConditionalGeneration"]:
            raise RuntimeError("This managed SGLang recipe requires the Qwen4Exp checkpoint")
        if c.get("offload_backend") != "file":
            raise RuntimeError("This GB10 managed recipe requires file-backed PLE")
        args = [
            str(python), "-m", "sglang.launch_server",
            "--model-path", str(model),
            "--served-model-name", c["served_model_name"],
            "--host", c["host"], "--port", str(c["port"]),
            "--context-length", str(c["context_length"]),
            "--max-running-requests", str(c["max_running_requests"]),
            "--max-total-tokens", str(c["max_total_tokens"]),
            "--model-impl", "sglang",
            "--chunked-prefill-size", str(c["chunked_prefill_size"]),
            "--mem-fraction-static", str(c["mem_fraction_static"]),
            "--ple-offload-embedding", "--ple-offload-backend", "file",
            "--ple-offload-dir", str(self.root / c["ple_directory"]),
            "--moe-runner-backend", c["moe_runner_backend"],
            "--reasoning-parser", "qwen3", "--tool-call-parser", "qwen3_coder",
        ]
        if _flag(c, "mtp"):
            args += [
                "--speculative-algorithm", "NEXTN",
                "--speculative-num-steps", "3",
                "--speculative-eagle-topk", "1",
                "--speculative-num-draft-tokens", "4",
                "--linear-attn-verify-backend", "triton",
            ]
        if _flag(c, "enable_metrics"):
            args.append("--enable-metrics")
        return args

    def readiness(self) -> ReadinessProbe:
        c = self.config
        return ReadinessProbe(f"http://{c['host']}:{c['port']}/ready")

    def capabilities(self) -> dict:
        return {
            "launch": "managed",
            "readiness": {"method": "GET", "path": "/ready", "generates_tokens": False},
            "abort": {"method": "POST", "path": "/abort_request", "scope": "request_id"},
            "tokenize": {"method": "POST", "path": "/v1/tokenize"},
            "metrics": {"path": "/metrics", "enabled": _flag(self.config, "enable_metrics")},
            "verification": "runtime_contract; model evidence remains deployment-specific",
        }


class ExternalEngineDriver:
    """An explicitly configured existing OpenAI endpoint, never an owned child."""

    def __init__(self, config: dict):
        self.config = config
        if config.get("management_mode") != "external":
            raise RuntimeError(
                f"No managed launch recipe for {config['engine']} and this checkpoint; "
                "configure an existing endpoint with management_mode=external"
            )
        self.base_url = config.get("external_base_url", "").rstrip("/")
        url = urlsplit(self.base_url)
        # The controller's open_local enforces literal loopback on each request.
        if (
            url.scheme != "http" or url.path != "/v1" or url.query or url.fragment
            or url.username or url.password
            or not ipaddress.ip_address(url.hostname or "").is_loopback
        ):
            raise ValueError("external_base_url must be a literal-loopback HTTP /v1 endpoint")
        if not isinstance(config.get("served_model_name"), str) or not config["served_model_name"]:
            raise ValueError("External readiness requires an explicit served_model_name")

    def launch_command(self) -> list[str]:
        raise RuntimeError(
            "External engine is not owned by modelctl; start it with its existing "
            "service manager after independently validating runtime and weight compatibility"
        )

    def readiness(self) -> ReadinessProbe:
        return ReadinessProbe(self.base_url + "/models", self.config["served_model_name"])

    def capabilities(self) -> dict:
        return {
            "launch": "external",
            "readiness": {"method": "GET", "path": "/v1/models", "generates_tokens": False},
            "abort": None, "tokenize": None, "metrics": None,
            "verification": "unverified; OpenAI wire does not establish engine extensions",
        }


def engine_for(root: Path, config: dict):
    engine = config.get("engine")
    if engine == "sglang" and config.get("management_mode", "managed") == "managed":
        return SGLangDriver(root, config)
    if engine in {"sglang", "vllm", "llama_cpp", "tensorrt_llm"}:
        return ExternalEngineDriver(config)
    raise ValueError(f"Unsupported model engine: {engine!r}")
