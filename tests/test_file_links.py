"""File-link boundaries using real repository document bytes, with no product records."""

import hashlib
import os
import shutil
from uuid import uuid4

import pytest
from pydantic import ValidationError
from workbench.config import PROJECT_ROOT
from workbench.contracts.errors import AppError
from workbench.modules.files import dto, links
from workbench.modules.files.domain import Artifact
from workbench.modules.tools.filesystem import authorized_path


@pytest.mark.parametrize("values", [{}, {"path": "README.md", "artifact_id": str(uuid4())}])
def test_present_file_requires_exactly_one_source(values):
    with pytest.raises(ValidationError):
        dto.PresentFile.model_validate(values)


def test_present_file_accepts_real_source_version():
    version = hashlib.sha256((PROJECT_ROOT / "README.md").read_bytes()).hexdigest()
    assert dto.PresentFile(path="README.md", version=version).version == version
    with pytest.raises(ValidationError):
        dto.PresentFile(path="README.md", version="latest")


def test_snapshot_contains_actual_immutable_document_bytes(tmp_path):
    # A disposable source copy permits removing the source without touching user files.
    source = tmp_path / "source" / "README.md"
    source.parent.mkdir()
    shutil.copyfile(PROJECT_ROOT / "README.md", source)
    original = source.read_bytes()
    staged, digest, size = links._stage_snapshot(tmp_path, source, tmp_path / "published")
    assert staged.read_bytes() == original
    assert digest == hashlib.sha256(original).hexdigest()
    assert size == len(original)
    source.unlink()
    assert staged.read_bytes() == original


def test_snapshot_refuses_a_directory(tmp_path):
    with pytest.raises(AppError) as failure:
        links._stage_snapshot(tmp_path, tmp_path, tmp_path / "published")
    assert failure.value.code in {"file_not_regular", "file_unavailable"}
    assert not list((tmp_path / "published").iterdir())


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="Platform does not expose O_NOFOLLOW")
def test_snapshot_refuses_swapped_final_symlink(tmp_path):
    source = tmp_path / "README.md"
    source.symlink_to(PROJECT_ROOT / "README.md")
    with pytest.raises(AppError) as failure:
        links._stage_snapshot(tmp_path, source, tmp_path / "published")
    assert failure.value.code == "file_unavailable"
    assert not list((tmp_path / "published").iterdir())


@pytest.mark.parametrize("path", [".local", ".runtime", "models", "../README.md"])
def test_presentation_preserves_existing_path_boundaries(path):
    with pytest.raises(AppError) as failure:
        authorized_path(PROJECT_ROOT, path)
    assert failure.value.status == 403


def test_original_png_does_not_become_trusted_inline_preview():
    row = Artifact(kind="workspace_snapshot", mime_type="image/png")
    assert not links.safe_inline_image(row)
    row.kind = "preview"
    assert links.safe_inline_image(row)
    row.kind = "frame_0001"
    assert links.safe_inline_image(row)
    row.mime_type = "image/svg+xml"
    assert not links.safe_inline_image(row)


def test_markdown_escapes_filename_link_delimiters():
    assert links._markdown("报告[核对]\\原件\n.txt", "/managed") == (
        "[报告\\[核对\\]\\\\原件 .txt](/managed)"
    )
