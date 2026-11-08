"""Bounded notebook JSON operations; callers own approval and the workspace file lock."""

import json
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from workbench.contracts.errors import AppError
from workbench.modules.tools import filesystem


class ReadNotebook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    start_cell: int = Field(default=0, ge=0)
    end_cell: int = Field(default=19, ge=0)
    include_outputs: bool = False


class NotebookOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["replace", "insert", "delete", "move"]
    index: int = Field(ge=0)
    source: str | None = None
    cell_type: Literal["code", "markdown", "raw"] | None = None
    destination: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def fields_for_action(self):
        if self.action in {"replace", "insert"} and self.source is None:
            raise ValueError("replace/insert require source")
        if self.action not in {"replace", "insert"} and self.source is not None:
            raise ValueError("source is only accepted by replace/insert")
        if (self.action == "move") != (self.destination is not None):
            raise ValueError("destination is required only by move")
        if self.cell_type is not None and self.action != "insert":
            raise ValueError("cell_type is only accepted by insert")
        return self


class EditNotebook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    expected_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    operations: list[NotebookOperation] = Field(min_length=1, max_length=100)


def _load(root: Path, value: str) -> tuple[Path, dict, str]:
    path = filesystem.authorized_path(root, value)
    if path.suffix.lower() != ".ipynb":
        raise AppError("invalid_notebook", "Notebook 工具仅处理 .ipynb 文件。")
    content, version = filesystem.read_text(path)
    try:
        notebook = json.loads(content)
    except (ValueError, RecursionError):
        raise AppError("invalid_notebook", "Notebook 不是有效的 JSON 文件。") from None
    if (
        not isinstance(notebook, dict)
        or notebook.get("nbformat") != 4
        or not isinstance(notebook.get("nbformat_minor"), int)
        or not isinstance(notebook.get("metadata"), dict)
        or not isinstance(notebook.get("cells"), list)
    ):
        raise AppError("invalid_notebook", "仅支持包含 cells 和 metadata 的 nbformat 4 文件。")
    for cell in notebook["cells"]:
        if not isinstance(cell, dict):
            raise AppError("invalid_notebook", "Notebook 单元格格式不正确。")
        source = cell.get("source")
        if (
            cell.get("cell_type") not in {"code", "markdown", "raw"}
            or not isinstance(cell.get("metadata"), dict)
            or "id" in cell
            and (not isinstance(cell["id"], str) or not 1 <= len(cell["id"]) <= 64)
            or not (
                isinstance(source, str)
                or isinstance(source, list)
                and all(isinstance(line, str) for line in source)
            )
            or cell["cell_type"] == "code"
            and not isinstance(cell.get("outputs"), list)
        ):
            raise AppError("invalid_notebook", "Notebook 单元格缺少有效的类型、内容或元数据。")
    return path, notebook, version


def _json_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def read_notebook(root: Path, values: dict) -> dict:
    root = root.resolve()
    options = ReadNotebook.model_validate(values)
    if options.end_cell < options.start_cell or options.end_cell - options.start_cell >= 100:
        raise AppError("invalid_cell_range", "每次读取不超过 100 个单元格，索引从 0 开始。")
    path, notebook, version = _load(root, options.path)
    result = {
        "path": filesystem.display_path(root, path),
        "version": version,
        "nbformat": notebook["nbformat"],
        "total_cells": len(notebook["cells"]),
        "cells": [],
        "truncated": False,
        "next_cell": None,
    }
    for index in range(options.start_cell, min(options.end_cell + 1, len(notebook["cells"]))):
        cell = notebook["cells"][index]
        item = {
            "index": index,
            "cell_type": cell["cell_type"],
            "source": "".join(cell["source"]),
        }
        if "id" in cell:
            item["id"] = cell["id"]
        if cell["cell_type"] == "code":
            item["execution_count"] = cell.get("execution_count")
            if options.include_outputs:
                item["outputs"] = cell["outputs"]
        remaining = filesystem.MAX_OUTPUT_BYTES - _json_size(result) - 256
        if _json_size(item) > remaining:
            if result["cells"]:
                result.update(truncated=True, next_cell=index)
                break
            # A large first cell must still reveal an excerpt rather than an empty page.
            item.pop("outputs", None)
            original = item["source"]
            item["source"] = original.encode("utf-8")[: max(0, remaining // 6)].decode(
                "utf-8", errors="ignore"
            )
            item["source_truncated"] = item["source"] != original
            if options.include_outputs and cell["cell_type"] == "code":
                item["outputs_truncated"] = True
            result["truncated"] = True
        result["cells"].append(item)
    else:
        next_cell = min(options.end_cell + 1, len(notebook["cells"]))
        if next_cell < len(notebook["cells"]):
            result.update(truncated=True, next_cell=next_cell)
    return result


def edit_notebook(root: Path, values: dict, **write_hooks) -> dict:
    """Apply operations in order; each index refers to the list after prior operations.

    Move destinations are final list indices. Source replacement deliberately preserves
    metadata, execution counts and outputs; this tool never executes notebook code.
    """
    root = root.resolve()
    options = EditNotebook.model_validate(values)
    _, notebook, version = _load(root, options.path)
    if version != options.expected_version:
        raise AppError("file_version_conflict", "Notebook 已改变，请重读后再编辑。", 409)
    cells = notebook["cells"]
    changes = []
    for operation in options.operations:
        maximum = len(cells) if operation.action == "insert" else len(cells) - 1
        if operation.index > maximum:
            raise AppError("invalid_cell_index", "单元格索引超出当前 Notebook 范围。", 409)
        if operation.action == "replace":
            cells[operation.index]["source"] = operation.source.splitlines(keepends=True)
        elif operation.action == "insert":
            cell = {
                "cell_type": operation.cell_type or "code",
                "id": uuid.uuid4().hex[:12],
                "metadata": {},
                "source": operation.source.splitlines(keepends=True),
            }
            if cell["cell_type"] == "code":
                cell.update(outputs=[], execution_count=None)
            cells.insert(operation.index, cell)
        elif operation.action == "delete":
            cells.pop(operation.index)
        else:
            if operation.destination >= len(cells):
                raise AppError("invalid_cell_index", "移动目标索引超出 Notebook 范围。", 409)
            cells.insert(operation.destination, cells.pop(operation.index))
        changes.append(operation.model_dump(exclude={"source"}, exclude_none=True))
    try:
        content = json.dumps(notebook, ensure_ascii=False, indent=1) + "\n"
    except (ValueError, RecursionError):
        raise AppError("invalid_notebook", "Notebook 嵌套结构超出处理限制。") from None
    result = filesystem.change_file(
        root,
        {"path": options.path, "expected_version": version, "content": content},
        patch=False,
        **write_hooks,
    )
    return {**result, "total_cells": len(cells), "changes": changes, "executed": False}
