"""Local parsers for Agent Skills and Agent Plugins 1.0; no package code runs.

Contracts: https://agentskills.io/specification and
https://agent-plugins.org/specification . Unknown client extensions are diagnostics.
"""

import json
import re
from pathlib import Path

import jsonschema
import yaml

from workbench.contracts.errors import AppError
from workbench.modules.extensions.storage import contained, file_manifest

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
_IDENTITY = {
    "type": "object",
    "required": ["$schema", "name"],
    "additionalProperties": False,
    "properties": {
        "$schema": {"const": PLUGIN_SCHEMA},
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "pattern": r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$",
        },
        **{
            key: {"type": "string"}
            for key in ("version", "description", "homepage", "repository", "license")
        },
        "author": {
            "type": "object",
            "additionalProperties": False,
            "properties": {key: {"type": "string"} for key in ("name", "email", "url")},
        },
        "keywords": {"type": "array", "items": {"type": "string"}},
        "extensions": {"type": "object"},
    },
}


def read_json(path: Path):
    if not path.is_file() or path.stat().st_size > 1024**2:
        raise AppError("extension_manifest_invalid", "扩展清单缺失或超过1MiB。", 422)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise AppError("extension_manifest_invalid", "扩展清单必须是有效JSON对象。", 422) from None


def read_skill(root: Path, relative: str) -> dict:
    path = contained(root, relative)
    if not path.is_file() or path.stat().st_size > 256 * 1024:
        raise AppError("skill_invalid", "SKILL.md缺失或超过256KiB。", 422)
    try:
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        if not text.startswith("---\n"):
            raise ValueError
        boundary = text.find("\n---", 4)
        if boundary < 0 or boundary > 65536:
            raise ValueError
        metadata = yaml.safe_load(text[4:boundary])
        if not isinstance(metadata, dict):
            raise ValueError
        name, description = metadata.get("name"), metadata.get("description")
        if (
            not isinstance(name, str)
            or len(name) > 64
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
            or not isinstance(description, str)
            or not description.strip()
            or len(description) > 1024
        ):
            raise ValueError
        if relative != "SKILL.md" and path.parent.name != name:
            raise ValueError
    except (ValueError, UnicodeError, yaml.YAMLError, RecursionError):
        raise AppError(
            "skill_invalid", "Skill需有效name/description，名称须匹配目录。", 422
        ) from None
    implicit = True
    config = path.parent / "agents/openai.yaml"
    if config.is_file() and config.stat().st_size <= 65536:
        extra = yaml.safe_load(config.read_text(encoding="utf-8"))
        policy = extra.get("policy", {}) if isinstance(extra, dict) else {}
        implicit = not (
            isinstance(policy, dict) and policy.get("allow_implicit_invocation") is False
        )
    return {
        "name": name,
        "description": description.strip(),
        "path": relative,
        "directory": str(Path(relative).parent).replace("\\", "/"),
        "license": metadata.get("license") if isinstance(metadata.get("license"), str) else None,
        "implicit_invocation": implicit,
    }


