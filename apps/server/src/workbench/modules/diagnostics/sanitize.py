"""Closed vocabularies: never redact arbitrary prose and call it safe."""

from types import MappingProxyType

ERROR_CODES = frozenset(
    {
        "provider_timeout",
        "provider_network_error",
        "provider_protocol_error",
        "provider_buffer_limit",
        "provider_input_limit",
        "provider_authentication",
        "provider_permission",
        "provider_rate_limit",
        "provider_quota",
        "provider_unavailable",
        "provider_context_mismatch",
        "provider_unsupported_content",
        "provider_native_input",
        "run_timeout",
        "run_cancelled",
        "tool_timeout",
        "permission_denied",
        "vault_locked",
        "mcp_outcome_unknown",
        "command_outcome_unknown",
        "command_timed_out",
        "file_processing_timeout",
        "artifact_not_found",
        "workspace_not_found",
        "diagnostics_database_unavailable",
        "diagnostics_owner_required",
    }
)
REASONS = MappingProxyType(
    {
        "ok": "已从标注的实际来源读取。",
        "missing": "该来源当前不存在；没有将缺失当作已测得的零占用。",
        "not_installed": "当前应用环境未安装该依赖。",
        "invalid_metadata": "来源元数据不符合允许的格式，细节已排除。",
        "query_unavailable": "该只读查询不可用或超过预算；未包含数据库异常正文。",
        "scan_limit": "计量达到时间、深度或条目预算，显示的是已观察部分。",
        "path_denied": "来源包含符号链接、不可验证路径或类型，未跟随。",
        "permission_denied": "无法安全读取来源，未包含系统异常或路径。",
        "unsupported": "当前平台未提供此模块所需的安全计量接口。",
        "clock_skew": "心跳时间超前于本次采样，健康状态保持未知。",
        "no_heartbeat": "没有实际心跳记录，不推测Worker存活。",
        "stale": "最新心跳超过30秒，不代表Worker目前就绪。",
        "reference_only": "仅报告引用及既有清单体积；未扫描、哈希或打包模型权重。",
        "database_scope": "数据库大小来自当前逻辑库，不包含整个集群、WAL或其他数据库。",
        "other": "详细内容已排除；该值不在诊断允许词表。",
    }
)


def sanitize_code(value) -> str:
    return value if isinstance(value, str) and value in ERROR_CODES else "other"


def sanitize_reason(value) -> str:
    """Only a reason identifier selects fixed text; supplied prose is discarded."""
    return REASONS[value] if isinstance(value, str) and value in REASONS else REASONS["other"]
