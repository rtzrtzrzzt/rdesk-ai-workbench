"""Stable HTTP/tool contracts. Nullable response fields remain required in OpenAPI."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Scope = Literal["user", "workspace"]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchError(DTO):
    code: str
    message: str
    retryable: bool


class SearchEngineCatalogQuery(DTO):
    scope: Scope | None = None
    endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    timeout_seconds: int = Field(default=20, ge=3, le=60)


class SearchEngine(DTO):
    name: str
    categories: list[str]
    default_enabled: bool


class SearchEngineCatalog(DTO):
    endpoint: str
    fetched_at: datetime
    engines: list[SearchEngine]


class SearchConnectionSave(DTO):
    scope: Scope
    name: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(min_length=1, max_length=2048)
    enabled: bool = True
    engines: list[str] = Field(default_factory=list, max_length=10)
    language: str = Field(
        default="auto", min_length=2, max_length=20, pattern=r"^[A-Za-z][A-Za-z0-9-]*$"
    )
    region: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    max_results: int = Field(default=5, ge=1, le=20)
    timeout_seconds: int = Field(default=20, ge=3, le=60)
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("engines")
    @classmethod
    def engine_names(cls, value: list[str]) -> list[str]:
        import re

        if any(not re.fullmatch(r"[A-Za-z0-9 _.-]{1,80}", item) for item in value):
            raise ValueError("引擎名称含不支持的字符。")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def locale_consistency(self):
        if self.region:
            if self.language in {"auto", "all"}:
                raise ValueError("指定地区时请选择具体语言，例如 zh 和 CN。")
            if "-" in self.language and self.language.rsplit("-", 1)[1].upper() != self.region:
                raise ValueError("语言代码与地区不一致。")
        return self


class SearchConnectionView(DTO):
    id: str
    provider_type: Literal["searxng"]
    scope: Scope
    workspace_id: str | None
    name: str
    endpoint: str
    enabled: bool
    engines: list[str]
    language: str
    region: str | None
    max_results: int
    timeout_seconds: int
    version: int
    status: Literal["unchecked", "ready", "unavailable"]
    checked_at: datetime | None
    last_error: SearchError | None


class SearchCapability(DTO):
    status: Literal["not_configured", "unavailable", "ready"]
    connection: SearchConnectionView | None
    error: SearchError | None


class SearchQuery(DTO):
    query: str = Field(min_length=1, max_length=500)
    max_results: int | None = Field(default=None, ge=1, le=20)
    time_range: Literal["day", "month", "year"] | None = None
    domains: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("query")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入搜索词。")
        # SearXNG's !/: query syntax can override engines/language configured by the user.
        if any(part.startswith(("!", ":")) for part in value.split()):
            raise ValueError("请通过连接设置选择引擎与语言，不在搜索词中覆盖。")
        return value

    @field_validator("domains")
    @classmethod
    def domain_names(cls, value: list[str]) -> list[str]:
        import re

        result = []
        for item in value:
            item = item.rstrip(".").encode("idna").decode("ascii").lower()
            if len(item) > 253 or not re.fullmatch(
                r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}", item
            ):
                raise ValueError("域名筛选仅接受域名，不接受 URL 或路径。")
            result.append(item)
        return list(dict.fromkeys(result))


class SearchSource(DTO):
    url: str
    title: str
    snippet: str
    fetched_at: datetime
    engines: list[str]


class SearchResult(DTO):
    connection_id: str
    query: str
    fetched_at: datetime
    results: list[SearchSource]
    warnings: list[str]
    truncated: bool


class FetchPage(DTO):
    url: str = Field(min_length=1, max_length=4096)


class WebPage(DTO):
    requested_url: str
    url: str
    title: str
    snippet: str
    text: str
    fetched_at: datetime
    content_type: str
    http_status: int
    truncated: bool
    extraction: Literal["html_text", "plain_text"]
