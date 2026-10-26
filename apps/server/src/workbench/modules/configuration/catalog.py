"""Configuration discovery with typed contracts and explicit operational boundaries."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    title: str
    description: str
    scopes: tuple[str, ...]
    effective_at: str
    source: str
    instructions: str
    operator_only: bool = False


SECTIONS = {
    "experience": Section(
        "通用与外观",
        "主题、运行中消息默认队列或引导",
        ("user",),
        "live_browser_sync",
        "account",
        "先读取版本，再保存theme和input_mode；浏览器自动同步。",
    ),
    "conversation_history": Section(
        "其它对话读取范围",
        "用户控制跨对话访问：关闭、当前工作区或账户全部工作区；子Agent始终限任务树",
        ("user",),
        "live_permission",
        "account",
        "AI可读取范围和版本；扩大/关闭须用户在设置→通用→其它对话读取范围操作。"
        "新工具list_conversations/search_conversations/read_conversation每次检查；当前任务恢复read_task_history保持原边界。",
        True,
    ),
    "runtime": Section(
        "用户任务默认",
        "主模型、审核思考、审批、命令预算、上下文、子任务、沙箱",
        ("user",),
        "new_runs",
        "runtime_defaults",
        "普通参数仅影响新任务；固定模型推理容量1不能改大。",
    ),
    "workspace_runtime": Section(
        "工作区任务默认",
        "工作区覆盖及每字段有效值/继承来源",
        ("workspace",),
        "new_runs",
        "workspace_runtime_defaults",
        "options中null表示继承；读取effective.sources确认最终值。",
    ),
    "models": Section(
        "模型与行为预设",
        "模型连接、能力声明、思考档位、原生/五档/自定义模板",
        ("user",),
        "new_runs",
        "models",
        "连接按版本保存；普通更新保留凭据。能力支持必须有真实证据，修改连接后重新验证。",
    ),
    "search": Section(
        "联网搜索",
        "连接、引擎、语言、地区、数量和超时",
        ("user", "workspace"),
        "next_call",
        "search_connections",
        "选择scope；工作区覆盖用户连接。保存指定引擎时沿用真实引擎目录校验。",
    ),
    "mcp": Section(
        "MCP连接",
        "传输、工具白名单、超时、凭据引用与执行授权",
        ("workspace",),
        "live_permission",
        "mcp_connections",
        "保存后仍须显式发现；凭据正文由用户保险库入口管理，AI只使用引用。",
    ),
    "computer": Section(
        "电脑总开关",
        "用户级电脑操作许可",
        ("user",),
        "immediate",
        "automation_settings",
        "关闭立即停止旧会话并撤销租约；开启不恢复旧会话。每个实际动作继续审核。",
    ),
    "automation_policy": Section(
        "工具与设备范围",
        "工作区浏览器/桌面授权范围与有效期",
        ("workspace",),
        "live_permission",
        "automation_policy",
        "范围授权不能代替实际动作审核，也不能开放截图外发。",
    ),
    "extensions": Section(
        "插件与Skill",
        "来源、版本、作用域、启停、更新、卸载与MCP候选",
        ("user", "workspace"),
        "new_runs_and_live_revocation",
        "extensions",
        "安装不执行脚本；Run冻结版本，停用实时撤权。install/upgrade前先检查来源。",
    ),
    "media": Section(
        "生成模型",
        "图像/视频/音频/3D连接、默认模型及本机生成参数",
        ("user", "workspace"),
        "new_jobs",
        "media_connections",
        "按版本保存，未接入能力不代表可生成；修改内存预算不能冒充真实验收。",
    ),
    "global_rules": Section(
        "全局规则",
        "用户AGENTS.md正文与内容版本",
        ("user",),
        "next_model_call",
        "memory_rules",
        "仅用户明确要求时编辑；使用内容版本，目录规则继续由授权文件工具管理。",
    ),
    "developer_tools": Section(
        "代码智能",
        "受信本地语言服务器与初始化配置",
        ("user", "workspace"),
        "next_call",
        "configuration_states",
        "language_servers=null自动发现；[]禁用通用LSP；命令须可信配置，调用仍受操作审核。",
    ),
    "file_processing": Section(
        "文件处理",
        "处理器可用性、提取、OCR、ASR及转换参数",
        ("application", "task"),
        "per_job",
        "file_processors",
        "用inspect_file_processors/process_file选择已有组件；安装/ASR模型路径由本机组件准备流程管理。",
        True,
    ),
    "credentials": Section(
        "凭据保险库",
        "初始化、锁定、解锁及连接凭据配置入口",
        ("user",),
        "immediate",
        "vault",
        "普通AI配置接口不读取或提交密钥正文；使用设置中的保险库和连接凭据入口。",
        True,
    ),
    "maintenance": Section(
        "数据与维护",
        "一致备份、隔离恢复、清理和诊断",
        ("application",),
        "explicit_operation",
        "maintenance",
        "这些是独立维护操作，须遵守既有静止边界与专用接口；不作为普通配置保存执行。",
        True,
    ),
    "application": Section(
        "应用启动参数",
        "监听、数据目录、会话、TLS、Worker环境及整机命令并发",
        ("application",),
        "restart",
        "WORKBENCH_environment",
        "参照config.py与原生启动入口配置；命令并发由WORKBENCH_COMMAND_MAX_ACTIVE_PER_RUN"
        "和WORKBENCH_COMMAND_MAX_ACTIVE_TOTAL设置（默认2/4，均1..32，前者不大于后者），"
        "统一应用到API及Worker，重启应用后生效；有效值可用list_commands核对。"
        "数据迁移/TLS/数据库密码不通过在线AI接口修改。",
        True,
    ),
    "launcher": Section(
        "本机启动配置",
        "已准备数据库、搜索、模型、应用端口与前端路径",
        ("application",),
        "next_start",
        ".local/launcher/profile.json",
        "使用runtime/distribution/one-click-services.md；文件含口令，禁止整文件读取/导出，接口仅提供方法。",
        True,
    ),
    "model_runtime": Section(
        "本地推理环境",
        "固定模型、上下文、MTP、加载和资源保护",
        ("application",),
        "model_restart",
        "runtime/model",
        "使用runtime/model/README.md与受管modelctl；更改须另行本机验证，在线配置不重载模型。",
        True,
    ),
}


def actions():
    from workbench.contracts.requests import CapabilitiesUpdate, ModelCreate, ModelUpdate
    from workbench.modules.automation.dto import AutomationSave, ComputerSettingsSave
    from workbench.modules.extensions import dto as extensions
    from workbench.modules.mcp.host_dto import MCPDiscover, MCPSave
    from workbench.modules.media.dto import MediaConnectionSave
    from workbench.modules.memory.dto import GlobalRulesUpdate
    from workbench.modules.preferences.configuration import ExperienceSave
    from workbench.modules.preferences.service import DefaultsSave, WorkspaceDefaultsSave
    from workbench.modules.search.dto import SearchConnectionSave

    from .dto import DeveloperSave

    return {
        "experience": {"save": ExperienceSave},
        "runtime": {"save": DefaultsSave},
        "workspace_runtime": {"save": WorkspaceDefaultsSave},
        "models": {
            "create": ModelCreate,
            "update": ModelUpdate,
            "capabilities": CapabilitiesUpdate,
        },
        "search": {"save": SearchConnectionSave},
        "mcp": {"save": MCPSave, "discover": MCPDiscover},
        "computer": {"save": ComputerSettingsSave},
        "automation_policy": {"save": AutomationSave},
        "extensions": {
            "install": extensions.ExtensionInstall,
            "upgrade": extensions.ExtensionUpgrade,
            "save": extensions.ExtensionChange,
            "remove": extensions.ExtensionRemove,
            "import_mcp": extensions.ExtensionMCPImport,
        },
        "media": {"save": MediaConnectionSave},
        "global_rules": {"save": GlobalRulesUpdate},
        "developer_tools": {"save": DeveloperSave},
    }


SECRET_FIELDS = frozenset(
    {
        "api_key",
        "encrypted_api_key",
        "password",
        "passphrase",
        "token",
        "access_token",
        "refresh_token",
        "secrets",
        "clear_secrets",
        "clear_api_key",
        "database_url",
        "environment",
        "env",
        "headers",
        "authorization",
        "proxy-authorization",
        "client_secret",
        "private_key",
    }
)


def public_schema(model):
    schema = model.model_json_schema()

    def trim(item):
        if isinstance(item, dict):
            if "properties" in item:
                item["properties"] = {
                    key: value
                    for key, value in item["properties"].items()
                    if key not in SECRET_FIELDS | {"request_id"}
                }
                if "required" in item:
                    item["required"] = [
                        key for key in item["required"] if key in item["properties"]
                    ]
            for value in item.values():
                trim(value)
        elif isinstance(item, list):
            for value in item:
                trim(value)

    trim(schema)
    return schema


def describe(name, *, include_schema=True):
    from dataclasses import asdict

    item = {"section": name, **asdict(SECTIONS[name])}
    contracts = actions().get(name, {})
    item["actions"] = {
        action: public_schema(model) if include_schema else {"schema": "read_settings"}
        for action, model in contracts.items()
    }
    item["credential_policy"] = "secret_values_user_only; references_and_configured_state_only"
    item["documentation"] = "docs/technical/ai-configuration.md"
    item["guide_tool"] = "read_workbench_guide"
    return item


def discover(query="", include_schemas=False):
    words = query.casefold().split()
    return {
        "protocol": "workbench-settings/1",
        "guide": {"tool": "read_workbench_guide", "path": "docs/ai-guide.md"},
        "sections": [
            describe(name, include_schema=include_schemas)
            for name, item in SECTIONS.items()
            if all(word in f"{name} {item.title} {item.description}".casefold() for word in words)
        ],
        "workflow": (
            "discover_settings → read_settings → preview_settings → configure_settings；"
            "未知结果先读回执及实际配置，不重发新请求。"
        ),
    }
