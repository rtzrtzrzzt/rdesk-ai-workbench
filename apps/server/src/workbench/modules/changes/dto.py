from typing import Literal

from pydantic import Field

from workbench.contracts.base import PublicResponse

Coverage = Literal["pending", "complete", "partial", "unknown"]


class ChangedFile(PublicResponse):
    id: str
    path: str
    kind: Literal["added", "modified", "deleted"]
    added_lines: int | None = None
    removed_lines: int | None = None
    before_version: str | None = None
    after_version: str | None = None
    attribution: str


class ChangeTotals(PublicResponse):
    files: int
    added_lines: int | None = None
    removed_lines: int | None = None


class ChangeSetView(PublicResponse):
    revision: str
    coverage: Coverage
    gaps: list[str] = Field(default_factory=list)
    files: list[ChangedFile] = Field(default_factory=list)
    totals: ChangeTotals


class ChangeDetails(PublicResponse):
    path: str
    diff: str | None = None
    truncated: bool = False
    coverage: Coverage
