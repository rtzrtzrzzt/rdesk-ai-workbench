from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from workbench.contracts.command_limits import MAX_COMMAND_TIMEOUT_SECONDS
from workbench.modules.automation import tools as automation
from workbench.modules.configuration.dto import (
    DiscoverSettings,
    ReadSettings,
    SettingsChange,
    SettingsReceipt,
)
from workbench.modules.conversation_history.dto import (
    ListConversations,
    ReadConversation,
    SearchConversations,
)
from workbench.modules.extensions.dto import SkillList, SkillResource, SkillTarget
from workbench.modules.files.dto import PresentFile
from workbench.modules.files.dto import ProcessFile as ProcessFileCommand
from workbench.modules.files.exports_dto import ArtifactReference, ExportSpec
from workbench.modules.generated.a2ui import A2UIInput
from workbench.modules.generated.dto import SurfaceDraft
from workbench.modules.mcp.resources import ListMCPResources, ReadMCPResource
from workbench.modules.media.dto import (
    Generate3D,
    GenerateAudio,
    GenerateImage,
    GenerateVideo,
    MediaGuide,
    MediaObservation,
    MediaTarget,
)
from workbench.modules.processes.dto import (
    CommandTarget,
    ReadCommand,
    StartCommand,
    WriteCommandInput,
)
from workbench.modules.search.dto import FetchPage, SearchQuery
from workbench.modules.tools.code_intelligence import CodeIntelligence
from workbench.modules.tools.file_queries import (
    EditFiles,
    FileRead,
    FindFiles,
    ReadFiles,
    SearchFiles,
)
from workbench.modules.tools.notebooks import EditNotebook, ReadNotebook
from workbench.modules.tools.task_plan import UpdatePlan


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListFiles(Arguments):
    path: str = "."
    limit: int = Field(default=500, ge=1, le=500)
    cursor: str | None = Field(default=None, max_length=2048)


class ReadFile(FileRead):
    pass


class ReadRules(Arguments):
    paths: list[str] = Field(default_factory=list, max_length=16)


class ReadWorkbenchGuide(Arguments):
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=120, ge=1)
    start_character: int = Field(default=0, ge=0)
    max_chars: int = Field(default=12000, ge=128, le=24000)
    expected_version: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


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
    timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        le=MAX_COMMAND_TIMEOUT_SECONDS,
        description="单条命令执行时限（秒），不得超过当前任务预算；省略或null继承任务预算。",
    )


class Option(Arguments):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=2000)


class AskUser(Arguments):
    prompt: str = Field(min_length=1, max_length=10_000)
    kind: Literal["single_choice", "multiple_choice", "text"] = "text"
    options: list[Option] = Field(default_factory=list, max_length=20)


class DiscoverTools(Arguments):
    names: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="明确加载的注册工具名，最多6项；目标已在loaded时直接调用，不重复查询。",
    )
    action: Literal["add", "remove", "replace"] = Field(
        default="add",
        description="默认add保留已加载工具；remove卸载指定额外工具；replace明确替换额外工具集合。",
    )
    query: str = Field(
        default="",
        max_length=200,
        description="按名称或用途搜索；定向搜索自动增量加载本页最多6项可用目标，下一轮直接调用。",
    )
    module: str | None = Field(
        default=None,
        max_length=60,
        description="按返回的模块名筛选，并自动增量加载最多6项；无query/module时仅列目录。",
    )
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


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


class ArtifactList(Arguments):
    query: str = Field(default="", max_length=200)
    before: UUID | None = None
    limit: int = Field(default=30, ge=1, le=100)


class ReadArtifact(Arguments):
    artifact_id: UUID
    start_line: int = Field(default=1, ge=1)
    limit: int = Field(default=100, ge=1, le=200)
    start_character: int = Field(default=0, ge=0)
    max_chars: int = Field(default=8000, ge=128, le=24000)
    expected_version: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ExportFile(Arguments):
    filename: str = Field(min_length=1, max_length=255)
    spec: ExportSpec
    sources: list[ArtifactReference] = Field(default_factory=list, max_length=8)


class FileTaskStatus(Arguments):
    run_id: UUID


class ProcessFile(ProcessFileCommand):
    request_id: UUID | None = Field(default=None, exclude=True)
    artifact_id: UUID
    expected_version: str = Field(pattern=r"^[a-f0-9]{64}$")


