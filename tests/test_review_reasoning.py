"""Parameter/compatibility checks, separate from real local-model acceptance."""

from copy import deepcopy
from types import SimpleNamespace

from workbench.contracts.requests import RunOptionOverrides, RunOptions
from workbench.modules.models.engine_adapters import (
    request_options,
    review_reasoning_overrides,
)


def configuration():
    return {
        "provider_type": "openai_compatible",
        "endpoint": "http://127.0.0.1:30000/v1",
        "model_identifier": "Qwen3.8-Flash-Next",
        "generation": {
            "engine_kind": "sglang",
            "thinking_mode": "enabled",
            "reasoning_effort": "medium",
        },
    }


def test_new_task_review_default_is_low_workspace_can_inherit():
    assert RunOptions().review_reasoning_effort == "low"
    assert RunOptionOverrides().review_reasoning_effort is None


def test_review_low_is_independent_of_main_connection_and_task_choice():
    config = configuration()
    config["generation"]["reasoning_effort"] = "xhigh"
    original = deepcopy(config)
    overrides = review_reasoning_overrides(config, "low")
    assert config == original
    parsed = request_options(
        SimpleNamespace(
            **{key: value for key, value in config.items() if key != "generation"},
            **{**config["generation"], **overrides},
        )
    )
    assert parsed["reasoning_effort"] == "low"
    assert parsed["chat_template_kwargs"]["reasoning_effort"] == "low"


def test_existing_task_without_review_choice_keeps_frozen_generation():
    config = configuration()
    assert review_reasoning_overrides(config) == {}
    assert review_reasoning_overrides(config, "default") == {}


def test_disabled_review_clears_inherited_effort_and_sends_only_thinking_switch():
    config = configuration()
    original = deepcopy(config)
    overrides = review_reasoning_overrides(config, "disabled")
    assert overrides == {"thinking_mode": "disabled", "reasoning_effort": None}
    assert config == original
    parsed = request_options(
        SimpleNamespace(
            **{key: value for key, value in config.items() if key != "generation"},
            **{**config["generation"], **overrides},
        )
    )
    assert parsed == {"chat_template_kwargs": {"enable_thinking": False}}


def test_unconfirmed_provider_never_receives_a_guessed_effort_parameter():
    config = configuration()
    config["provider_type"] = "anthropic"
    config["model_identifier"] = "claude"
    assert review_reasoning_overrides(config, "low") == {}
    assert review_reasoning_overrides(config, "disabled") == {}
