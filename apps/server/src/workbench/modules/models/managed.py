"""Read the managed deployment's public manifest, without controlling its process."""

import json
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit

import httpx

from workbench.config import PROJECT_ROOT

SGLANG_CONTRACT = "sglang-0.5.21"
_CONTRACTS = OrderedDict()
_CONTRACT_LOCK = threading.Lock()


def matching_deployment(config):
    """A generic engine label or loopback address is not a managed identity."""
    if config.provider_type not in {"openai_compatible", "openai_chat"}:
        return None
    if config.engine_kind not in {"auto", "sglang"}:
        return None
    try:
        manifest = json.loads((PROJECT_ROOT / "runtime/model/deployment.json").read_text())
        actual = urlsplit(config.endpoint.rstrip("/"))
        expected = urlsplit(f"http://{manifest['host']}:{manifest['port']}/v1")
        if (
            actual.hostname not in {"localhost", "127.0.0.1", "::1"}
            or (actual.scheme, actual.hostname, actual.port, actual.path) != (
                expected.scheme, expected.hostname, expected.port, expected.path
            )
            or actual.username or actual.password or actual.query or actual.fragment
            or config.model_identifier != manifest["served_model_name"]
            or manifest.get("engine") != "sglang"
            or manifest.get("engine_version") != "0.5.21"
            or manifest.get("engine_source_commit") != "e00930c5489053f26d86b179cee0d087f846acbb"
        ):
            return None
        return manifest
    except (OSError, ValueError, KeyError, TypeError):
        return None


def verified_contract(config):
    """Read non-generating metadata; unknown versions keep generic semantics."""
    manifest = matching_deployment(config)
    if manifest is None:
        return None
    key = (config.endpoint, config.model_identifier, manifest["engine_source_commit"])
    with _CONTRACT_LOCK:
        cached = _CONTRACTS.get(key)
        if cached and time.monotonic() < cached[0]:
            _CONTRACTS.move_to_end(key)
            return cached[1]
    actual = urlsplit(config.endpoint)
    url = f"{actual.scheme}://{actual.netloc}/server_info"
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    contract = None
    started = time.monotonic()
    try:
        with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
            with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 256 * 1024 or time.monotonic() - started > 2:
                        raise ValueError("Server metadata exceeds contract budget")
        info = json.loads(data)
        if (
            info.get("version") == "0.5.21"
            and info.get("served_model_name") == manifest["served_model_name"]
            and info.get("model_path") == str((PROJECT_ROOT / manifest["model_path"]).resolve())
        ):
            contract = SGLANG_CONTRACT
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    with _CONTRACT_LOCK:
        _CONTRACTS[key] = (time.monotonic() + (30 if contract else 5), contract)
        _CONTRACTS.move_to_end(key)
        while len(_CONTRACTS) > 128:
            _CONTRACTS.popitem(last=False)
    return contract


def default_connection() -> dict | None:
    path = PROJECT_ROOT / "runtime/model/deployment.json"
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return {
        "name": "Flash-Next · 本机",
        "provider_type": "openai_compatible",
        "endpoint": f"http://{manifest['host']}:{manifest['port']}/v1",
        "model_identifier": manifest["served_model_name"],
        "context_window": manifest["context_length"],
        "capabilities": {
            key: support
            if isinstance(support, dict)
            else {"support": support, "evidence_status": "declared"}
            for key, support in manifest.get("capabilities", {}).items()
        },
    }
