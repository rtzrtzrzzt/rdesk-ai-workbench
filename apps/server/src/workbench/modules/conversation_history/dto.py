from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationHistorySave(DTO):
    scope: Literal["disabled", "workspace", "user"]
    expected_version: int = Field(ge=0)
    request_id: UUID


class ConversationHistoryView(DTO):
    scope: Literal["disabled", "workspace", "user"] = "workspace"
    version: int
    initialized: bool
    child_scope: Literal["task_tree"] = "task_tree"


class ListConversations(DTO):
    scope: Literal["current", "all"] = "current"
    query: str = Field(default="", max_length=200, description="标题关键词，按文字匹配。")
    archived: Literal["exclude", "include", "only"] = "exclude"
    include_subagents: bool = False
    created_after: datetime | None = None
    created_before: datetime | None = None
    before: UUID | None = None
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("created_after", "created_before")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("时间需包含时区，例如2026-10-26T00:00:00+08:00。")
        return value

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.created_after and self.created_before and self.created_after >= self.created_before:
            raise ValueError("created_after必须早于created_before。")
        return self


class SearchConversations(ListConversations):
    query: str = Field(min_length=1, max_length=200)
    search_in: Literal["title", "messages", "all"] = "all"
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def meaningful_query(cls, value):
        if not value.strip():
            raise ValueError("关键词不能为空白。")
        return value.strip()


class ReadConversation(DTO):
    thread_id: UUID
    message_id: UUID | None = None
    after_message_id: UUID | None = None
    start_character: int = Field(default=0, ge=0)
    max_chars: int = Field(
        default=8000, ge=128, le=65536, description="本次所有消息正文的总字符上限。"
    )
    limit: int = Field(default=5, ge=1, le=20)
    include_tools: bool = False

    @model_validator(mode="after")
    def cursor_shape(self):
        if self.message_id and self.after_message_id:
            raise ValueError("指定message_id续读或after_message_id翻页，不能同时使用。")
        if self.start_character and not self.message_id:
            raise ValueError("字符续读需要明确message_id。")
        return self
