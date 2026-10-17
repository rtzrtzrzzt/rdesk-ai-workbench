"""Publish checked, immutable UI snapshots through the existing artifact lifecycle."""

import copy
import hashlib
import json
import math
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules.context.transfers import allowed_result
from workbench.modules.files.domain import Artifact
from workbench.modules.files.service import get_artifact
from workbench.modules.files.storage import file_digest, publish_bytes, storage_path
from workbench.modules.generated.dto import SurfaceDraft
from workbench.modules.runs.domain import Run
from workbench.modules.runs.ownership import assert_owned
from workbench.modules.runs.service import emit, get

CONTAINERS = {"Card", "Column", "Row", "Details"}
PROPS = {
    "Text": {"text"},
    "Heading": {"text", "level"},
    **{kind: {"title"} for kind in CONTAINERS},
    "List": {"items"},
    "Table": {"columns", "rows"},
    "Chart": {"kind", "x_label", "y_label", "unit", "points"},
    "FileLink": {"artifact_id", "label"},
}


def text(value):
    return isinstance(value, str) and len(value) <= 100_000


def scalar(value):
    return value is None or text(value) or type(value) in {int, float} and math.isfinite(value)


def check_components(document):
    nodes = document["components"]
    mapping = {}
    for node in nodes:
        if not isinstance(node, dict) or set(node) != {"id", "type", "props", "children"}:
            raise ValueError("组件字段无效")
        identifier, kind, props, children = (
            node["id"],
            node["type"],
            node["props"],
            node["children"],
        )
        if (
            not isinstance(identifier, str)
            or not 1 <= len(identifier) <= 256
            or identifier in mapping
        ):
            raise ValueError("组件ID重复或无效")
        if kind not in PROPS or not isinstance(props, dict) or set(props) - PROPS[kind]:
            raise ValueError("组件类型或参数不在可信目录")
        if (
            not isinstance(children, list)
            or len(children) > 64
            or len(set(children)) != len(children)
        ):
            raise ValueError("子组件引用无效")
        if (
            any(not isinstance(child, str) for child in children)
            or kind not in CONTAINERS
            and children
        ):
            raise ValueError("只有容器可持有子节点")
        valid = True
        if kind == "Text":
            valid = text(props.get("text"))
        elif kind == "Heading":
            valid = text(props.get("text")) and props.get("level") in {2, 3}
        elif kind in CONTAINERS:
            valid = "title" not in props or text(props["title"])
        elif kind == "List":
            valid = isinstance(props.get("items"), list) and len(props["items"]) <= 1000
            valid = valid and all(text(item) for item in props["items"])
        elif kind == "Table":
            columns, rows = props.get("columns"), props.get("rows")
            valid = (
                isinstance(columns, list)
                and 1 <= len(columns) <= 50
                and all(text(x) for x in columns)
            )
            valid = valid and isinstance(rows, list) and len(rows) <= 1000
            valid = valid and all(
                isinstance(row, list)
                and len(row) == len(columns)
                and all(scalar(cell) for cell in row)
                for row in rows
            )
        elif kind == "Chart":
            points = props.get("points")
            valid = props.get("kind") in {"bar", "line"} and all(
                text(props.get(k)) for k in ("x_label", "y_label", "unit")
            )
            valid = valid and isinstance(points, list) and len(points) <= 2000
            valid = valid and all(
                isinstance(p, dict)
                and set(p) == {"label", "value"}
                and text(p["label"])
                and (
                    p["value"] is None
                    or type(p["value"]) in {int, float}
                    and math.isfinite(p["value"])
                )
                for p in points
            )
        elif kind == "FileLink":
            valid = text(props.get("label")) and props.get("artifact_id") in {
                item["artifact_id"] for item in document["sources"]
            }
        if not valid:
            raise ValueError(f"{kind}参数无效或超过预算")
        mapping[identifier] = node
    reached = set()
    parents = {}
    for node in nodes:
        for child in node["children"]:
            parents[child] = parents.get(child, 0) + 1
            if parents[child] > 1 or child == document["root_id"]:
                raise ValueError("每个组件只能有一个父节点，根节点不可被引用")

    def walk(identifier, path):
        if identifier in path or len(path) >= 8 or identifier not in mapping:
            raise ValueError("组件引用缺失、循环或超过深度8")
        reached.add(identifier)
        for child in mapping[identifier]["children"]:
            walk(child, path | {identifier})

    walk(document["root_id"], set())
    if reached != set(mapping):
        raise ValueError("存在根节点不可到达的组件")


def pointer(value, path):
    if not isinstance(path, str) or len(path) > 1000 or path and not path.startswith("/"):
        raise ValueError("数据路径必须是JSON Pointer")
    for key in path.split("/")[1:] if path else []:
        key = key.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def bind_data(document, bindings, source_data):
    for node in document["components"]:
        if node["type"] not in {"Table", "Chart"}:
            if node["id"] in bindings:
                raise ValueError("只有表格或图表接受数据绑定")
            continue
        binding = bindings.get(node["id"])
        if not binding or binding.get("artifact_id") not in source_data:
            raise ValueError("数据组件需要实际来源绑定，不能接受模型填写的数据行")
        records = pointer(source_data[binding["artifact_id"]], binding.get("pointer", ""))
        if not isinstance(records, list):
            raise ValueError("绑定路径不是实际数据列表")
        if len(records) > (1000 if node["type"] == "Table" else 2000):
            raise ValueError("实际数据超过组件预算，请先选取来源片段")
        props = node["props"]
        if node["type"] == "Table":
            if props.get("rows"):
                raise ValueError("请将rows留空，数据行由实际来源生成")
            fields = binding.get("fields")
            if not isinstance(fields, list) or len(fields) != len(props.get("columns", [])):
                raise ValueError("绑定字段需要与表格列一一对应")
            props["rows"] = [
                [
                    cell if scalar(cell) else json.dumps(cell, ensure_ascii=False)
                    for cell in (pointer(record, field) for field in fields)
                ]
                for record in records
            ]
        else:
            if props.get("points"):
                raise ValueError("请将points留空，图表数值由实际来源生成")
            props["points"] = [
                {
                    "label": str(pointer(record, binding["label_pointer"])),
                    "value": pointer(record, binding["value_pointer"]),
                }
                for record in records
            ]


