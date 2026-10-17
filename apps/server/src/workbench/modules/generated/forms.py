"""A deliberately small non-secret form vocabulary, shared with MCP elicitation."""

import json
import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from workbench.contracts.errors import AppError

SENSITIVE = re.compile(r"password|passwd|secret|token|api.?key|credential|private.?key", re.I)


def check_form(schema: dict) -> None:
    properties = schema.get("properties", {})
    if schema.get("type") != "object" or not isinstance(properties, dict) or len(properties) > 20:
        raise AppError("form_schema_unsupported", "表单需要至多20个简单字段。", 422)
    allowed = {
        "type",
        "title",
        "description",
        "properties",
        "required",
        "additionalProperties",
        "$schema",
        "enum",
        "oneOf",
        "const",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "default",
        "format",
        "pattern",
    }

    def visit(value, depth=0):
        if not isinstance(value, dict) or depth > 3 or set(value) - allowed:
            raise AppError("form_schema_unsupported", "此表单包含尚未支持的结构。", 422)
        for key in value:
            if key.startswith("$") and key != "$schema":
                raise AppError("form_schema_unsupported", "表单不允许外部引用。", 422)
        if value.get("format") not in {None, "email", "uri", "date", "date-time"}:
            raise AppError("form_schema_unsupported", "此表单字段格式尚未支持。", 422)
        for name, field in value.get("properties", {}).items():
            if SENSITIVE.search(name):
                raise AppError("form_secret_denied", "凭据必须通过专用密钥入口填写。", 422)
            visit(field, depth + 1)
            if field.get("type") not in {"string", "integer", "number", "boolean", "array"}:
                raise AppError("form_schema_unsupported", "表单字段类型尚未支持。", 422)
            if field.get("type") == "array":
                item = field.get("items", {})
                if item.get("type") != "string" or not isinstance(item.get("enum"), list):
                    raise AppError("form_schema_unsupported", "数组字段只支持明确的多选值。", 422)
                visit(item, depth + 1)
        if "oneOf" in value:
            if not isinstance(value["oneOf"], list) or len(value["oneOf"]) > 30:
                raise AppError("form_schema_unsupported", "选项数量不受支持。", 422)
            for item in value["oneOf"]:
                visit(item, depth + 1)

    visit(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise AppError("form_schema_invalid", "表单定义无效。", 422) from None


def validate_answer(schema: dict, answer) -> None:
    if (
        not isinstance(answer, dict)
        or set(answer) != {"action", "fields"}
        or answer["action"] not in {"accept", "decline", "cancel"}
        or not isinstance(answer["fields"], dict)
        or len(json.dumps(answer).encode()) > 16_384
    ):
        raise AppError("invalid_form_answer", "请提交此表单的有效答案或明确拒绝。", 422)
    if answer["action"] != "accept":
        if answer["fields"]:
            raise AppError("invalid_form_answer", "拒绝或取消不能携带表单字段。", 422)
        return
    check_form(schema)
    fields = answer["fields"]
    if set(fields) - set(schema.get("properties", {})):
        raise AppError("invalid_form_answer", "答案中包含未登记字段。", 422)
    try:
        Draft202012Validator(schema).validate(fields)
    except ValidationError:
        raise AppError("invalid_form_answer", "答案不满足已保存表单的字段约束。", 422) from None
