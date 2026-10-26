"""Read a fixed public manual without granting access to the application directory."""

from workbench.config import PROJECT_ROOT
from workbench.modules.tools.filesystem import read_file


def read(values):
    result = read_file(PROJECT_ROOT, {**values, "path": "docs/ai-guide.md"})
    if result["next"]:
        result["next"].pop("path", None)
    return {
        **result,
        "note": (
            "这是工作台操作说明；当前能力、参数和权限"
            "以实时工具schema、设置合同及实际结果为准。"
        ),
    }