def create(run_id, operation_id, values):
    draft = SurfaceDraft.model_validate(values)
    identifier = str(uuid5(UUID(operation_id), "ui-surface:1"))
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        assert_owned(db, run_id)
        existing = db.get(Artifact, identifier)
        if existing and existing.status == "available":
            return {
                "artifact_id": existing.id,
                "version": existing.content_hash,
                "protocol": "workbench-ui/1",
            }
        document = {
            "protocol": "workbench-ui/1",
            "catalog_version": "workbench-core/1",
            **draft.model_dump(mode="json", exclude={"bindings"}),
            "sources": [
                {"artifact_id": str(item.artifact_id), "version": item.expected_version}
                for item in draft.sources
            ],
            "artifact_id": identifier,
            "run_id": run_id,
        }
        sources = {}
        for reference in draft.sources:
            row = get_artifact(db, run.user_id, run.workspace_id, str(reference.artifact_id))
            path = storage_path(row.storage_path)
            if row.status != "available" or row.content_hash != reference.expected_version:
                raise AppError("ui_source_changed", "界面来源版本不可用，请重新读取。", 409)
            if row.run_id and not allowed_result(db, run, row.run_id):
                raise AppError("ui_source_transfer_denied", "此界面来源尚未授权给当前模型。", 403)
            if row.byte_size <= 1024 * 1024 and row.mime_type == "application/json":
                if file_digest(path) != row.content_hash:
                    raise AppError("ui_source_changed", "界面来源存储内容已经改变。", 409)
                try:
                    sources[row.id] = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, UnicodeError):
                    raise AppError(
                        "ui_source_invalid", "此来源不是有效UTF-8 JSON数据。", 422
                    ) from None
        try:
            bind_data(document, copy.deepcopy(draft.bindings), sources)
            check_components(document)
            encoded = json.dumps(document, ensure_ascii=False, allow_nan=False).encode()
        except (ValueError, TypeError, KeyError, IndexError):
            raise AppError(
                "ui_surface_invalid", "界面定义或真实数据绑定无效，请修正组件与来源。", 422
            ) from None
        if len(encoded) > 1024 * 1024:
            raise AppError("ui_surface_budget", "界面快照超过1 MiB预算，请缩小范围。", 422)
        digest = hashlib.sha256(encoded).hexdigest()
        if existing and existing.content_hash != digest:
            raise AppError("ui_surface_conflict", "同一次界面发布的内容已改变。", 409)
        if existing is None:
            existing = Artifact(
                id=identifier,
                user_id=run.user_id,
                workspace_id=run.workspace_id,
                run_id=run_id,
                display_name=f"{draft.title}.ui.json",
                mime_type="application/json",
                byte_size=len(encoded),
                content_hash=digest,
                kind="ui_surface",
                storage_path=f"workspaces/{run.workspace_id}/artifacts/{identifier}/surface.json",
                metadata_json={
                    "_pending_document": document,
                    "source_refs": document["sources"],
                    "catalog_version": "workbench-core/1",
                },
            )
            db.add(existing)
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        assert_owned(db, run_id)
        if run.cancel_requested_at:
            raise AppError(
                "ui_publish_cancelled", "任务已请求停止，未发布此界面。", 409, "cancelled"
            )
        row = db.get(Artifact, identifier)
        path = storage_path(row.storage_path)
        if path.exists() and file_digest(path) != row.content_hash:
            raise AppError("ui_surface_conflict", "界面存储与发布意图不同，请核对。", 409)
        if not path.exists():
            publish_bytes(
                path,
                json.dumps(
                    row.metadata_json["_pending_document"], ensure_ascii=False, allow_nan=False
                ).encode(),
            )
        row.status = "available"
        row.metadata_json = {
            key: value for key, value in row.metadata_json.items() if not key.startswith("_")
        }
        emit(
            db,
            run_id,
            "ui.surface_published",
            {"artifact_id": identifier, "version": row.content_hash},
        )
        return {
            "artifact_id": identifier,
            "version": row.content_hash,
            "protocol": "workbench-ui/1",
        }


def listing(db, user_id, run_id):
    run = get(db, user_id, run_id)
    result = []
    for row in db.scalars(
        select(Artifact)
        .where(
            Artifact.run_id == run.id,
            Artifact.user_id == user_id,
            Artifact.kind == "ui_surface",
            Artifact.status == "available",
        )
        .order_by(Artifact.created_at)
        .limit(50)
    ):
        path = storage_path(row.storage_path)
        if path.is_file() and path.stat().st_size <= 1024 * 1024:
            result.append(
                {
                    "artifact_id": row.id,
                    "version": row.content_hash,
                    "document": json.loads(path.read_text(encoding="utf-8")),
                }
            )
    return {"surfaces": result}
