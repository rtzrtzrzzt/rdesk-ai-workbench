"""Permission contract checks; real browser/desktop acceptance is separate."""

import pytest
from pydantic import ValidationError
from workbench.contracts.errors import AppError
from workbench.modules.automation.dto import AutomationConfiguration
from workbench.modules.automation.service import automatic_configuration
from workbench.modules.browser.policy import NetworkPolicy


def test_global_switch_scope_does_not_grant_remote_transfers_or_local_origins():
    config = automatic_configuration({})
    assert config.desktop_all_windows and config.desktop_control and config.desktop_capture
    assert config.browser_public_network and config.browser_enabled
    assert config.remote_model_revisions == []
    assert config.browser_local_origins == []
    assert config.target_window_id is None and config.target_window_pid is None


def test_fixed_window_authority_still_requires_target_and_takeover():
    with pytest.raises(ValidationError):
        AutomationConfiguration(desktop_capture=True, desktop_control=True)
    with pytest.raises(ValidationError):
        AutomationConfiguration(
            desktop_capture=True,
            desktop_control=True,
            desktop_all_windows=True,
        )


def test_public_browser_mode_accepts_public_sites_without_a_fixed_list():
    policy = NetworkPolicy([], [], set(), allow_public=True)
    assert str(policy.validate("https://huggingface.co/Qwen")) == "https://huggingface.co/Qwen"
    assert policy.validate("https://qwen.ai:8443/").port == 8443


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8766/api/v1/workspaces",
        "http://localhost/",
        "http://[::1]/",
        "http://192.168.1.1/",
        "http://169.254.169.254/",
        "file:///etc/passwd",
        "https://user:password@qwen.ai/",
    ],
)
def test_public_browser_mode_keeps_protected_targets_blocked(url):
    with pytest.raises(AppError):
        NetworkPolicy([], [], set(), allow_public=True).validate(url)


def test_legacy_exact_origin_mode_is_not_implicitly_broadened():
    policy = NetworkPolicy(["https://qwen.ai"], [], set())
    with pytest.raises(AppError):
        policy.validate("https://huggingface.co/Qwen")


def test_explicit_local_origin_still_cannot_access_workbench_api():
    local = "http://127.0.0.1:8766"
    policy = NetworkPolicy([], [local], {local}, allow_public=True)
    with pytest.raises(AppError):
        policy.validate(local + "/%61pi/v1/workspaces")