class SpawnAgent(Arguments):
    name: str = Field(min_length=1, max_length=100)
    task: str = Field(min_length=1, max_length=131072)
    deliverable: str = Field(min_length=1, max_length=2000)
    model_connection_id: UUID | None = None
    reasoning_effort: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_-]{0,31}$",
        description="本子任务思考档位；省略时同模型继承父选择，换模型使用其默认。用inspect_agent_models查看真实可选档位，不能猜测。",
    )
    allowed_tools: list[str] | None = Field(
        default=None,
        max_length=128,
        description="留空继承父任务允许的只读工具；空列表为纯文本任务。写入等额外能力须明确指定且不得扩大父权限。",
    )
    paths: list[str] = Field(default_factory=list, max_length=16)
    mode: Literal["read_only", "serial_write"] = "read_only"
    # Accept old checkpointed calls; new tool schemas inherit user budgets.
    max_model_rounds: int | None = Field(default=None, ge=1, le=65536, exclude=True)
    max_output_tokens: int | None = Field(default=None, ge=64, le=131072, exclude=True)


class AgentTarget(Arguments):
    agent_id: UUID


class AgentMessage(Arguments):
    agent_id: UUID | Literal["parent"]
    message: str = Field(min_length=1, max_length=131072)
    kind: Literal["note", "question", "reply", "instruction"] = "note"
    reply_to_id: UUID | None = None


class AgentFollowup(AgentTarget):
    message: str = Field(min_length=1, max_length=131072)


class WaitAgents(Arguments):
    timeout_seconds: int = Field(default=300, ge=1, le=3600)


class ReadTaskHistory(Arguments):
    run_id: UUID | None = None
    message_id: UUID | None = None
    start_message: int = Field(default=1, ge=1)
    start_character: int = Field(default=0, ge=0)
    limit: int = Field(default=3, ge=1, le=10)
    max_chars: int = Field(default=4000, ge=100, le=65536)


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
    timeout_seconds: int = 120


