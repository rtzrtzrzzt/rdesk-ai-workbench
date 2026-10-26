"""Actionable parameter errors without echoing commands, values or unknown keys."""

import math

from pydantic import BaseModel, ValidationError

from workbench.contracts.command_limits import DEFAULT_COMMAND_TIMEOUT_SECONDS
from workbench.contracts.errors import AppError


def parameter_error(error: ValidationError, schema: type[BaseModel]) -> AppError:
    names = set()

    def fields(value):
        if isinstance(value, dict):
            names.update(value.get("properties", {}))
            for child in value.values():
                fields(child)
        elif isinstance(value, list):
            for child in value:
                fields(child)

    fields(schema.model_json_schema())
    issues = []
    messages = {
        "missing": "必须提供此字段",
        "extra_forbidden": "包含工具未声明的字段，请按schema移除",
        "int_parsing": "必须是整数",
        "int_type": "必须是整数",
        "int_from_float": "必须是整数",
        "float_parsing": "必须是数值",
        "float_type": "必须是数值",
        "string_type": "必须是文本",
        "bool_type": "必须是布尔值",
        "literal_error": "请使用schema列出的允许值",
        "enum": "请使用schema列出的允许值",
    }
    bounds = {
        "less_than_equal": ("le", "最大允许"),
        "greater_than_equal": ("ge", "最小允许"),
        "less_than": ("lt", "必须小于"),
        "greater_than": ("gt", "必须大于"),
        "string_too_long": ("max_length", "文本最大长度"),
        "string_too_short": ("min_length", "文本最小长度"),
        "too_long": ("max_length", "最多项数"),
        "too_short": ("min_length", "最少项数"),
    }
    for item in error.errors(include_url=False, include_input=False)[:8]:
        # A location can contain user-supplied extra/dictionary keys. Only emit
        # schema-declared names and array indices, never those arbitrary keys.
        location = (
            ".".join(
                str(part)
                if (isinstance(part, int) and part >= 0) or part in names
                else "未声明字段"
                for part in item["loc"]
            )
            or "参数"
        )
        kind = item["type"]
        message = messages.get(kind, "不符合此字段的schema格式")
        if kind in bounds:
            key, label = bounds[kind]
            value = item.get("ctx", {}).get(key)
            if type(value) in {int, float} and math.isfinite(value):
                message = f"{label}{value}"
                if location.endswith("_seconds"):
                    message += "秒"
        issues.append({"field": location, "message": message})
    return AppError(
        "invalid_tool_arguments",
        "工具参数不符合格式："
        + "；".join(f"{item['field']}：{item['message']}" for item in issues)
        + "。请按本轮工具schema修正，不要重复提交相同参数。",
        details={"fields": issues},
    )


def command_timeout(values: dict, options: dict, recorded: dict | None = None) -> dict:
    maximum = options.get("command_timeout_seconds", DEFAULT_COMMAND_TIMEOUT_SECONDS)
    requested = values.get("timeout_seconds")
    if requested is None:
        # A checkpoint may predate the new default. Reuse its audited value so
        # recovery cannot extend the deadline or change the operation identity.
        requested = recorded["timeout_seconds"] if recorded else maximum
    if requested > maximum:
        issue = {
            "field": "timeout_seconds",
            "message": f"本任务最大允许{maximum}秒；可缩短本次时限或调整新任务的高级时间预算",
        }
        raise AppError(
            "invalid_tool_arguments",
            f"工具参数不符合格式：timeout_seconds：{issue['message']}。",
            details={"fields": [issue]},
        )
    return {**values, "timeout_seconds": requested}
