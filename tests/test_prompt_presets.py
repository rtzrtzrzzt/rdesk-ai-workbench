"""Wire and configuration checks; no live inference or product acceptance claims."""

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError
from workbench.modules.models.engine_adapters import reasoning_choices, reasoning_overrides
from workbench.modules.models.managed import default_connection
from workbench.modules.models.profile import Generation
from workbench.modules.models.prompt_templates import PromptTemplate, instructions
from workbench.modules.models.providers import ProviderConfig, _request
from workbench.modules.tools.registry import definitions


def configuration(template):
    managed = default_connection()
    return {
        **managed,
        "generation": Generation(engine_kind="sglang", prompt_template=template).model_dump(),
    }


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_workbench_efforts_keep_tool_protocol_and_do_not_send_unsupported_qwen_values(effort):
    frozen = configuration(PromptTemplate(mode="workbench"))
    override = reasoning_overrides(frozen, effort)
    config = ProviderConfig(
        provider_type=frozen["provider_type"],
        endpoint=frozen["endpoint"],
        model_identifier=frozen["model_identifier"],
        engine_kind="sglang",
        thinking_mode=override["thinking_mode"],
        reasoning_effort=override["reasoning_effort"],
        prompt_template=frozen["generation"]["prompt_template"],
    )
    tools = definitions(["read_rules", "read_skill"])
    system = instructions(frozen, override)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "读取规则。"}]
    body = json.loads(_request(config, messages, tools)[3])
    baseline = json.loads(
        _request(replace(config, prompt_template=None, reasoning_effort="medium"), messages, tools)[
            3
        ]
    )
    assert body["tools"] == baseline["tools"]
    assert body["messages"] == baseline["messages"]
    assert body["reasoning_effort"] == "medium"
    assert system and frozen["generation"]["reasoning_effort"] is None


def test_custom_choices_and_prompt_come_from_the_saved_template():
    template = PromptTemplate(mode="custom", name="我的模板", instructions="交付前核对关键依据。")
    frozen = configuration(template)
    choices = reasoning_choices(frozen)
    assert [item["value"] for item in choices if item["kind"] == "custom"] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert "交付前核对关键依据。" in instructions(frozen, {"reasoning_effort": "max"})
    assert "Max" in instructions(frozen, {"reasoning_effort": "max"})
    assert "思考方式预设" not in instructions(frozen, {"thinking_mode": "disabled"})


def test_invalid_custom_default_is_rejected_before_saving():
    with pytest.raises(ValidationError, match="默认档位"):
        PromptTemplate(mode="custom", default_effort="unlisted")


def test_parent_disabled_choice_can_be_carried_without_a_reasoning_value():
    frozen = configuration(PromptTemplate(mode="workbench"))
    assert reasoning_overrides(frozen, "disabled") == {
        "thinking_mode": "disabled",
        "reasoning_effort": None,
    }
