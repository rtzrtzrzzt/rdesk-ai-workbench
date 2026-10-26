"""Configuration and discovery contracts; real admission is verified in the workbench."""

import pytest
from pydantic import ValidationError
from workbench.config import Settings
from workbench.modules.configuration import catalog
from workbench.modules.tools.registry import TOOLS, definitions


def test_capacity_is_installation_configuration(monkeypatch):
    for suffix in ("PER_RUN", "TOTAL"):
        monkeypatch.delenv(f"WORKBENCH_COMMAND_MAX_ACTIVE_{suffix}", raising=False)
    settings = Settings()
    assert (settings.command_max_active_per_run, settings.command_max_active_total) == (2, 4)
    monkeypatch.setenv("WORKBENCH_COMMAND_MAX_ACTIVE_PER_RUN", "3")
    monkeypatch.setenv("WORKBENCH_COMMAND_MAX_ACTIVE_TOTAL", "6")
    configured = Settings()
    assert (configured.command_max_active_per_run, configured.command_max_active_total) == (3, 6)
    section = catalog.describe("application")
    assert section["scopes"] == ("application",)
    assert section["effective_at"] == "restart"
    assert "application" not in catalog.actions()


@pytest.mark.parametrize("per_run,total", [(0, 4), (2, 0), (33, 33), (3, 2)])
def test_invalid_capacity_is_rejected_before_startup(per_run, total):
    with pytest.raises(ValidationError):
        Settings(command_max_active_per_run=per_run, command_max_active_total=total)


def test_active_command_query_is_discoverable_without_approval_or_caller_scope():
    tool = TOOLS["list_commands"]
    assert not tool.requires_approval
    assert tool.module == "processes" and tool.replay_policy == "idempotent"
    schema = definitions(["list_commands"])[0]["function"]["parameters"]
    assert not schema["properties"]
    assert schema["additionalProperties"] is False
    assert "每任务一个/本机两个" not in TOOLS["start_command"].description
