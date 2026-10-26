"""Auxiliary checks use existing deployment metadata and actual saved loading logs."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/services"))
from launcher_config import load_profile  # noqa: E402
from launcher_progress import ModelLogProgress  # noqa: E402


def test_real_loading_log_distinguishes_main_and_mtp_without_claiming_ready():
    paths = sorted((ROOT / ".local/model/evidence").glob("*/server.log"))
    source = next(
        (
            path
            for path in reversed(paths)
            if path.stat().st_size and "Qwen4ExpForCausalLMMTP" in path.read_text(errors="replace")
        ),
        None,
    )
    if source is None:
        pytest.skip("Actual MTP loading evidence is not included in source distributions")
    progress = ModelLogProgress()
    while progress.position < source.stat().st_size:
        detail = progress.read(source.parent)
    assert progress.loads >= 2
    assert "初始化" in detail
    assert "就绪" not in detail


def test_explicit_instance_does_not_inherit_main_profile(monkeypatch):
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(ROOT / ".local/acceptance/data"))
    assert load_profile() == {}
