from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from workbench.modules.preferences.configuration import LanguageServer


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscoverSettings(DTO):
    query: str = Field(default="", max_length=200)
    include_schemas: bool = False


class ReadSettings(DTO):
    section: str = Field(min_length=1, max_length=80)
    resource_id: UUID | None = None
    scope: Literal["user", "workspace", "application", "task"] | None = None
    include_schema: bool = True


class SettingsChange(ReadSettings):
    action: str = Field(default="save", min_length=1, max_length=40)
    values: dict = Field(default_factory=dict)
    include_schema: bool = False

    @model_validator(mode="after")
    def credential_boundary(self):
        import json

        from .catalog import SECRET_FIELDS

        if len(json.dumps(self.values, ensure_ascii=False).encode()) > 262144:
            raise ValueError("单次配置候选不得超过256KiB。")

        def check(value):
            if isinstance(value, dict):
                if any(key.casefold() in SECRET_FIELDS for key in value):
                    raise ValueError("凭据正文不得通过普通配置工具提交，请使用用户保险库入口。")
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)
                if any(
                    isinstance(item, str)
                    and item.casefold().split("=", 1)[0]
                    in {
                        "--token",
                        "--api-key",
                        "--api_key",
                        "--password",
                        "--secret",
                        "--authorization",
                    }
                    for item in value
                ):
                    raise ValueError("命令参数中的凭据应改用受管引用。")

        check(self.values)
        return self


class ApplySettings(SettingsChange):
    request_id: UUID


class LanguageServerChange(LanguageServer):
    command: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        description="受信可执行文件及参数；已有同language条目可省略以保留隐藏的原命令，新条目必须提供。",
    )

    @field_validator("command")
    @classmethod
    def command_parts(cls, value):
        return None if value is None else super().command_parts(value)


class DeveloperSave(DTO):
    expected_version: int = Field(ge=0)
    language_servers: list[LanguageServerChange] | None = Field(default=None, max_length=8)


class SettingsReceipt(DTO):
    request_id: UUID
