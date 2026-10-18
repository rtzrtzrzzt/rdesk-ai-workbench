"""Auxiliary checks on existing project folders; live selection is verified separately."""

from pathlib import Path

import pytest
from workbench.config import PROJECT_ROOT
from workbench.contracts.errors import AppError
from workbench.modules.workspace_directories import DirectoryListing, browse


def test_existing_folders_are_canonical_and_files_are_not_listed():
    listed = DirectoryListing.model_validate(browse(str(PROJECT_ROOT)))
    assert listed.path == str(PROJECT_ROOT.resolve())
    assert listed.parent_path == str(PROJECT_ROOT.parent.resolve())
    assert "apps" in {entry.name for entry in listed.entries}
    assert "README.md" not in {entry.name for entry in listed.entries}
    assert all(Path(entry.path).is_dir() for entry in listed.entries)
    assert all(not entry.name.startswith(".") for entry in listed.entries)
    assert ".git" in {
        entry["name"] for entry in browse(str(PROJECT_ROOT), show_hidden=True)["entries"]
    }


def test_home_and_filesystem_root_can_be_selected():
    assert browse()["path"] == str(Path.home().resolve())
    assert browse(Path.home().anchor)["parent_path"] is None


@pytest.mark.parametrize("path", ["relative-folder", str(PROJECT_ROOT / "README.md"), "\0"])
def test_invalid_and_non_directory_paths_return_actionable_errors(path):
    with pytest.raises(AppError) as failure:
        browse(path)
    assert failure.value.status == 422
