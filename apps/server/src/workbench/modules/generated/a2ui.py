"""Pure, bounded A2UI v0.9.1 snapshot adapter; publication remains the host's job."""

from __future__ import annotations

import copy
import json
import math
import re
from typing import Any, Literal

from pydantic import ConfigDict, Field
from workbench.contracts.base import PublicResponse
from workbench.contracts.errors import AppError
from workbench.contracts.requests import Command
from workbench.modules.files.exports_dto import ArtifactReference
from workbench.modules.generated.dto import SurfaceDraft

ADAPTER_VERSION = "workbench.a2ui:1"
SPEC_REVISION = "45633b97636daf89bafb43fa7db23e3cd916461d"
CUSTOM_CATALOG = "workbench-core/1"
BASIC_CATALOGS = frozenset(
    {
        "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json",
        "https://a2ui.org/specification/v0_9_1/catalogs/basic/catalog.json",
    }
)
MAX_BYTES = 1024 * 1024
MAX_MESSAGES = 128
MAX_COMPONENTS = 64
_MISSING = object()
_KINDS = {"createSurface", "updateComponents", "updateDataModel", "deleteSurface"}


class A2UIInput(Command):
    # JSON objects are deliberately inspected before interpreting their contents.
    messages: list[dict[str, Any]] | dict[str, Any]
    surface_id: str = Field(min_length=1, max_length=256, strict=True)
    title: str = Field(min_length=1, max_length=200, strict=True)
    sources: list[ArtifactReference] = Field(default_factory=list, max_length=16)
    bindings: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=64)


class Diagnostic(PublicResponse):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    component_id: str | None = None


class A2UIResult(PublicResponse):
    model_config = ConfigDict(extra="forbid")
    adapter_version: Literal["workbench.a2ui:1"] = ADAPTER_VERSION
    specification_version: Literal["v0.9.1"] = "v0.9.1"
    specification_revision: Literal["45633b97636daf89bafb43fa7db23e3cd916461d"] = SPEC_REVISION
    wire_version: Literal["v0.9", "v0.9.1"]
    surface_id: str
    catalog_id: str
    target_catalog: Literal["workbench-core/1"] = CUSTOM_CATALOG
    source_validation: Literal["host_required"] = "host_required"
    status: Literal["ready", "incomplete", "deleted"]
    # These are adapter-local replay counters, never official wire fields or CAS.
    revision: int
    generation: int
    message_count: int
    missing_component_ids: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    draft: SurfaceDraft | None = None


def _reject(code: str, message: str) -> None:
    raise AppError("a2ui_" + code, message, 422, "validation")


def _json_budget(value: Any) -> None:
    remaining = 12000

    def visit(item, depth):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 20:
            _reject("budget", "A2UI JSON超过条目或深度预算。")
        if item is None or type(item) in {str, bool, int}:
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                visit(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                visit(child, depth + 1)
            return
        _reject("json_invalid", "A2UI只接受有限数值及普通JSON数据。")

    visit(value, 0)
    try:
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError):
        _reject("json_invalid", "A2UI不是有效UTF-8 JSON。")
    if size > MAX_BYTES:
        _reject("budget", "A2UI JSON超过1 MiB预算。")


