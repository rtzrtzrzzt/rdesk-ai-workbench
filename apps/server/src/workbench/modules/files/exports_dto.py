"""Restricted export commands. Content is explicit data, never executable Python or macros."""

import json
import re
from pathlib import PurePath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    Field,
    JsonValue,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from workbench.contracts.requests import Command

TextCell = Annotated[str, StringConstraints(max_length=32767)]
Scalar = (
    TextCell
    | Annotated[int, Field(strict=True)]
    | Annotated[float, Field(strict=True, allow_inf_nan=False)]
    | StrictBool
    | None
)
Row = Annotated[list[Scalar], Field(max_length=100)]


class ArtifactReference(Command):
    artifact_id: UUID
    expected_version: str = Field(pattern=r"^[a-f0-9]{64}$")


class TableData(Command):
    rows: list[Annotated[list[TextCell], Field(min_length=1, max_length=30)]] = Field(
        min_length=1, max_length=200
    )

    @model_validator(mode="after")
    def rectangular(self):
        if any(len(row) != len(self.rows[0]) for row in self.rows):
            raise ValueError("文档表格每行列数必须一致。")
        return self


class TextExport(Command):
    format: Literal["txt", "md"]
    text: str = Field(max_length=1_000_000)


class CSVExport(Command):
    format: Literal["csv"]
    rows: list[Row] = Field(max_length=5000)
    formula_policy: Literal["escape", "preserve"] = "escape"


class JSONExport(Command):
    format: Literal["json"]
    value: JsonValue


class DOCXExport(Command):
    format: Literal["docx"]
    paragraphs: list[Annotated[str, StringConstraints(max_length=32767)]] = Field(
        default_factory=list, max_length=500
    )
    tables: list[TableData] = Field(default_factory=list, max_length=20)


class FormulaCell(Command):
    formula: str = Field(min_length=2, max_length=8192, pattern=r"^=")

    @field_validator("formula")
    @classmethod
    def restricted_formula(cls, value):
        # Excel may evaluate later. Exclude external/DDE and unapproved functions.
        if re.search(r"[\x00-\x1f]", value) or any(char in value for char in ("[", "]", "\\", "|")):
            raise ValueError("不支持外部工作簿、DDE 或多行公式。")
        allowed = {
            "SUM",
            "AVERAGE",
            "MIN",
            "MAX",
            "COUNT",
            "COUNTA",
            "IF",
            "IFERROR",
            "ROUND",
            "ROUNDUP",
            "ROUNDDOWN",
            "ABS",
            "AND",
            "OR",
            "NOT",
            "CONCAT",
            "CONCATENATE",
            "LEN",
            "LEFT",
            "RIGHT",
            "MID",
            "TRIM",
            "LOWER",
            "UPPER",
            "SUMIF",
            "SUMIFS",
            "COUNTIF",
            "COUNTIFS",
        }
        # Tokenizer is a parser only; no function is executed or external reference followed.
        from openpyxl.formula.tokenizer import Tokenizer, TokenizerError

        try:
            tokens = Tokenizer(value).items
        except TokenizerError:
            raise ValueError("公式不符合支持的语法。") from None
        if any(
            token.type == "FUNC"
            and token.subtype == "OPEN"
            and token.value[:-1].upper() not in allowed
            for token in tokens
        ):
            raise ValueError("公式使用了未启用的函数。")
        return value


WorkbookCell = Scalar | FormulaCell
WorkbookRow = Annotated[list[WorkbookCell], Field(max_length=100)]


class WorkbookSheet(Command):
    name: str = Field(min_length=1, max_length=31)
    rows: list[WorkbookRow] = Field(max_length=5000)

    @field_validator("name")
    @classmethod
    def sheet_name(cls, value):
        if (
            re.search(r"[\x00-\x1f\\/*?:\[\]]", value)
            or value.startswith("'")
            or value.endswith("'")
        ):
            raise ValueError("工作表名称含不支持的字符。")
        return value


class XLSXExport(Command):
    format: Literal["xlsx"]
    sheets: list[WorkbookSheet] = Field(min_length=1, max_length=8)

    @field_validator("sheets")
    @classmethod
    def unique_names(cls, value):
        names = [sheet.name.casefold() for sheet in value]
        if len(set(names)) != len(names):
            raise ValueError("工作表名称不能重复。")
        return value


class PresentationSlide(Command):
    title: str = Field(default="", max_length=300)
    paragraphs: list[Annotated[str, StringConstraints(max_length=5000)]] = Field(
        default_factory=list, max_length=25
    )
    table: TableData | None = None

    @model_validator(mode="after")
    def slide_budget(self):
        if sum(map(len, self.paragraphs)) + len(self.title) > 5000:
            raise ValueError("每页文本不能超过 5000 字符。")
        return self


class PPTXExport(Command):
    format: Literal["pptx"]
    slides: list[PresentationSlide] = Field(min_length=1, max_length=40)


class ImageConversion(Command):
    format: Literal["png", "jpeg", "webp"]
    source: ArtifactReference
    frame_index: int | None = Field(default=None, ge=0, le=1000)
    max_width: int | None = Field(default=None, ge=1, le=4096)
    max_height: int | None = Field(default=None, ge=1, le=4096)
    quality: int = Field(default=90, ge=1, le=95)
    jpeg_background: str = Field(default="#FFFFFF", pattern=r"^#[a-fA-F0-9]{6}$")


class PDFConversion(Command):
    format: Literal["pdf"]
    source: ArtifactReference


ExportSpec = Annotated[
    TextExport
    | CSVExport
    | JSONExport
    | DOCXExport
    | XLSXExport
    | PPTXExport
    | ImageConversion
    | PDFConversion,
    Field(discriminator="format"),
]


class ExportCreate(Command):
    request_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    spec: ExportSpec
    sources: list[ArtifactReference] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def budget(self):
        if self.filename in {".", ".."} or any(
            char in self.filename for char in ("/", "\\", "\0", "\r", "\n")
        ):
            raise ValueError("导出文件名只能是文件名，不能含路径或控制字符。")
        suffix = PurePath(self.filename).suffix.lower()
        allowed = {".jpg", ".jpeg"} if self.spec.format == "jpeg" else {"." + self.spec.format}
        if suffix not in allowed:
            raise ValueError("导出文件扩展名必须与目标格式一致。")
        try:
            content = json.dumps(
                self.model_dump(mode="json"), ensure_ascii=False, allow_nan=False
            ).encode()
        except (ValueError, TypeError):
            raise ValueError("导出内容不能含非有限数值或非 JSON 数据。") from None
        if len(content) > 2 * 1024**2:
            raise ValueError("导出请求超过 2 MiB，请缩小内容范围。")
        cells = sum(len(row) for table in getattr(self.spec, "tables", []) for row in table.rows)
        cells += sum(len(row) for sheet in getattr(self.spec, "sheets", []) for row in sheet.rows)
        cells += sum(
            len(row)
            for slide in getattr(self.spec, "slides", [])
            if slide.table
            for row in slide.table.rows
        )
        if cells > 20_000:
            raise ValueError("Office 导出最多 20,000 个单元格。")
        return self


class ExportProcessing(Command):
    """Internal duck-typed ProcessFile contract accepted by existing create_processing."""

    request_id: UUID
    operation: Literal["export"] = "export"
    start: Literal[1] = 1
    limit: Literal[1] = 1
    export_spec: ExportCreate
