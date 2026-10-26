"""Editable behavior presets, independent from a model's message serialization."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EffortPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    label: str = Field(min_length=1, max_length=40)
    instructions: str = Field(min_length=1, max_length=12000)


def five_presets() -> list[EffortPreset]:
    return [
        EffortPreset(id="low", label="低", instructions="保持分析简短、聚焦，直接解决问题。"),
        EffortPreset(
            id="medium", label="中", instructions="正常分析，检查完成任务必需的关键条件。"
        ),
        EffortPreset(
            id="high", label="高", instructions="深入分析问题，核验关键假设和可能影响结论的条件。"
        ),
        EffortPreset(
            id="xhigh", label="极高", instructions="充分分析，比较可行方案，检查边界条件和反例。"
        ),
        EffortPreset(
            id="max",
            label="Max",
            instructions="充分分析复杂问题，核查关键依据，在交付前独立复核结论和必要完成条件。",
        ),
    ]


class PromptTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["native", "workbench", "custom"] = "native"
    name: str = Field(default="我的模板", min_length=1, max_length=80)
    instructions: str = Field(default="", max_length=60000)
    default_effort: str = Field(default="medium", pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    presets: list[EffortPreset] = Field(default_factory=five_presets, min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_presets(self):
        identifiers = [item.id for item in self.presets]
        if len(set(identifiers)) != len(identifiers) or "disabled" in identifiers:
            raise ValueError("档位标识必须唯一，disabled保留给真正关闭思考")
        if self.mode == "custom" and self.default_effort not in identifiers:
            raise ValueError("默认档位必须存在于自定义模板中")
        return self


def template_for(configuration: dict) -> PromptTemplate:
    return PromptTemplate.model_validate(
        configuration.get("generation", {}).get("prompt_template") or {}
    )


def choices(template: PromptTemplate) -> list[EffortPreset]:
    return five_presets() if template.mode == "workbench" else template.presets


def instructions(configuration: dict, overrides: dict | None = None) -> str:
    template = template_for(configuration)
    if template.mode == "native":
        return ""
    generation = {**configuration.get("generation", {}), **(overrides or {})}
    parts = [template.instructions] if template.mode == "custom" and template.instructions else []
    if generation.get("thinking_mode") != "disabled":
        selected = generation.get("reasoning_effort") or (
            "medium" if template.mode == "workbench" else template.default_effort
        )
        preset = next((item for item in choices(template) if item.id == selected), None)
        if preset is not None:
            parts.append(f"思考方式预设（{preset.label}）：{preset.instructions}")
    return "\n".join(parts)
