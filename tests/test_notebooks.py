"""Real temporary notebook JSON files; no kernel execution or product acceptance."""

import copy
import hashlib
import json

import pytest
from workbench.contracts.errors import AppError
from workbench.modules.tools import filesystem, notebooks


@pytest.fixture
def notebook(tmp_path):
    document = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"custom": {"keep": True}},
        "cells": [
            {
                "cell_type": "code",
                "id": "original-code",
                "metadata": {"tags": ["retain"]},
                "source": ["value = 1\n", "print(value)"],
                "execution_count": 3,
                "outputs": [{"output_type": "stream", "name": "stdout", "text": ["1\n"]}],
            },
            {
                "cell_type": "markdown",
                "id": "original-markdown",
                "metadata": {},
                "source": "# Notes",
            },
        ],
    }
    path = tmp_path / "analysis.ipynb"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, document


def test_read_version_outputs_and_zero_based_pagination(notebook):
    path, document = notebook
    result = notebooks.read_notebook(path.parent, {"path": path.name, "end_cell": 0})
    assert result["version"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["cells"][0]["source"] == "".join(document["cells"][0]["source"])
    assert "outputs" not in result["cells"][0]
    assert result["next_cell"] == 1 and result["truncated"]
    result = notebooks.read_notebook(path.parent, {"path": path.name, "include_outputs": True})
    assert result["cells"][0]["outputs"] == document["cells"][0]["outputs"]


def test_source_edit_preserves_unrelated_content_and_requires_current_version(notebook):
    path, original = notebook
    version = hashlib.sha256(path.read_bytes()).hexdigest()
    result = notebooks.edit_notebook(
        path.parent,
        {
            "path": path.name,
            "expected_version": version,
            "operations": [{"action": "replace", "index": 0, "source": "value = 2\n"}],
        },
    )
    expected = copy.deepcopy(original)
    expected["cells"][0]["source"] = ["value = 2\n"]
    assert json.loads(path.read_text()) == expected
    assert result["executed"] is False and result["previous_version"] == version
    before = path.read_bytes()
    with pytest.raises(AppError, match="已改变"):
        notebooks.edit_notebook(
            path.parent,
            {
                "path": path.name,
                "expected_version": version,
                "operations": [{"action": "delete", "index": 0}],
            },
        )
    assert path.read_bytes() == before


def test_ordered_insert_move_delete_and_batch_failure_is_atomic(notebook):
    path, original = notebook
    version = hashlib.sha256(path.read_bytes()).hexdigest()
    base = {"path": path.name, "expected_version": version}
    with pytest.raises(AppError):
        notebooks.edit_notebook(
            path.parent,
            {
                **base,
                "operations": [
                    {"action": "delete", "index": 0},
                    {"action": "delete", "index": 2},
                ],
            },
        )
    assert json.loads(path.read_text()) == original
    notebooks.edit_notebook(
        path.parent,
        {
            **base,
            "operations": [
                {"action": "insert", "index": 2, "source": "answer = 42"},
                {"action": "move", "index": 2, "destination": 0},
                {"action": "delete", "index": 2},
            ],
        },
    )
    cells = json.loads(path.read_text())["cells"]
    assert cells[0]["source"] == ["answer = 42"]
    assert cells[0]["execution_count"] is None and cells[0]["outputs"] == []
    assert cells[1] == original["cells"][0]


def test_paths_symlinks_and_output_budget(notebook, tmp_path):
    path, document = notebook
    with pytest.raises(AppError) as escaped:
        notebooks.read_notebook(tmp_path, {"path": "../outside.ipynb"})
    assert escaped.value.code == "path_not_allowed"
    link = tmp_path / "linked.ipynb"
    link.symlink_to(path)
    with pytest.raises(AppError) as denied:
        notebooks.edit_notebook(
            tmp_path,
            {
                "path": link.name,
                "expected_version": hashlib.sha256(path.read_bytes()).hexdigest(),
                "operations": [{"action": "replace", "index": 0, "source": "new"}],
            },
        )
    assert denied.value.code == "symlink_write_denied"
    document["cells"][0]["source"] = "大\n" * 50000
    path.write_text(json.dumps(document), encoding="utf-8")
    result = notebooks.read_notebook(tmp_path, {"path": path.name, "include_outputs": True})
    assert result["truncated"] and result["cells"][0]["source_truncated"]
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= filesystem.MAX_OUTPUT_BYTES
