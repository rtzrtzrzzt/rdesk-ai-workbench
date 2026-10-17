from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from workbench.modules.automation import tools as automation
from workbench.modules.files.exports_dto import ArtifactReference, ExportSpec
from workbench.modules.generated.a2ui import A2UIInput
from workbench.modules.generated.dto import SurfaceDraft
from workbench.modules.processes.dto import CommandTarget, ReadCommand, StartCommand
from workbench.modules.search.dto import FetchPage, SearchQuery


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListFiles(Arguments):
    path: str = "."


class ReadFile(Arguments):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=200, ge=1)


class SearchFiles(Arguments):
    query: str = Field(min_length=1, max_length=500)
    path: str = "."


class WriteFile(Arguments):
    path: str
    content: str = Field(max_length=1_000_000)
    expected_version: str = Field(pattern=r"^(missing|[a-f0-9]{64})$")


class ApplyPatch(Arguments):
    path: str
    old_text: str = Field(min_length=1, max_length=500_000)
    new_text: str = Field(max_length=500_000)
    expected_version: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExecCommand(Arguments):
    command: str = Field(min_length=1, max_length=20_000)
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class Option(Arguments):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=2000)


class AskUser(Arguments):
    prompt: str = Field(min_length=1, max_length=10_000)
    kind: Literal["single_choice", "multiple_choice", "text"] = "text"
    options: list[Option] = Field(default_factory=list, max_length=20)


class DiscoverTools(Arguments):
    names: list[str] = Field(default_factory=list, max_length=6)


class MemorySearch(Arguments):
    query: str = Field(default="", max_length=200)
    scope: Literal["user", "workspace", "all"] = "all"


class MemoryRead(Arguments):
    document_id: UUID
    start_line: int = Field(default=1, ge=1)
    limit: int = Field(default=50, ge=1, le=200)


class MemoryPropose(Arguments):
    scope: Literal["user", "workspace"] = "workspace"
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=20_000)
    expected_scope_generation: int = Field(ge=1)
    document_id: UUID | None = None
    base_version: str = Field(default="missing", pattern=r"^(missing|[a-f0-9]{64})$")
    source_refs: list[str] = Field(default_factory=list, max_length=20)


class ListArtifacts(Arguments):
    pass


class ReadArtifact(Arguments):
    artifact_id: UUID
    start_line: int = Field(default=1, ge=1)
    limit: int = Field(default=100, ge=1, le=200)


class ExportFile(Arguments):
    filename: str = Field(min_length=1, max_length=255)
    spec: ExportSpec
    sources: list[ArtifactReference] = Field(default_factory=list, max_length=8)


class FileTaskStatus(Arguments):
    run_id: UUID


class SpawnAgent(Arguments):
    name: str = Field(min_length=1, max_length=100)
    task: str = Field(min_length=1, max_length=8000)
    deliverable: str = Field(min_length=1, max_length=2000)
    model_connection_id: UUID | None = None
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["list_files", "read_file", "search_files"],
        min_length=1,
        max_length=16,
    )
    paths: list[str] = Field(default_factory=list, max_length=16)
    mode: Literal["read_only", "serial_write"] = "read_only"
    max_model_rounds: int = Field(default=8, ge=1, le=16)
    max_output_tokens: int = Field(default=1024, ge=64, le=4096)


class AgentTarget(Arguments):
    agent_id: UUID


class AgentMessage(AgentTarget):
    message: str = Field(min_length=1, max_length=8000)


class WaitAgents(Arguments):
    timeout_seconds: int = Field(default=300, ge=1, le=3600)


class ReadTaskHistory(Arguments):
    run_id: UUID | None = None
    start_message: int = Field(default=1, ge=1)
    limit: int = Field(default=3, ge=1, le=10)
    max_chars: int = Field(default=600, ge=100, le=2000)


class MCPListTools(Arguments):
    connection_id: UUID | None = None
    tool_name: str | None = Field(default=None, max_length=200)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=5, ge=1, le=10)


class MCPCall(Arguments):
    connection_id: UUID
    snapshot_id: str = Field(min_length=32, max_length=36)
    tool_name: str = Field(min_length=1, max_length=200)
    arguments: dict = Field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    schema: type[BaseModel]
    description: str
    requires_approval: bool = False
    replay_policy: Literal["read", "idempotent", "conditional_write", "at_most_once"] = "read"
    module: str = "filesystem"