def _keys(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys():
        _reject("shape", "A2UI缺少必要字段或对象类型无效。")
    if value.keys() - set(required) - set(optional):
        _reject("unsupported_field", "A2UI含本适配器不支持的字段；不会忽略动作或样式。")


def _identifier(value):
    if (
        type(value) is not str
        or not 1 <= len(value) <= 256
        or any(ord(char) < 32 for char in value)
    ):
        _reject("identifier", "A2UI标识必须为1至256字符且不含控制字符。")
    return value


def _text(value):
    if type(value) is not str or len(value) > 100000:
        _reject("text", "A2UI文本必须为不超过100000字符的字符串。")
    return value


def _pointer(path):
    if type(path) is not str or len(path) > 1000 or path and not path.startswith("/"):
        _reject("pointer", "A2UI数据路径必须为有界JSON Pointer。")
    if re.search(r"~(?![01])", path):
        _reject("pointer", "A2UI JSON Pointer转义无效。")
    # A2UI defines '/' as the whole model, unlike the empty-key RFC6901 pointer.
    if path in {"", "/"}:
        return []
    parts = [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]
    if len(parts) > 16:
        _reject("budget", "A2UI数据路径超过16层。")
    return parts


def _update(data, message):
    parts = _pointer(message.get("path", "/"))
    if not parts:
        return copy.deepcopy(message["value"]) if "value" in message else _MISSING
    if data is _MISSING:
        data = {}
    target = data
    for part in parts[:-1]:
        if type(target) is not dict:
            _reject("data_path_unsupported", "仅支持对象键路径更新，不改写数组索引或跨越标量。")
        if part not in target:
            if "value" not in message:
                return data  # Removing a nonexistent object key has no effect.
            target[part] = {}
        target = target[part]
    if type(target) is not dict:
        _reject("data_path_unsupported", "仅支持对象键路径更新，不改写数组索引或跨越标量。")
    if "value" in message:
        target[parts[-1]] = copy.deepcopy(message["value"])
    else:
        target.pop(parts[-1], None)
    return data


def _dynamic(value, data):
    if type(value) is not dict:
        return value
    _keys(value, {"path"})  # FunctionCall, functions and handlers are never evaluated.
    target = data
    for part in _pointer(value["path"]):
        if target is _MISSING:
            break
        if type(target) is not dict:
            _reject("data_path_unsupported", "仅支持对象路径绑定，不解析数组索引或标量子路径。")
        target = target.get(part, _MISSING)
    return target


def _children(value):
    if type(value) is not list or len(value) > MAX_COMPONENTS:
        _reject("children_unsupported", "仅支持至多64个静态children ID，不支持模板。")
    result = [_identifier(item) for item in value]
    if len(set(result)) != len(result):
        _reject("children_duplicate", "同一组件不能重复引用子节点。")
    return result


def _shape(node, custom):
    if type(node) is not dict or not {"id", "component"} <= node.keys():
        _reject("component_shape", "组件必须是含id/component的JSON对象。")
    _identifier(node["id"])
    kind = node["component"]
    if type(kind) is not str:
        _reject("component_unsupported", "组件类型必须为当前目录中的名称。")
    if kind == "Text":
        _keys(node, {"id", "component", "text"}, {"variant"})
        if node.get("variant", "body") != "body":
            _reject("variant_unsupported", "Text子集仅支持body纯文本，不忽略其他variant。")
        if type(node["text"]) is dict:
            _keys(node["text"], {"path"})
            _pointer(node["text"]["path"])
        else:
            _text(node["text"])
    elif kind in {"Column", "Row"} or kind == "List" and not custom:
        _keys(node, {"id", "component", "children"}, {"direction"} if kind == "List" else ())
        _children(node["children"])
        if kind == "List":
            direction = node.get("direction", "vertical")
            if type(direction) is not str or direction not in {"vertical", "horizontal"}:
                _reject("list_direction", "Basic List方向必须为vertical或horizontal。")
    elif kind == "Card":
        _keys(node, {"id", "component", "child"})
        _identifier(node["child"])
    elif kind == "List" and custom:
        _keys(node, {"id", "component", "items"})
        value = node["items"]
        if type(value) is dict:
            _keys(value, {"path"})
            _pointer(value["path"])
        elif type(value) is not list or len(value) > 1000:
            _reject("list_items", "自有List仅接受至多1000项文本。")
        else:
            for item in value:
                _text(item)
    elif kind == "Table" and custom:
        _keys(node, {"id", "component", "columns"}, {"rows"})
        if type(node["columns"]) is not list or not 1 <= len(node["columns"]) <= 50:
            _reject("table_columns", "自有Table必须提供1至50个列名。")
        for column in node["columns"]:
            _text(column)
        if "rows" in node and node["rows"] != []:
            _reject(
                "table_model_data", "Table数据行只能来自真实artifact绑定，不能来自模型或dataModel。"
            )
    elif kind == "Chart" and custom:
        _keys(node, {"id", "component", "kind", "x_label", "y_label", "unit"}, {"points"})
        if type(node["kind"]) is not str or node["kind"] not in {"bar", "line"}:
            _reject("chart_kind", "自有Chart仅支持bar或line。")
        for key in ("x_label", "y_label", "unit"):
            _text(node[key])
        if "points" in node and node["points"] != []:
            _reject(
                "chart_model_data", "Chart数值只能来自真实artifact绑定，不能来自模型或dataModel。"
            )
    elif kind == "FileLink" and custom:
        _keys(node, {"id", "component", "artifact_id", "label"})
        _identifier(node["artifact_id"])
        _text(node["label"])
    else:
        _reject(
            "component_unsupported", "组件不在当前目录子集中；Table/Chart/FileLink只属于自有目录。"
        )


def _map(node, custom, data):
    kind, props, children = node["component"], {}, []
    if kind == "Text":
        value = _dynamic(node["text"], data)
        if value is _MISSING:
            return None
        props = {"text": _text(value)}
    elif kind in {"Column", "Row"}:
        children = list(node["children"])
    elif kind == "Card":
        children = [node["child"]]
    elif kind == "List" and not custom:
        kind = "Row" if node.get("direction", "vertical") == "horizontal" else "Column"
        children = list(node["children"])
    elif kind == "List":
        value = _dynamic(node["items"], data)
        if value is _MISSING:
            return None
        if type(value) is not list or len(value) > 1000:
            _reject("list_items", "自有List仅接受至多1000项文本。")
        props = {"items": [_text(item) for item in value]}
    elif kind == "Table":
        props = {"columns": list(node["columns"]), "rows": []}
    elif kind == "Chart":
        props = {key: node[key] for key in ("kind", "x_label", "y_label", "unit")}
        props["points"] = []
    elif kind == "FileLink":
        props = {"artifact_id": node["artifact_id"], "label": node["label"]}
    return {"id": node["id"], "type": kind, "props": props, "children": children}


def _references(node, custom):
    if node["component"] == "Card":
        return [node["child"]]
    if node["component"] in {"Row", "Column"} or node["component"] == "List" and not custom:
        return node["children"]
    return []


def _graph(nodes, custom):
    parents, missing = {}, set()
    for node in nodes.values():
        for child in _references(node, custom):
            parents[child] = parents.get(child, 0) + 1
            if parents[child] > 1 or child == "root":
                _reject("tree_invalid", "组件树存在多父节点或对root的反向引用。")
            if child not in nodes:
                missing.add(child)
    if "root" not in nodes:
        missing.add("root")
    reached = set()

    def walk(identifier, path, connected):
        if identifier in path or len(path) >= 8:
            _reject("tree_invalid", "组件树存在循环或超过8层。")
        if identifier not in nodes:
            return
        if connected:
            reached.add(identifier)
        for child in _references(nodes[identifier], custom):
            walk(child, path | {identifier}, connected)

    walk("root", set(), True)
    for identifier in nodes.keys() - reached:
        walk(identifier, set(), False)
    return sorted(missing), sorted(nodes.keys() - reached)


def _binding(node, binding, source_ids):
    if node["component"] not in {"Table", "Chart"}:
        if binding is not None:
            _reject("binding_target", "仅Table或Chart接受artifact数据绑定。")
        return
    if binding is None:
        return
    table = node["component"] == "Table"
    required = (
        {"artifact_id", "fields"} if table else {"artifact_id", "label_pointer", "value_pointer"}
    )
    _keys(binding, required, {"pointer"})
    if type(binding["artifact_id"]) is not str or binding["artifact_id"] not in source_ids:
        _reject("binding_source", "数据绑定必须引用本次明确提供的artifact版本。")
    _pointer(binding.get("pointer", ""))
    if table:
        fields = binding["fields"]
        if type(fields) is not list or len(fields) != len(node["columns"]):
            _reject("binding_fields", "Table绑定字段必须与列数相同。")
        for path in fields:
            _pointer(path)
    else:
        _pointer(binding["label_pointer"])
        _pointer(binding["value_pointer"])


def adapt(command: A2UIInput) -> A2UIResult:
    """Replay one supplied ordered snapshot; never load sources or publish a surface.

    Callers must persist input/result and apply their own authorization and CAS.
    Only a ready result may be passed to generated.service.create, which alone
    validates artifact ownership, current versions and actual bound JSON records.
    """
    if not isinstance(command, A2UIInput):
        command = A2UIInput.model_validate(command)
    _identifier(command.surface_id)
    _json_budget(command.model_dump(mode="python", exclude={"sources"}))
    raw = command.messages
    if type(raw) is dict and "messages" in raw:
        _keys(raw, {"messages"})
        messages = raw["messages"]
    else:
        messages = [raw] if type(raw) is dict else raw
    if type(messages) is not list or not 1 <= len(messages) <= MAX_MESSAGES:
        _reject("message_budget", "请提供1至128条有序A2UI信封。")
    source_ids = [str(item.artifact_id) for item in command.sources]
    if len(set(source_ids)) != len(source_ids):
        _reject("source_duplicate", "同一artifact不能在本次来源中重复声明版本。")
    nodes, data, catalog, version = {}, _MISSING, None, None
    active, revision, generation = False, 0, 0
    for envelope in messages:
        if type(envelope) is not dict:
            _reject("shape", "每条A2UI信封必须为JSON对象。")
        kinds = envelope.keys() & _KINDS
        if len(kinds) != 1:
            _reject("envelope", "每条A2UI信封必须且只能有一个已知消息类型。")
        kind = next(iter(kinds))
        _keys(envelope, {"version", kind})
        wire = envelope["version"]
        if wire not in ("v0.9", "v0.9.1"):
            _reject("version", "仅接受官方v0.9.1 schema所允许的v0.9/v0.9.1信封。")
        if version is not None and wire != version:
            _reject("version_changed", "单次快照不能混合wire版本。")
        version = wire
        body = envelope[kind]
        fields = {
            "createSurface": ({"surfaceId", "catalogId"}, {"sendDataModel"}),
            "updateComponents": ({"surfaceId", "components"}, set()),
            "updateDataModel": ({"surfaceId"}, {"path", "value"}),
            "deleteSurface": ({"surfaceId"}, set()),
        }
        _keys(body, *fields[kind])
        if _identifier(body["surfaceId"]) != command.surface_id:
            _reject("surface_scope", "本适配器一次只重放指定surface的完整快照。")
        revision += 1
        if kind == "createSurface":
            if active:
                _reject("surface_exists", "活跃surface不能重复create；必须先delete。")
            if type(body["catalogId"]) is not str or body["catalogId"] not in BASIC_CATALOGS | {
                CUSTOM_CATALOG
            }:
                _reject("catalog", "目录ID未受支持；不会下载或猜测目录。")
            if "sendDataModel" in body and body["sendDataModel"] is not False:
                _reject("send_data_unsupported", "本只读适配器不向外发送dataModel。")
            catalog, nodes, data = body["catalogId"], {}, _MISSING
            active, generation = True, generation + 1
            continue
        if not active:
            _reject("surface_inactive", "update/delete必须对应已create且未delete的surface。")
        if kind == "deleteSurface":
            active, nodes, data = False, {}, _MISSING
        elif kind == "updateDataModel":
            data = _update(data, body)
            if data is not _MISSING:
                _json_budget(data)
        else:
            updates = body["components"]
            if type(updates) is not list or not 1 <= len(updates) <= MAX_COMPONENTS:
                _reject("component_budget", "每次更新须含1至64个组件。")
            seen = set()
            for node in updates:
                _shape(node, catalog == CUSTOM_CATALOG)
                identifier = node["id"]
                if identifier in seen:
                    _reject("component_duplicate", "同一更新中的组件ID不能重复。")
                seen.add(identifier)
                nodes[identifier] = copy.deepcopy(node)
            if len(nodes) > MAX_COMPONENTS:
                _reject("component_budget", "当前surface超过64个组件。")
            _graph(nodes, catalog == CUSTOM_CATALOG)
    base = dict(
        wire_version=version,
        surface_id=command.surface_id,
        catalog_id=catalog,
        revision=revision,
        generation=generation,
        message_count=len(messages),
    )
    if not active:
        return A2UIResult(
            **base,
            status="deleted",
            diagnostics=[
                Diagnostic(
                    code="surface_deleted", message="本次快照以删除结束，不发布或恢复历史UI。"
                )
            ],
        )
    custom = catalog == CUSTOM_CATALOG
    missing, unreachable = _graph(nodes, custom)
    diagnostics = []
    if missing:
        diagnostics.append(
            Diagnostic(code="components_incomplete", message="root或子节点尚未定义。")
        )
    if unreachable:
        diagnostics.append(
            Diagnostic(code="components_unreachable", message="存在尚未接入root的组件。")
        )
    if command.bindings.keys() - nodes.keys():
        _reject("binding_target", "数据绑定指向当前快照未定义的组件。")
    mapped = []
    for node in nodes.values():
        binding = command.bindings.get(node["id"])
        _binding(node, binding, source_ids)
        if node["component"] in {"Table", "Chart"} and binding is None:
            diagnostics.append(
                Diagnostic(
                    code="source_binding_required",
                    component_id=node["id"],
                    message="缺少真实artifact绑定；不使用dataModel填数据。",
                )
            )
        if node["component"] == "FileLink" and node["artifact_id"] not in source_ids:
            _reject("file_source", "FileLink必须指向本次明确提供的artifact版本。")
        value = _map(node, custom, data)
        if value is None:
            diagnostics.append(
                Diagnostic(
                    code="data_incomplete",
                    component_id=node["id"],
                    message="声明的数据路径尚无值，不补默认内容。",
                )
            )
        else:
            mapped.append(value)
    if diagnostics:
        return A2UIResult(
            **base, status="incomplete", missing_component_ids=missing, diagnostics=diagnostics
        )
    draft = SurfaceDraft(
        title=command.title,
        root_id="root",
        components=mapped,
        sources=command.sources,
        bindings=copy.deepcopy(command.bindings),
    )
    return A2UIResult(**base, status="ready", draft=draft)