def inspect_package(root: Path) -> dict:
    diagnostics = []
    skills, servers, manifest = [], [], {}
    portable = (root / "plugin.json").exists()
    legacy = (root / ".codex-plugin/plugin.json").exists()
    if portable:
        raw = read_json(contained(root, "plugin.json"))
        for key in set(raw) - set(_IDENTITY["properties"]):
            diagnostics.append(f"忽略非标准清单字段：{key[:80]}")
        manifest = {key: value for key, value in raw.items() if key in _IDENTITY["properties"]}
        if "extensions" in manifest and not isinstance(manifest["extensions"], dict):
            diagnostics.append("extensions不是对象，已忽略。")
            manifest.pop("extensions")
        try:
            jsonschema.Draft202012Validator(_IDENTITY).validate(manifest)
            if "--" in manifest["name"] or ".." in manifest["name"]:
                raise ValueError
        except (jsonschema.ValidationError, ValueError):
            raise AppError(
                "plugin_manifest_invalid",
                "Agent Plugins清单版本、名称或字段类型不符合1.0规范。",
                422,
            ) from None
        format_name, skill_roots, mcp_path = "agent-plugins/1.0.0", ["skills"], "mcp.json"
        overlay = manifest.get("extensions", {}).get("com.openai")
        if overlay is not None:
            diagnostics.append("OpenAI扩展字段仅作兼容元数据；私有连接器、hooks和认证不会执行。")
        elif legacy:
            diagnostics.append("已使用根plugin.json身份；兼容Codex清单不覆盖标准组件位置。")
    elif legacy:
        manifest = read_json(contained(root, ".codex-plugin/plugin.json"))
        if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
            raise AppError("plugin_manifest_invalid", "Codex兼容清单缺少name。", 422)
        declared = manifest.get("skills", "./skills/")
        skill_roots = [declared] if isinstance(declared, str) else declared
        if not isinstance(skill_roots, list) or any(
            not isinstance(item, str) for item in skill_roots
        ):
            raise AppError(
                "plugin_manifest_invalid", "Codex skills字段须为相对目录或目录列表。", 422
            )
        format_name, mcp_path = "codex-compat/1", ".mcp.json"
        if (
            any(key in manifest for key in ("hooks", "apps", "agents"))
            or (root / ".app.json").exists()
        ):
            diagnostics.append(
                "本包包含宿主专用组件，仅导入可支持的Skill/MCP声明，私有组件未执行。"
            )
    elif (root / "SKILL.md").is_file():
        skill = read_skill(root, "SKILL.md")
        skills.append(skill)
        manifest = {"name": skill["name"], "description": skill["description"]}
        format_name, skill_roots, mcp_path = "agent-skills/1", [], None
    else:
        raise AppError(
            "extension_format_unknown", "目录中未找到SKILL.md、plugin.json或Codex插件清单。", 422
        )
    for relative in skill_roots:
        directory = contained(root, relative)
        if not directory.exists():
            continue
        if not directory.is_dir():
            diagnostics.append(f"Skill目录不可用：{relative}")
            continue
        entries = [directory] if (directory / "SKILL.md").is_file() else sorted(directory.iterdir())
        for entry in entries:
            if not entry.is_dir() or not (entry / "SKILL.md").exists():
                continue
            try:
                item = read_skill(root, (entry / "SKILL.md").relative_to(root).as_posix())
                if item["name"] in {row["name"] for row in skills}:
                    diagnostics.append(f"重复Skill名称已跳过：{item['name']}")
                    continue
                config = entry / "agents/openai.yaml"
                if config.is_file() and config.stat().st_size <= 65536:
                    extra = yaml.safe_load(config.read_text())
                    policy = extra.get("policy", {}) if isinstance(extra, dict) else {}
                    if (
                        isinstance(policy, dict)
                        and policy.get("allow_implicit_invocation") is False
                    ):
                        item["implicit_invocation"] = False
                skills.append(item)
            except (AppError, yaml.YAMLError, ValueError, UnicodeError):
                diagnostics.append(f"无效Skill已跳过：{entry.name[:80]}")
    if mcp_path and (root / mcp_path).exists():
        try:
            raw = read_json(contained(root, mcp_path))
            if portable and raw.get("$schema") != MCP_SCHEMA:
                raise ValueError
            definitions = raw.get("mcpServers")
            if not isinstance(definitions, dict) or len(definitions) > 64:
                raise ValueError
            for name, server in definitions.items():
                if not isinstance(server, dict):
                    diagnostics.append(f"MCP声明无效：{str(name)[:80]}")
                    continue
                transport = server.get("type") or ("stdio" if "command" in server else "http")
                if transport == "streamable-http":
                    transport = "streamable_http"
                unsupported = set(server) - {"type", "command", "args", "cwd", "url"}
                reason = "包含需手动配置的env/headers/认证或宿主扩展字段。" if unsupported else None
                if transport not in {"stdio", "http", "streamable_http"}:
                    reason = "此MCP传输尚未适配。"
                # Credentials in an imported config are never copied into the
                # public manifest or a new connection without the vault workflow.
                servers.append(
                    {
                        "name": str(name)[:100],
                        "transport": transport,
                        "configuration": {
                            key: server[key]
                            for key in ("command", "args", "cwd", "url")
                            if key in server
                        },
                        "importable": reason is None,
                        "reason": reason,
                    }
                )
        except (AppError, ValueError):
            diagnostics.append("MCP组件清单无效，其他Skill仍可使用。")
    files = file_manifest(root)
    return {
        "format": format_name,
        "name": manifest["name"],
        "description": manifest.get("description", ""),
        "declared_version": manifest.get("version"),
        "license": manifest.get("license"),
        "skills": skills,
        "mcp_servers": servers,
        "diagnostics": diagnostics,
        "files": files,
    }
