from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from workbench.contracts.command_limits import MAX_COMMAND_TIMEOUT_SECONDS
from workbench.modules.diagnostics.dto import Storage


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Argument = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
Argv = Annotated[list[Argument], Field(min_length=1, max_length=32)]
CommandName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")]


class ProjectDefaults(DTO):
    # Intentionally excludes model, approvals, sandbox and OS/network permissions.
    command_timeout_seconds: int | None = Field(default=None, ge=1, le=MAX_COMMAND_TIMEOUT_SECONDS)
    checkpoint_rounds: int | None = Field(default=None, ge=1, le=128)
    model_retry_limit: int | None = Field(default=None, ge=0, le=2)
    retry_backoff_seconds: int | None = Field(default=None, ge=1, le=30)
    context_target_ratio: float | None = Field(default=None, ge=0.5, le=0.95, allow_inf_nan=False)


class ProjectConfiguration(DTO):
    version: Literal[1]
    interpreter: Argv | None = None
    commands: dict[CommandName, Argv] = Field(default_factory=dict, max_length=16)
    defaults: ProjectDefaults = Field(default_factory=ProjectDefaults)

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value

    @field_validator("interpreter", "commands")
    @classmethod
    def declarative_arguments(cls, value):
        arrays = value.values() if isinstance(value, dict) else [value] if value else []
        secret_flags = {
            "--token",
            "--api-key",
            "--api_key",
            "--password",
            "--secret",
            "--authorization",
            "api_key",
            "token",
            "password",
            "secret",
        }
        for argv in arrays:
            for part in argv:
                if any(ord(character) < 32 for character in part):
                    raise ValueError("command arguments cannot contain control characters")
                if part.casefold().split("=", 1)[0] in secret_flags:
                    raise ValueError("credentials cannot be stored in project configuration")
        return value


class ProjectConfigurationView(DTO):
    path: str
    status: Literal["missing", "valid", "invalid", "unavailable"]
    message: str
    configuration: ProjectConfiguration | None = None
    effective_at: Literal["new_runs"] = "new_runs"
    maximum_bytes: Literal[65536] = 65536


class DataDirectoryView(DTO):
    root_path: str
    source: Literal["environment_override", "platform_default"]
    global_rules_path: str
    extensions_path: str
    desktop_open_available: Literal[False] = False
    conversation_source: Literal["postgresql"] = "postgresql"
    storage: Storage