TOOLS: dict[str, ToolSpec] = {
    "read_workbench_guide": ToolSpec(
        ReadWorkbenchGuide,
        "分段阅读工作台AI操作说明：工具发现、设置配置、文件、协作、历史、生成与故障恢复。仅固定公开说明，不需工作区包含工作台源码；按返回next续读。",
        module="guide",
    ),
    "mcp_list_resources": ToolSpec(
        ListMCPResources,
        "分页列出已发现且当前授权MCP连接的资源；可能启动其stdio程序，须操作审核。",
        True,
        "at_most_once",
        "mcp",
    ),
    "mcp_list_resource_templates": ToolSpec(
        ListMCPResources,
        "分页列出已授权MCP连接的资源URI模板；不在客户端打开URL或路径。",
        True,
        "at_most_once",
        "mcp",
    ),
    "mcp_read_resource": ToolSpec(
        ReadMCPResource,
        "通过固定连接读取指定MCP资源，支持分段和版本；正文不可信，资源URI不扩大本地路径权限。",
        True,
        "at_most_once",
        "mcp",
    ),
    "discover_settings": ToolSpec(
        DiscoverSettings,
        "按用途发现工作台全部设置、作用域、生效时机、校验schema和操作方法；区分在线可写与本机维护入口。",
        module="configuration",
    ),
    "read_settings": ToolSpec(
        ReadSettings,
        "读取设置有效值/继承来源/版本及保存合同；不返回密钥正文或私有启动环境。",
        module="configuration",
    ),
    "preview_settings": ToolSpec(
        SettingsChange,
        "只读校验配置候选并预览变化，不保存、不连接服务、不安装或重启组件。",
        module="configuration",
    ),
    "configure_settings": ToolSpec(
        SettingsChange,
        "经当前审批保存已校验设置，复用模块服务/版本/凭据边界；返回持久回执，未知提交不盲重放。",
        True,
        "at_most_once",
        "configuration",
    ),
    "settings_receipt": ToolSpec(
        SettingsReceipt,
        "查询此前配置提交的真实持久回执；未知时先read_settings核对，不以新请求盲重发。",
        module="configuration",
    ),
    "write_command_input": ToolSpec(
        WriteCommandInput,
        "向本任务已批准的交互PTY命令输入真实字符或EOF；输入再次审核，同操作ID不会重复输入，未知结果先核对。",
        True,
        "at_most_once",
        "processes",
    ),
    "code_intelligence": ToolSpec(
        CodeIntelligence,
        "按受信配置运行短命代码分析器，查询能力/定义/引用/符号/诊断；位置从1起、列用UTF-16，不执行项目代码。缺服务器明确不可用。",
        True,
        "idempotent",
    ),
    "read_notebook": ToolSpec(
        ReadNotebook, "读取Notebook指定单元格，索引从0起；输出默认省略，不执行代码，保留版本。"
    ),
    "edit_notebook": ToolSpec(
        EditNotebook,
        "按版本替换/插入/删除/移动Notebook单元格，不执行代码，保留无关单元格、元数据和输出。",
        True,
        "conditional_write",
    ),
    "inspect_file_processors": ToolSpec(
        Arguments, "查看当前文件提取/预览/转录/视频抽帧等处理器的真实可用条件。", module="artifacts"
    ),
    "process_file": ToolSpec(
        ProcessFile,
        "按文件真实版本发起已有提取/预览/OCR/转PDF/视频抽帧/转录流程；复用文件Worker，本批工具后自动等待真实结果。",
        True,
        "idempotent",
        "artifacts",
    ),
    "cancel_file_task": ToolSpec(
        FileTaskStatus,
        "停止本任务创建的文件处理子任务，沿现有取消与资源回收流程处理。",
        replay_policy="idempotent",
        module="artifacts",
    ),
    "read_plan": ToolSpec(
        Arguments, "读取当前任务持久计划、版本和真实步骤状态；无计划时版本0。", module="plan"
    ),
    "update_plan": ToolSpec(
        UpdatePlan,
        "按版本更新多步骤任务计划；稳定步骤ID，最多一个进行中。步骤完成须有实际依据，不能替代执行结果。",
        replay_policy="idempotent",
        module="plan",
    ),
    "find_files": ToolSpec(
        FindFiles, "按相对glob模式定位授权工作区文件，例如**/*.tsx；支持分页，保护目录不可绕过。"
    ),
    "read_files": ToolSpec(
        ReadFiles, "批量读取最多12个文件的指定行，逐文件返回真实版本、错误及续读位置。"
    ),
    "edit_files": ToolSpec(
        EditFiles,
        "按预期版本批量精确编辑；先校验全部文件，再逐文件原子写入，返回每项真实结果；不承诺跨文件原子事务。",
        True,
        "conditional_write",
    ),
    "inspect_media": ToolSpec(
        Arguments, "查看已接入的图像、视频、音频及3D生成模型；未接入类型不可生成。", module="media"
    ),
    "read_media_guide": ToolSpec(
        MediaGuide, "读取所选生成模型提示词指南和统一调用流程。", module="media"
    ),
    "generate_image": ToolSpec(
        GenerateImage,
        "通过已接入模型生成或编辑图像，立即返回持久任务；主任务结束前会等待生成任务。",
        True,
        "idempotent",
        "media",
    ),
    "generate_video": ToolSpec(
        GenerateVideo, "通过用户已接入的视频模型提交持久生成任务。", True, "idempotent", "media"
    ),
    "generate_audio": ToolSpec(
        GenerateAudio,
        "通过用户已接入的语音/音频模型提交持久生成任务。",
        True,
        "idempotent",
        "media",
    ),
    "generate_3d": ToolSpec(
        Generate3D, "通过用户已接入的3D模型提交持久生成任务。", True, "idempotent", "media"
    ),
    "media_task_status": ToolSpec(
        MediaTarget, "查询本任务生成进度和真实产物，保留同一run_id；不要重复提交。", module="media"
    ),
    "cancel_media_task": ToolSpec(
        MediaTarget,
        "取消本任务的生成任务，真实资源回收后才标记清理结束。",
        replay_policy="idempotent",
        module="media",
    ),
    "view_media_output": ToolSpec(
        MediaObservation,
        "查看实际生成文件或抽帧的安全预览/元数据；本地图像模型实际读图，其他格式须先提取对应内容。",
        replay_policy="idempotent",
        module="media",
    ),
    "list_skills": ToolSpec(SkillList, "查询本任务已授权且冻结版本的Skill目录。", module="skills"),
    "read_skill": ToolSpec(
        SkillResource,
        "按skill_id读取SKILL.md或包内文本资源，无需审批，保留固定版本和来源。",
        module="skills",
    ),
    "prepare_skill": ToolSpec(
        SkillTarget,
        "把Skill固定版本导出到当前工作区，供后续工具读取或执行；不运行脚本，不覆盖修改。",
        requires_approval=True,
        replay_policy="idempotent",
        module="skills",
    ),
    "present_file": ToolSpec(
        PresentFile,
        "为用户展示真实文件：指定工作区path或artifact_id，返回可直接点击的版本化Markdown链接；图片预览由文件Worker处理。",
        replay_policy="idempotent",
        module="artifacts",
    ),
    "start_command": ToolSpec(
        StartCommand,
        "按当前审批策略启动长命令并立即返回持久句柄；时限继承任务预算，可显式缩短。"
        "配合read_command_output、stop_command查看进度或停止，不要重启同一命令。"
        "不是OS沙箱；并发上限见list_commands；满额时先查看占位，不要重复启动。"
        "任务结束会停止未结束命令。",
        requires_approval=True,
        replay_policy="at_most_once",
        module="processes",
    ),
    "list_commands": ToolSpec(
        Arguments,
        "核对当前任务全部活动命令与实际关闭回执，返回command_id、占位原因、"
        "每任务/本机并发上限和剩余名额；只公开其他任务的占用数量，不停止命令。",
        replay_policy="idempotent",
        module="processes",
    ),
    "read_command_output": ToolSpec(
        ReadCommand,
        "按返回的next_offset继续读取同一command_id的真实日志与状态。"
        "eof仅表示当前日志尾；以command.owned_resources_closed及exit_code确认结束，"
        "未结束时继续观察，不得重启命令或声称完成；完整结束日志另存产物。",
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
        "读取电脑操作总开关、实际原生能力和本任务会话；开启后首次使用会按需启动执行器。",
        module="automation",
    ),
    "browser_open": ToolSpec(
        automation.BrowserOpen,
        "电脑操作开启后创建可访问公网的独立浏览器；不继承用户登录，须通过操作审核。",
        requires_approval=True,
        replay_policy="at_most_once",
        module="automation",
    ),
    "browser_observe": ToolSpec(
        automation.BrowserObserve,
        "读取当前页面正文和元素引用；下一动作last_observation必须取返回的observation_id，不能用action_id。",
        requires_approval=True,
        replay_policy="at_most_once",
        module="automation",
    ),
    "browser_action": ToolSpec(
        automation.BrowserStep,
        "先browser_observe，再复制最新observation_id；navigate的target_ref固定为page并只传url。元素动作使用观察中的e数字引用。智能模式自动审核，未知结果不重放。",
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
        "电脑操作开启后打开桌面观察/控制会话，可操作不同窗口；模式固定，控制持有单写租约并经过审核。",
        requires_approval=True,
        replay_policy="at_most_once",
        module="automation",
    ),
    "computer_observe": ToolSpec(
        automation.SessionTarget,
        "审核后捕获真实桌面与几何；动作复制本次observation_id/generation/geometry_id/target_window_id。切换窗口后重新观察。",
        requires_approval=True,
        replay_policy="at_most_once",
        module="automation",
    ),
    "computer_action": ToolSpec(
        automation.ComputerStep,
        "按最新截图和当前窗口执行原生输入，坐标为原图像素，缩略图坐标须换算。智能模式自动审核。",
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
    "list_conversations": ToolSpec(
        ListConversations,
        "分页列出当前用户授权范围内的其它对话，支持标题/时间/归档筛选；默认当前工作区。scope=all须用户明确开启，子Agent限当前任务树。",
        module="conversations",
    ),
    "search_conversations": ToolSpec(
        SearchConversations,
        "在授权对话标题或可见消息中按关键词搜索，先过滤权限/资料限制再匹配；每对话一个片段，next_before非空时仍需翻页，不声称全量检索。",
        module="conversations",
    ),
    "read_conversation": ToolSpec(
        ReadConversation,
        "按thread_id分段读取其它对话可见正文；message_id从该消息开始，返回next可按消息/字符续读。max_chars为总预算，工具结果须显式include_tools；不恢复原任务或自动加载图片。",
        module="conversations",
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
    "read_rules": ToolSpec(
        ReadRules,
        "直接读取已登记的用户全局、项目和相关子目录AGENTS规则，不需审核；paths只填工作区相对路径，不要用read_file猜测私有全局规则路径。",
        module="rules",
    ),
    "search_files": ToolSpec(
        SearchFiles, "搜索工作区文本，支持字面/正则、glob、大小写、前后文和续读游标；命中保留行号。"
    ),
    "write_file": ToolSpec(
        WriteFile,
        "条件创建或替换文本文件；existing文件须使用read_file版本，"
        "新文件expected_version=missing。通过当前审批策略后执行。",
        True,
        "conditional_write",
    ),
    "apply_patch": ToolSpec(
        ApplyPatch,
        "在指定版本文件中精确替换唯一一段文字，通过当前审批策略后执行。",
        True,
        "conditional_write",
    ),
    "exec_command": ToolSpec(
        ExecCommand,
        "通过当前审批策略后运行命令，保存日志与进程归属，等待真实结束。"
        "时限继承任务预算，可显式缩短；预计耗时或需即时查看进度时，"
        "先discover_tools加载start_command、read_command_output、stop_command使用持久句柄。",
        True,
        "at_most_once",
    ),
    "ask_user": ToolSpec(
        AskUser, "向用户询问必要信息，支持选项或自由输入；等待真实提交。", module="interaction"
    ),
    "discover_tools": ToolSpec(
        DiscoverTools,
        "names明确加载工具；query/module定向搜索会自动加载本页最多6项可用目标，无过滤时仅列目录；按next_offset续页。保留已有工具。"
        "卸载或替换须显式指定action=remove或replace。新工具schema仅在下一轮模型请求提供，"
        "本轮不能同时调用尚未加载的工具。记忆、网络工具按需加载。",
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
        ArtifactList,
        "按文件名检索/分页列出当前工作区文件的来源、类型和版本；使用next_before继续。",
        module="artifacts",
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
        "派生独立子任务；最多3个未释放名额、深度1，指定目标/交付/工具/文件范围。继承用户预算，共享模型排队；可先派遣独立任务再继续自己的工作。",
        replay_policy="idempotent",
        module="collaboration",
    ),
    "inspect_agent_models": ToolSpec(
        Arguments,
        "列出当前用户模型连接及各自支持的思考档位/模板，不含地址或密钥；派遣不同模型前查询。",
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
        "主子双向发送持久消息；子任务用agent_id=parent向主任务提问或汇报。note/question/reply在模型安全边界送达；instruction调整活动子任务。消息可唤醒等待，不会给已完成任务开启新轮，续办用followup_agent。",
        replay_policy="idempotent",
        module="collaboration",
    ),
    "wait_agent_message": ToolSpec(
        WaitAgents,
        "等待自己的新协作消息，释放Worker和模型许可；子任务提问后等待父回复。超时返回真实消息状态，不等同收到回复。",
        replay_policy="idempotent",
        module="collaboration",
    ),
    "followup_agent": ToolSpec(
        AgentFollowup,
        "向已有子Agent追加工作：活动轮按instruction改向，完成或中断且资源已核实关闭后开启新轮，同一身份和对话历史保留。不可越过暂停、关闭或未知资源。",
        replay_policy="idempotent",
        module="collaboration",
    ),
    "interrupt_agent": ToolSpec(
        AgentTarget,
        "中断子Agent当前执行轮，保留身份、对话和结果；实际清理完成后可followup_agent继续。发出停止请求不等于资源已关闭。",
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
    "read_rules",
    "list_skills",
    "read_skill",
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


def definitions(
    names: list[str] | None = None, *, command_timeout_seconds: int = MAX_COMMAND_TIMEOUT_SECONDS
) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": TOOLS[name].description,
                "parameters": tool_schema(name, command_timeout_seconds),
            },
        }
        for name in (names if names is not None else CORE_TOOL_NAMES)
    ]


def tool_schema(name: str, command_timeout_seconds: int) -> dict:
    schema = compact_schema(TOOLS[name].schema.model_json_schema(mode="serialization"))
    if name in {"exec_command", "start_command"}:
        field = schema["properties"]["timeout_seconds"]
        for choice in field["anyOf"]:
            if choice.get("type") == "integer":
                choice["maximum"] = command_timeout_seconds
        field["description"] = (
            f"单条命令执行时限（秒），本任务允许1～{command_timeout_seconds}；"
            "省略或null继承此任务预算，不额外缩短。"
        )
    return schema
