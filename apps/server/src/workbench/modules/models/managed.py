"""Read the managed deployment's public manifest, without controlling its process."""

import json

from workbench.config import PROJECT_ROOT


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