TOOLS: dict[str, ToolSpec] = {
    "start_command": ToolSpec(
        StartCommand,
        "启动需明确批准的长主机命令，返回持久句柄；不是OS沙箱。每任务一个/本机两个，任务结束会停止。",
        requires_approval=True,
        replay_policy="at_most_once",
        module="processes",
    ),
    "read_command_output": ToolSpec(
        ReadCommand,
        "按字节游标读取本任务命令的真实日志与退出状态；完整结束日志另存产物。",
        replay_policy="idempotent",
        module="processes",
    ),
    "stop_command": ToolSpec(
        CommandTarget,
        "停止本任务长命令；实际进程清理确认后才关闭句柄，不声称外部业务效果撤销。",
        replay_policy="idempotent",
        module="processes",
    ),
    "present_a2ui": ToolSpec(
        A2UIInput,
        "提交有序A2UI v0.9.1消息快照的受限只读组件；只有ready且真实来源核对后发布，不执行HTML/JS。",
        replay_policy="idempotent",
        module="generated",
    ),
    "inspect_automation": ToolSpec(
        Arguments,
        "读取用户电脑授权、原生能力和本任务会话；不能自行启用或扩大范围。",
        module="automation",
    ),
    "browser_open": ToolSpec(
        automation.BrowserOpen,
        "在用户已授权的精确网页范围中开独立浏览器；不继承用户登录。",
        requires_approval=True,
        replay_policy="at_most_once",
        module="automation",
    ),
    "browser_observe": ToolSpec(
        automation.BrowserObserve,
        "读取当前页面正文和受限元素引用；可选保存真实截图。",
        replay_policy="at_most_once",
        module="automation",
    ),
    "browser_action": ToolSpec(
        automation.BrowserStep,
        "绑定最新观察引用导航/点击/输入/滚动；每次需批准，未知不重放。",
        requires_approval=True,
        replay_policy="at_most_once",
        module="automation",
    ),
    "browser_download": ToolSpec(
        automation.BrowserDownload,
        "下载最新观测中指定链接的原件，最多16MiB；无登录Cookie，仅精确授权目标，结果为文件产物。",
        requires_approval=True,
        replay_policy="at_most_once",
        module="automation",
    ),
    "computer_open": ToolSpec(
        automation.ComputerOpen,
        "打开已授权桌面观察/控制会话；模式固定，控制需精确窗口及单写租约。",
        requires_approval=True,
        replay_policy="at_most_once",
        module="automation",
    ),
    "computer_observe": ToolSpec(
        automation.SessionTarget,
        "捕获当前授权窗口的真实截图、原始像素几何和代数。",
        requires_approval=True,
        replay_policy="at_most_once",
        module="automation",
    ),
    "computer_action": ToolSpec(
        automation.ComputerStep,
        "按最新截图和窗口执行有限原生输入，坐标为原图像素，缩略图坐标须按尺寸比例换算。每次需批准。",
        requires_approval=True,
        replay_policy="at_most_once",
        module="automation",
    ),
    "automation_stop": ToolSpec(
        automation.SessionTarget,
        "停止本任务电脑会话并等待真实关闭，不撤销已发生业务效果。",
        replay_policy="idempotent",
        module="automation",
    ),
    "inspect_ui_catalog": ToolSpec(
        Arguments, "查看可信界面组件目录和真实数据绑定方式；不执行HTML或JS。", module="generated"
    ),
    "present_ui": ToolSpec(
        SurfaceDraft,
        "发布workbench-ui/1只读界面。先查看inspect_ui_catalog；表图数据由来源绑定填入。",
        replay_policy="idempotent",
        module="generated",
    ),
    "mcp_list_tools": ToolSpec(
        MCPListTools, "分页查看用户已发现的MCP目录；指定tool_name读取单个schema。", module="mcp"
    ),
    "mcp_call": ToolSpec(
        MCPCall,
        "按固定目录调用MCP工具；每次需真实参数批准，支持持久表单，未知结果不重发。",
        requires_approval=True,
        replay_policy="at_most_once",
        module="mcp",
    ),
    "read_task_history": ToolSpec(
        ReadTaskHistory,
        "分段读取当前任务树原始会话消息；压缩后按实际来源恢复条件，已忘记记忆正文过滤。",
        module="context",
    ),
    "inspect_context": ToolSpec(
        ListArtifacts,
        "查看当前规则/工具前缀与上下文压缩来源；不提供密钥或隐藏推理。",
        module="context",
    ),
    "list_files": ToolSpec(ListFiles, "列出工作区内的文件，返回有界结果。"),
    "read_file": ToolSpec(ReadFile, "读取UTF-8文件的指定行，并返回供条件修改使用的版本。"),
    "search_files": ToolSpec(SearchFiles, "在工作区文本文件中搜索字面关键词。"),
    "write_file": ToolSpec(
        WriteFile,
        "条件创建或替换文本文件；existing文件须使用read_file版本，"
        "新文件expected_version=missing。必须等待用户授权。",
        True,
        "conditional_write",
    ),
    "apply_patch": ToolSpec(
        ApplyPatch,
        "在指定版本文件中精确替换唯一一段文字。必须等待用户授权。",
        True,
        "conditional_write",
    ),
    "exec_command": ToolSpec(
        ExecCommand,
        "按任务冻结的执行配置运行有界命令，保存日志与进程归属；必须等待用户对该命令授权。",
        True,
        "at_most_once",
    ),
    "ask_user": ToolSpec(
        AskUser, "向用户询问必要信息，支持选项或自由输入；等待真实提交。", module="interaction"
    ),
    "discover_tools": ToolSpec(
        DiscoverTools,
        "names为空列出额外工具；填写工具名后为本任务加载其schema。记忆、网络工具按需加载。",
        replay_policy="idempotent",
        module="discovery",
    ),
    "memory_search": ToolSpec(
        MemorySearch,
        "检索私有Markdown记忆；query为空列出索引和遗忘世代。结果有来源与版本。",
        module="memory",
    ),
    "memory_read": ToolSpec(
        MemoryRead, "读取记忆正文的指定行；已忘记内容不可使用。", module="memory"
    ),
    "memory_propose": ToolSpec(
        MemoryPropose,
        "提交待用户审阅的记忆候选，不自动成为长期记忆。先查询当前scope generation和版本。",
        replay_policy="idempotent",
        module="memory",
    ),
    "web_search": ToolSpec(
        SearchQuery, "通过用户明确启用的搜索连接检索联网来源。", module="search"
    ),
    "read_webpage": ToolSpec(
        FetchPage, "读取公开网页纯文本；拒绝私网地址和跨权限重定向。", module="search"
    ),
    "list_artifacts": ToolSpec(
        ListArtifacts, "列出工作区已保存文件及派生结果的来源、类型和版本。", module="artifacts"
    ),
    "read_artifact": ToolSpec(
        ReadArtifact, "分段读取已保存文件或提取文本，返回实际来源及截断信息。", module="artifacts"
    ),
    "export_file": ToolSpec(
        ExportFile,
        "从明确结构化内容创建Office/文本/表格文件或转换图片；保留原件，受限异步处理。",
        True,
        "idempotent",
        "artifacts",
    ),
    "file_task_status": ToolSpec(
        FileTaskStatus, "查询文件处理真实进度与已发布结果，不虚构转换成功。", module="artifacts"
    ),
    "spawn_agent": ToolSpec(
        SpawnAgent,
        "派生独立子任务；最多3个未释放名额、深度1，指定目标/交付/工具/文件范围。共享模型排队。",
        replay_policy="idempotent",
        module="collaboration",
    ),
    "inspect_agents": ToolSpec(
        ListArtifacts, "查看真实子任务状态、结果、问题及未释放名额。", module="collaboration"
    ),
    "wait_agents": ToolSpec(
        WaitAgents,
        "持久等待子任务，释放Worker与模型槽；超时只返回真实状态，不取消子任务。",
        replay_policy="idempotent",
        module="collaboration",
    ),
    "send_agent_message": ToolSpec(
        AgentMessage,
        "向当前任务树中仍活动的子任务补充要求，下一次模型调用生效。",
        replay_policy="idempotent",
        module="collaboration",
    ),
    "cancel_agent": ToolSpec(
        AgentTarget,
        "请求停止子任务，真实清理结束前继续占名额。",
        replay_policy="idempotent",
        module="collaboration",
    ),
    "release_agent": ToolSpec(
        AgentTarget,
        "释放已终止且已清理的子Agent名额；保留全部结果。",
        replay_policy="idempotent",
        module="collaboration",
    ),
}

CORE_TOOL_NAMES = [
    "list_files",
    "read_file",
    "search_files",
    "write_file",
    "apply_patch",
    "exec_command",
    "ask_user",
    "discover_tools",
]


def compact_schema(value):
    if isinstance(value, dict):
        return {
            key: compact_schema(item)
            for key, item in value.items()
            if not (key == "title" and isinstance(item, str))
        }
    if isinstance(value, list):
        return [compact_schema(item) for item in value]
    return value


def definitions(names: list[str] | None = None) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": TOOLS[name].description,
                "parameters": compact_schema(TOOLS[name].schema.model_json_schema()),
            },
        }
        for name in (names if names is not None else CORE_TOOL_NAMES)
    ]
