# 工具系统、权限边界与故障恢复技术规格

1.0.0 当前已实现85个内置工具，覆盖文件、命令、配置、历史、协作、记忆、浏览器与生成等分类；发现可用性仍依赖当前连接、组件和权限。最新接口范围及真实证据见[状态第22节](../implementation-status.md#22-2026-10-26-跨对话读取与-ai-操作说明)。当前AI操作流程与完整工具名称索引见[AI说明书](../ai-guide.md)。`read_workbench_guide`提供固定公开说明的版本化分段读取；`list_conversations/search_conversations/read_conversation`提供用户实时允许范围内的对话目录、关键词定位及按消息/字符续读。默认当前工作区，账户跨工作区须用户设置，子Agent限任务树；来源片段再次注入前复核，不自动读图或启动原任务。

> 本文保留工具系统的完整设计约束，当前目录、参数和可执行能力以 `modules/tools/registry.py`、DTO 及相应模块说明为准；尖括号仍为占位符。已有实现、审查修复和实际工具验收见[状态清单](../implementation-status.md)，规划中的通用恢复接口不自动成为现有 HTTP 路由。
>
> 适用基线：单人、多工作区、局域网访问，预留多人身份边界；React / TypeScript / Vite + FastAPI + PostgreSQL，LangGraph 是唯一 agent 运行时；Linux、Windows、macOS 原生部署设计，DGX Spark ARM64 / 128GB 统一内存优先实测，其余平台逐项验收。Docker 仅是个别执行沙箱的可选项。

2026-10-19工具补齐实现（下文拟定名称仍是设计术语，实际调用用下表）：

| 实际工具 | 新增行为与限制 |
| --- | --- |
| `discover_tools` | 按用途/模块查询和分页；筛选查询自动增量加载当前页最多6个尚未加载的可用schema，纯目录查询只读；显式名称加载及replace/remove仍保留，返回真实可用条件及检查入口 |
| `find_files` / `search_files` | glob定位、字面量/正则、大小写、上下文及游标；沿用工作区/规则/符号链接边界与真实rg预算 |
| `read_file` / `read_files` | 明确起止行、长行字符偏移、版本化续读；批读共用输出预算 |
| `edit_files` | 按文件版本先检查完整批次，再在已有文件锁下逐项原子写；不承诺跨文件事务，异常返回实际已完成部分 |
| `list_artifacts` / `read_artifact` | 名称过滤、稳定分页、长行字符续读和内容版本；真实Artifact ID与任务ID严格区分 |
| `read_plan` / `update_plan` | 持久步骤、版本和当前步骤；聊天展示，压缩后继续注入 |
| `inspect_file_processors` / `process_file` / `cancel_file_task` | 复用已有提取/OCR/转写/转换，自动等待file Worker后交真实结果；缺组件如实不可用 |
| `code_intelligence` | capabilities、定义、引用、符号、诊断；TS/JS复用本地TypeScript，通用LSP仅用可信设置中的命令；当前Ruff提供Python诊断，Python定义/引用仍缺服务器 |
| `read_notebook` / `edit_notebook` | 按单元格读取、版本化插入/替换/删除/移动；保留无关内容，不执行Notebook内核 |
| `mcp_list_resources` / `mcp_list_resource_templates` / `mcp_read_resource` | 绑定连接与快照、分页和内容版本；URI不扩大本地/网络权限，正文作为不可信内容 |
| `start_command` / `write_command_input` | 可选Linux PTY、24×80、合并输出及受审输入/EOF；复用命令日志/预算/停止/完整回收，未知输入不重发；bwrap交互明确不支持 |
| `list_commands` | 核对当前任务活动命令、并发上限及剩余名额；其他任务仅提供占用数量；默认每任务2/整机4，未知资源继续占位 |
| `discover_settings` / `read_settings` / `preview_settings` / `configure_settings` / `settings_receipt` | 设置目录、当前值、只读候选校验、受控保存与回执；范围及专用维护入口见[配置接口](ai-configuration.md) |

以上已通过本机真实浏览器、固定本地Qwen及实际Worker链路验收，具体证据和组件边界见[状态第17节](../implementation-status.md#17-2026-10-19-工具补齐设置接口与交互改进)。旧工具操作重放保留原已批准参数，不因新增默认字段改变摘要。文件处理在创建时原子设置父/根任务，父任务授权事件记录在父Run，完成后交还真实产物。

2026-10-19命令预算实现：`exec_command`与`start_command`统一使用冻结的`RunOptions.command_timeout_seconds`（默认3600秒，可配置1..86400）。工具省略/null继承，显式时限不能超任务预算；工具schema同步声明实际上限，审批前归一化，旧操作重放不延长已审时限。普通命令等待真实终态，需即时句柄/进度的任务复用`start_command`、`read_command_output`与`stop_command`；按同一ID和游标观察，不重跑，日志EOF不等于进程结束。合法活动态轮询使用嵌套命令回执判断；参数校验仅反馈安全字段和范围，不回显输入值或任意未知字段名。运行超时、取消与结果未知仍保持现有资源清理和不重放边界。

2026-10-20命令并发与停止增量：`exec_command`和`start_command`共享整机准入，限额由应用配置控制。满额在准入事务外核对一次真实关闭回执，仍满额时反馈`command_capacity`及当前任务占位、统计、处理指引和`not_started=true`，不隐式排队或自动重发。配置与资源判据见[命令模块](../../apps/server/src/workbench/modules/processes/README.md)。用户取消后，若剩余命令均为有效终态unknown且执行器已退出、无其他活动资源，则以`failed/resource_cleanup_unconfirmed`结束对话执行，明确显示资源待核对；未知命令仍保留账本与名额、继续独立对账并阻止数据删除。这不代表资源已关闭，不放开运行中或身份核验失败的资源。

## 目录

1. [定位与术语](#1-定位与术语)
2. [完整工具目录](#2-完整工具目录)
3. [统一 ToolSpec、ToolCall 与 ToolResult](#3-统一-toolspectoolcall-与-toolresult)
4. [ToolExecutor 执行流程与生命周期](#4-toolexecutor-执行流程与生命周期)
5. [权限、作用域、密钥与批准](#5-权限作用域密钥与批准)
6. [原生执行、沙箱与平台适配器](#6-原生执行沙箱与平台适配器)
7. [MCP 集成与协议兼容](#7-mcp-集成与协议兼容)
8. [全系统错误分类](#8-全系统错误分类)
9. [重试所有权、时间与资源预算](#9-重试所有权时间与资源预算)
10. [幂等、结果未知与对账](#10-幂等结果未知与对账)
11. [取消、崩溃与恢复](#11-取消崩溃与恢复)
12. [UI 反馈与事件契约](#12-ui-反馈与事件契约)
13. [关键场景与验收要求](#13-关键场景与验收要求)
14. [复用、实施边界与来源](#14-复用实施边界与来源)

相关文档：[总体架构](../architecture.md)、[运行时与数据契约](runtime-and-data.md)、[模型、上下文与记忆](models-and-context.md)、[文件、桌面与交互界面](files-computer-ui.md)、[部署与验证](deployment-and-validation.md)。统一实体与状态枚举以运行时与数据契约为准；变更时同步更新本章，不能让两套状态表示同一事实。

## 1. 定位与术语

### 1.1 工具系统负责把意图变成可确认的事实

模型决定“建议做什么”，后端决定“是否允许、如何执行、结果如何确认”。模型输出 shell 文本不等于获得执行权，模型说“文件已保存”也不能作为产品成功依据。必须经过真实执行、结果验证与持久化，才能让后续步骤依赖这一结果。

所有内置工具、文件处理器、浏览器、桌面执行端和 MCP 工具共用 ToolExecutor。统一的是权限、预算、取消、记录和错误语义，具体能力仍保留独立接口；不能把所有工具都退化成任意脚本执行。LangGraph 负责规划、等待和恢复，ToolExecutor 只承接逻辑工具调用，不再嵌入第二个 agent 循环。

### 1.2 核心概念

| 概念 | 含义 | 例子与边界 |
| --- | --- | --- |
| ToolSpec | 一种工具的版本化定义 | 文件补丁工具的参数、权限与输出 schema |
| ToolCall | 一次逻辑工具调用，表示具体意图 | 将指定文件改成拟议版本；重试仍关联原调用 |
| Operation | 一项需要去重、确认和对账的业务动作 | 提交一次补丁、创建一次远端对象 |
| Attempt | 对原意图的一次实际执行尝试 | 首次请求超时后重试，产生新 attempt |
| Request | 一次入口请求的关联与去重边界 | 前端提交创建 run，请求重传不能创建两个 run |
| 副作用 | 对外部状态的改变 | 写文件、启动服务、安装包、点击提交、发消息 |
| 幂等 | 重复同一操作不产生额外效果 | 带原版本条件将文件替换为确定内容；不是所有写都幂等 |
| 对账 | 查询动作是否已生效，并绑定真实证据 | 超时后查远端回执，或比对实际文件版本 |
| 租约 | 有期限、可续期的独占权 | 工作区自动写入权；租约过期不能自动杀死旧进程 |
| 适配器 | 将内部契约转换为具体系统调用 | subprocess、浏览器、MCP SDK、桌面代理 |
| continuation | 恢复一个尚未完成交互所需的关联状态 | MCP 需要输入后，继续原工具调用而非从头重做 |

实体沿用 `user_id / workspace_id / thread_id / run_id / step_id / attempt_id / tool_call_id / operation_id / request_id`。后端创建或校验这些实体的归属；模型不得自报用户身份、批准结果、预算余额或租约持有者。内部 tool_call_id 另行关联提供方原始工具调用 ID，不能假设不同模型给出的 ID 全局唯一。

### 1.3 数据流和责任边界

```text
LangGraph 节点：选择工具与参数、处理结果或进入等待
    ↓
工具注册表：可信定义、版本快照、适配器和策略绑定
    ↓
ToolExecutor：身份 → 参数 → 权限 → 预算 → 租约 → 执行 → 对账 → 收尾
    ↓
原生文件/进程、网络、浏览器、文件处理、桌面、MCP 适配器
    ↓
PostgreSQL：任务表、租约、操作账本、尝试、批准、问题、事件、检查点
    ↓
SSE：传输已经提交的事件；前端连接不是事实存储位置
```

首版用 PostgreSQL 任务表、租约和通知承接调度。通知只用于唤醒，丢失通知后仍通过任务表发现待办，不把通知当可靠任务队列。Valkey 可选，不强依赖 Celery 或第二个任务运行时。前端能力提示与后端预检共用同一能力/策略来源；禁用按钮不能代替后端权限检查。

## 2. 完整工具目录

### 2.1 组织与暴露规则

使用稳定命名空间，如 `files.read`、`process.start`、`web.fetch`、`browser.observe`、`memory.read`、`tasks.spawn`、`mcp.<connection_id>.<remote_tool>`。这些是拟定内部 ID；暴露给模型时按提供方命名规则做可逆映射。

完整目录不必全部放进每轮上下文。按任务暴露类别与简述，通过 `tools.search / tools.describe` 发现具体定义，再由注册表核验。工具发现可以按 ID、描述、标签和输入类型做文本检索，无需向量库。发现不等于启用，描述不等于授权；工具升级、权限变化或新 MCP 工具出现后重新预检。

每项能力显示 `available / unavailable / unconfigured / unsupported` 及原因。未安装的可选能力不能伪装成已可用；不能因为本地失败就自动上传资料、切付费服务或安装依赖。停用先处理已有调用和资源，再撤销新调用入口。

### 2.2 文件、代码与多格式处理

| 拟定工具 | 输入和输出 | 必须处理的边界 |
| --- | --- | --- |
| `files.list / files.stat` | 相对路径、分页、隐藏文件策略；类型、大小、版本、截断提示 | 不默认遍历整机，不跟随越界符号链接 |
| `files.read` | 文件 ID/路径、行或字节范围、编码；内容、行号、版本 | 大文件分段；二进制转交处理器，不能乱码读取后声称完成 |
| `files.search` | 文本/正则、glob、最大结果数；路径、行列、上下文 | 优先 `rg`；限制范围、时间、输出；零结果是成功 |
| `files.write` | 目标、内容/制品引用、预期旧版本、覆盖策略 | 自动写租约；已有文件默认要求版本，不盲写 |
| `files.apply_patch` | 明确补丁与基准版本；实际 diff、新版本或冲突位置 | 先校验完整补丁，不将失败片段模糊套用到相似位置 |
| `files.mkdir / copy / move` | 源、目标、覆盖与递归策略 | 两端分别校验；跨盘 move 不是原子重命名 |
| `files.delete` | 精确列表、版本、递归标记 | 删除独立授权；不得将未展开的大范围 glob 当作确定批准 |
| `files.diff` | 两个文件、版本或 worktree | 只读；区分用户与 agent 修改，不因此自动回退 |
| `files.extract` | 原文件引用、页/表/区域、解析选项 | 返回文本/结构、来源定位、警告、缺失页；读取不等于保真编辑 |
| `files.preview` | 文件、页/区域、预览格式 | 独立制品，不改原件；预览可能与原软件显示不同 |
| `files.export` | 内容结构、目标格式、输出位置 | 新建副本优先；标注转换损失、不可编辑元素与未验证格式 |

解析、预览、创建、编辑、导出是不同能力。Docling 等负责抽取结构，Office/PDF 的写入与保真编辑交给格式专用工具；不因能读 DOCX 就声称可无损修改所有版式。宏、外链、嵌入可执行对象不随预览自动运行。OCR、音视频处理及旧 Office 转换按可选依赖与平台能力预检，详见文件章节。

路径校验不能只对字符串做 `startswith`。规范化工作区根与目标，处理大小写、符号链接、挂载点和平台路径语义；新文件根据父目录检查，不因尚不存在而跳过。尽可能使用目录句柄、不跟随链接等系统能力，减少检查后目标被替换的竞态。归档解包拒绝 `../`、绝对路径和越界链接，并限制展开文件数、总大小与嵌套层数。

Git 查看状态、差异、日志可以只读执行；checkout、reset、clean、merge、rebase、commit、push 都是写操作，不能只看命令名 `git` 就判只读。修改前读取现有内容和未提交变更，保留用户工作。无 Git 的工作区仍可做文件版本和 diff，但不能假装拥有 worktree 与仓库历史。

### 2.3 进程、终端与开发验证

| 拟定工具 | 关键契约 | 边界 |
| --- | --- | --- |
| `process.run` | argv、cwd、secret/env 引用、超时、输出上限；退出码与日志 | 优先参数数组，避免 shell 插值；cwd 在授权范围 |
| `process.start` | 同上，另有长期运行策略；返回受管资源 ID | 启动即有副作用，绑定 operation/run，支持查询和清理 |
| `process.poll` | 进程资源 ID、日志游标；增量输出和状态 | 查询不重启；按 offset 去重 |
| `process.stdin` | 受管 ID、输入、敏感标记 | 输入可能确认删除或执行命令，属于写；敏感内容不回显 |
| `process.stop` | 资源 ID、优雅退出宽限期 | 停止受管进程树，重复停止返回当前状态 |
| `process.list` | 工作区/run 范围 | 不暴露无关主机进程的机密参数和环境 |
| `terminal.open / resize` | PTY 会话、尺寸、生命周期 | PTY 只提供终端语义，不赋予额外权限 |

构建、测试、lint、格式化和包管理复用项目脚本，经 `process.*` 执行，不为每门语言另造执行器。测试失败是可供 agent 分析的有效失败结果，不应因非零退出码就原样重复三遍。进程启动失败、进程崩溃和测试断言失败分别记录。

任意 shell 文本走单独声明的 shell 模式。shell、Python、Node、编译器都可能读写文件、联网和派生进程；不能以第一个命令名判断权限。批准展示解释器、完整实际命令、cwd、影响范围与外部连接，密钥只显示名称。下载依赖使用默认源，不因网络故障自动换镜像或安装未授权组件。

进程输出提供前端实时片段、模型有限摘要和完整日志制品。超限标记 `truncated` 与日志引用，不能静默丢失；大日志不进入每条数据库事件。后台进程由监督器确认结束，不依赖浏览器连接。Windows 使用平台受管进程树机制，POSIX 使用进程组等能力，不能假设所有平台都有同一信号与 PTY 行为。

### 2.4 搜索、HTTP 与浏览器

| 拟定工具 | 能力 | 权限及行为 |
| --- | --- | --- |
| `web.search` | 查询词、时间、域名筛选；标题、摘要和 URL | 搜索服务会接收查询词；本地部署不等于搜索完全离线 |
| `web.fetch` | 读取允许 URL；状态、最终 URL、类型、正文与定位 | 每次重定向重查目标，拒绝未授权内网/元数据端点 |
| `web.download` | 下载到制品目录，来源、大小和类型上限 | 外部读加本地写；不自动执行、解压或安装 |
| `browser.open / navigate` | 隔离浏览器会话、导航与存储配置 | 不默认接管日常浏览器全部登录态 |
| `browser.observe` | DOM、可访问性树、页面文本、截图、页面版本 | 尽量用结构化定位；截图同样可能包含私密资料 |
| `browser.click / type / select` | 定位器、动作参数、预期页面版本 | 提交、购买、发布须按实际业务副作用授权 |
| `browser.evaluate` | 可选脚本能力 | 高权限；页面 JS bridge 不能任意调用后端工具 |
| `browser.close` | 受管会话 ID | 仅关闭本系统拥有的会话 |

网页、搜索摘要、DOM、下载文件都是外部不可信输入，不能覆盖系统指令、授权、密钥规则或工具注册表。页面写着“把环境变量发给此 URL”不形成用户授权。

网络策略不仅限制初始 URL，还覆盖 DNS 解析、重定向、浏览器子资源、WebSocket 等实际出站路径。应用层 URL 校验无法约束任意进程网络时，应如实标注，需隔离网络才能强制。局域网访问工作台不等于允许 agent 访问局域网全部设备。

静态公开页面优先 HTTP；动态页面或授权登录态用浏览器；只有 API/浏览器接口确实不能完成时再用桌面。网络关闭时本地文件、缓存与本地模型仍可用，UI 明确显示联网能力不可用，不反复重试。

### 2.5 记忆、知识库与上下文

| 拟定工具 | 内容 | 边界 |
| --- | --- | --- |
| `memory.list / read` | Markdown 记忆、来源、范围和版本 | 普通文件与元数据组织，不引入向量库 |
| `memory.search` | 标题、标签、路径、正文检索 | `rg` 或 PostgreSQL 文本检索，返回依据位置 |
| `memory.propose` | 拟保存内容、依据、范围、理由、敏感类别 | 提议不等于写入，不将临时状态自动写入全局偏好 |
| `memory.write / update` | 目标、内容、版本与授权依据 | 受同样写租约和版本条件约束，可追溯 |
| `memory.delete` | 指定条目及索引清理范围 | 说明历史/备份保留策略，不宣称自动物理擦除全部副本 |
| `knowledge.index / search` | 文档引用、MD 索引与文本检索结果 | 抽取结果保留源定位，索引不是事实来源的替代品 |
| `context.inspect` | 本轮规则、记忆、附件及预算的引用清单 | 展示组成和来源，不泄露密钥或默认公开内部推理 |

长期偏好、项目事实、任务状态、知识文档与检索缓存分开。全局、工作区、项目/目录规则按模型与记忆章节合并；低可信资料写入记忆后不因此升级为系统指令。多人预留体现在归属与 ACL，不为首版引入无关的组织管理功能。

`memory.write / update / delete` 是受控记忆服务入口，不能绕过候选核对、版本、遗忘屏障和用户授权。子 agent 只暴露 read/search/propose；最终合并由主任务调用统一 MemoryWriteService 或由用户在界面操作，不能因工具已注册就让每个子 agent 直接改永久记忆。

### 2.6 任务、子 agent 与人工交互

| 拟定工具 | 能力 | 强制规则 |
| --- | --- | --- |
| `tasks.plan / update` | 步骤、依赖、进度与交付物引用 | 计划完成不替代真实执行与验收事实 |
| `tasks.spawn` | 子任务说明、读写归属、模型配置、预算、父 run | 最多3个未释放子agent身份，深度1；后端限制，不靠提示词 |
| `tasks.message` | 同任务树内部协作消息 | 不等于向外部人员发送邮件/即时消息 |
| `tasks.inspect / wait` | 查询、等待已有任务 | 等待释放生成槽位，不通过轮询反复创建子任务 |
| `tasks.cancel` | 停止子任务或任务树 | 传播到模型、工具、进程和资源 |
| `user.ask` | 澄清问题、选项、自由文本、版本 | 持久 question，进入 waiting_user；旧答案不能恢复新问题 |
| `user.request_approval` | 具体动作、目标、权限差额、有效期 | 持久 approval；与普通问题独立，模型不能自行同意 |

活跃子 agent 上限与模型生成并发独立。Spark 初始生成并发为 1；可有多个子任务等待资源，但不因此同时加载/生成 3 份大模型请求。计数口径遵循运行时契约；父预算覆盖全部子任务，子任务不各自得到无限额度。

当前协作工具为 spawn_agent、inspect_agents、send_agent_message、wait_agents、wait_agent_message、followup_agent、interrupt_agent/cancel_agent、release_agent。消息工具的 agent_id 支持主任务的子 agent UUID，以及子任务使用的 parent；后者不能用于控制或兄弟通信。普通消息仅投递到已有执行轮，不自动为已完成 agent 开新轮；明确 followup 才启动下一轮。需要回复时使用真实 reply_to_id。等待超时只报告真实状态，不视为收到答复；中断请求不等于资源关闭。会话允许多轮，但每个根任务仍最多3个未释放身份、深度1，共享根预算及原模型许可。

共享同一项目或底层目录的自动写入统一单写者，即使从不同 workspace 引用也不能绕开。键以规范化资源身份确定，不只看 workspace_id。子 agent 写代码优先独立 worktree，由主 agent 整合；无 Git 时排队。worktree 仍可能共享端口、数据库或桌面，需各自资源租约。用户手工编辑不受自动租约限制，提交前必须再次校验版本。

### 2.7 MCP 与桌面

MCP 提供工具/资源/模板等协议映射，不自动开放远端模型采样、用户询问或主机访问，见第 7 节。

桌面可提供 `desktop.observe / click / type / scroll / key / stop`。调用包含被控设备、桌面会话、观察版本、窗口上下文、坐标系；动作前检查观察是否过期，一个桌面只有一个自动写租约，始终提供停止入口。访问工作台网页的电脑不自动成为被控端，需用户授权的 companion。平台、Wayland、ARM64 和执行器限制见文件与桌面章节。

鼠标事件发送成功不等于业务成功；动作后重新观察并确认。表单提交后断线是可能已生效，不能自动再次点击提交。音频、OCR、视频抽帧等复用文件/模型适配器，不另建 agent runtime。

## 3. 统一 ToolSpec、ToolCall 与 ToolResult

### 3.1 ToolSpec：可信、版本化定义

内部用 Pydantic 验证并生成 JSON Schema，前后端类型从同一来源派生。注册表不接受远端指定任意 Python import 路径或执行类。

| 字段 | 含义和约束 |
| --- | --- |
| tool_id、version、schema_version | 稳定工具 ID、行为版本和结构版本；在途固定快照 |
| title、description、category | 有长度限制的用途、能力和限制说明 |
| input_schema、output_schema | 类型、必填字段、上限、允许值；拒绝危险未知参数 |
| adapter_id、adapter_version | 本地可信配置绑定实现 |
| execution_profile | native_scoped / os_sandbox / docker_optional / remote 配置引用 |
| required_scopes | 路径、网络、连接、进程、桌面等权限模板 |
| effect_class | read_only / local_write / external_write / process_control；按保守上界 |
| idempotency | read / conditional / remote_key / none；附前提与对账方式 |
| timeouts、limits | 排队、连接、空闲、尝试时限；字节/条数/容量上限 |
| retry_policy_ref | 统一重试策略引用，不允许插件隐藏无限重试 |
| concurrency_keys | 项目写、桌面写、连接/工具实例等资源键 |
| availability | 状态、平台/依赖证据、不可用原因 |
| sensitivity | 敏感类别、脱敏与保留策略 |
| capabilities | 是否支持进度、取消、对账、结构化/流式/多模态输出 |
| provenance | 内置来源或 MCP 连接、远端名、发现时间、快照 |

参数绑定的权限模板由可信后端解析，例如从 path 得出实际目标根。远端自称 read-only 不能直接变成本地可信授权。新增写能力、外部目标变化或 schema 变化，不沿用旧批准。

### 3.2 ToolCall：意图与执行上下文分离

模型只提供工具选择与 arguments，后端补身份、固定版本、策略、预算和资源。内部记录至少包含：

```text
ToolCall（设计示意）
  tool_call_id, operation_id, request_id
  user_id, workspace_id, thread_id, run_id, step_id
  tool_id, tool_version, tool_spec_snapshot_id
  arguments_ref, arguments_digest, redacted_summary
  resolved_scopes, policy_revision, approval_id?
  deadline_at, budget_reservation_id?, retry_policy_ref
  status, created_at, updated_at
```

实际参数引用受 ACL 控制；摘要值用于同键输入一致性，不是要求全项目哈希审计。批准绑定规范化参数、目标与权限，不能只绑定一句自然语言描述。排队期间改变参数必须重新形成意图或批准。

### 3.3 ToolResult：终态事实

```json
{
  "schema_version": "<结构版本>",
  "tool_call_id": "<逻辑工具调用ID>",
  "operation_id": "<操作ID>",
  "attempt_id": "<尝试ID>",
  "status": "succeeded",
  "content": [{"type": "text", "text": "<有限事实摘要>"}],
  "structured": {"<按output_schema定义的字段>": "<值>"},
  "artifacts": [{"artifact_id": "<制品ID>", "role": "<输出或日志>"}],
  "effects": [{"target_ref": "<目标>", "state": "confirmed"}],
  "error": null,
  "usage": {"elapsed_ms": "<实际耗时>", "output_bytes": "<实际字节数>"},
  "truncated": false,
  "started_at": "<UTC时间>",
  "finished_at": "<UTC时间>"
}
```

示例仅说明结构；实际数字必须为相应数值类型。最终 status 为 `succeeded / failed / cancelled / outcome_unknown`。`created / running` 是执行中的 attempt 状态，排队与等待不强塞进最终结果。ToolCall、attempt 和 run 状态分别记录，不能混用。

错误统一为 `kind / code / message / retryable / retry_after_ms / details_ref`。message 是可向用户显示的安全信息；原始响应、栈和私密参数在受限详情中。retryable 只是候选条件，仍需幂等、授权和预算判断。

content 可含文本、图片、音频与资源引用。大文件使用 artifact_id，不将 base64 复制进事件、日志和 prompt。引用带所有权、媒体类型、大小和权限；渲染不自动执行 HTML/JS。structured 不符合 schema 时不能标完整成功；保留原制品并返回 OUTPUT_SCHEMA_INVALID。

effects 逐项标记 `confirmed / not_applied / unknown`。批量复制部分完成时列出已完成、未执行与未知项，不整体报成功，也不从头重放整个批次。若存在尚未确认的副作用，最终 status 优先使用 outcome_unknown；无未知项但未完成整体目标用 failed，并保留已确认结果。原子工具不支持部分成功时，要在提交前完成整体检查。

### 3.4 进度和额外输入不是最终成功

日志、解析页数、页面截图、模型 token 属于进度信号，包含 attempt_id、stream、offset、有限 payload/引用和时间，按 offset 去重。

MCP input_required 转为持久化 question/approval 与原 tool_call 的 continuation。等待是中间信号，不返回业务 succeeded；run 进入相应等待状态，释放生成槽位和可释放租约。后续沿原 tool_call/operation 继续，不能重做此前已产生副作用。同一次逻辑执行沿用 attempt_id，以 execution_phase=awaiting_input 标记暂停；每轮传输另记 exchange_id、序号与结果。收到补充输入请求不是执行成功，也不计故障重试；只有实际故障导致重新尝试才按策略新建 attempt。详细粒度以运行时契约为准。

## 4. ToolExecutor 执行流程与生命周期

### 4.1 完整流程

1. **固定定义。** 查工具和版本快照，检查停用、平台、依赖与连接能力；不可用时说明原因，不能让模型猜名字。
2. **校验归属。** 验证 user/workspace/thread/run/step 关系，不能靠客户端传另一个 workspace_id 读取其制品或进程。
3. **校验输入。** schema、长度、文件、真实目标、主机、连接、模态与格式预检。参数错误不消耗实际执行次数。
4. **解析权限。** 从目标计算具体所需权限，检查任务授权、策略、profile、外发和密钥。需要批准先持久化并等待，尚不提交副作用。
5. **检查去重。** 同 operation 同参已有确定结果则复用；同键不同参拒绝；已有 unknown 先对账。
6. **预算与排队。** 申请模型/工具/项目写/桌面等资源，保存可恢复排队记录；等待批准不占模型槽位。
7. **二次预检。** 获租约后重读目标版本、批准有效期与剩余 deadline，防止等待期间条件改变。按固定次序或原子申请资源集合，避免死锁。
8. **记账后执行。** 在事务中登记 attempt、操作阶段、租约和开始事件；提交成功后才向外部发送副作用。不持数据库长事务等待进程/网络。
9. **实际调用。** 适配器收到取消信号、截止时间、最小环境、资源句柄、输出接收器、去重键；不能隐藏重试。
10. **确认与对账。** 校验输出和副作用，收集日志、用量、制品。写请求发出后断线不判成未执行。
11. **提交完成事实。** 事务更新 attempt、operation、结果引用与事件；成功落库后才让下游依赖。
12. **清理释放。** 停止应停止的子进程、关闭临时资源、释放预留和租约。旧写入者未确认停止/隔离时，不把写槽位给新调用。

前置拒绝、有效业务失败和系统故障分别计数。agent 收到测试失败后修改代码形成新步骤，是业务推进；执行器对同一请求原样重试，是基础设施重试，两者不得混为一谈。

### 4.2 并行与生命周期

无依赖纯读可以在资源限额内并行；要求一致快照时固定文件版本。写、批准、对账、重试和依赖结果的步骤顺序推进。重型 OCR、浏览器、解析也消耗 UMA/CPU，不能因不调用模型就绕过全局资源限制；不需要 GPU 的等待不占生成槽位。

工具实现提供 probe、execute、cancel、inspect，支持时提供 reconcile、cleanup。注册表管理配置、发现、启用、不可用、停用和卸载。在途固定旧版；紧急禁用时明确取消/等待，不能无提示换实现。动态发现只更新候选元数据，本地策略保持可信。恢复健康需通过有界检查，不让每个 run 无限重连。

## 5. 权限、作用域、密钥与批准

### 5.1 有效权限取交集

```text
身份/工作区访问权 ∩ 用户任务授权 ∩ 工作区策略
  ∩ 执行 profile 可实际强制的限制 ∩ 当前批准 ∩ 子任务更窄范围
  = 本次有效权限
```

子 agent 只能继承或缩小父权限。计划、网页、MCP 描述、模型文本和记忆不是授权来源。AGENTS.md 按全局/项目/目录来源合并，提供任务与开发约束，不突破用户权限或主机隔离。

首版即使单用户也有真实会话鉴权与工作区访问校验；LAN 可访问不能等于无鉴权。来源检查、跨站请求保护和下载 URL 的权限按架构规定实施，不把浏览器提交的 user_id 当身份。预留多人不是首版实现组织管理，而是避免把全部资源设计成无所有者。

| 作用域 | 至少表达 | 边界 |
| --- | --- | --- |
| 文件 | 允许根、读/写/执行、精确目标/受限目录、递归策略 | 当前项目不包含 ~/.ssh、其他项目和浏览器配置 |
| 进程 | 程序/解释器、cwd、shell、环境引用、生命周期 | “允许测试”不等于任意主机写；OS 能力才可强制约束 |
| 网络 | 关闭/允许列表/开放、协议、主机、端口、内网策略 | 访问域名不等于允许发送任意数据 |
| 连接 | connection_id、账户、权限、操作类别 | 读取云盘不等于写入、分享或访问另一账户 |
| 桌面 | 设备、会话、窗口/应用范围、动作 | 窗口变化需重观察，不能继续向错误窗口输入 |
| 外发 | 数据类别、目标、用途、大小 | 外部模型、搜索、MCP 分别检查 |
| 资源 | 时间、token、费用、内存、并发、输出 | 子 agent 共用父预算 |

### 5.2 网络与密钥分离

允许 API 域名不代表可读所有密钥；允许使用某密钥不代表可将其作为普通文本交给模型或另一个站点。密钥通过 secret_ref 由适配器临时解析，不进 Markdown、prompt、事件、普通日志或前端状态。外部进程只注入本次必需变量，禁止继承整个后端环境。

凭据到期返回 AUTH_EXPIRED，只更新对应连接；没配置返回 AUTH_MISSING，不扫描用户目录“找 token”。脱敏是补救，优先从源头不记录秘密。外部浏览器 profile、cookie、下载和桌面截图同样按敏感数据管理，不能只保护 API key。

### 5.3 批准的对象必须具体

批准记录至少有 approval_id、归属、run/operation、请求版本、动作摘要、规范目标、权限差额、参数摘要值、profile、外发说明、有效期、决定和时间。支持仅本次与用户明确选择的受限规则，不能默认记住无限范围。

先做可逆准备：提交前给实际 diff 和分支，发布前给制品和目标，桌面输入前给设备/窗口。当前任务已明确授权且范围不变的动作不重复弹窗；超范围才申请新增权限并解释具体策略来源。批准期间 waiting_approval，释放生成槽位和可释放租约；拒绝后使用已授权替代方案或结束，不能改写命令规避拒绝。

批准过期、目标版本变化、连接身份变化、profile 降级时重新预检，必要时重新批准。澄清 question 与权限 approval 独立，不能用普通问题的回答隐式授予高风险权限。批准是允许执行，不是证明已经执行。

## 6. 原生执行、沙箱与平台适配器

### 6.1 工作目录限制不等于 OS 沙箱

cwd 只决定相对路径起点，普通原生进程仍可能读取绝对路径、联网、启动子进程和写同用户其他文件。Python 层文件路径校验仅约束经过该 API 的动作，不约束 shell 内部系统调用。UI 必须展示实际执行模式，不把 native_scoped 称为强隔离。

| profile | 实际作用 | 限制 |
| --- | --- | --- |
| native_scoped | 工具参数/路径/环境约束、受管进程、普通用户权限 | 对任意命令不构成 OS 隔离 |
| os_sandbox | 平台已验证的文件/网络/进程隔离与资源限制 | Linux/Windows/macOS 机制不同，不能只检测命令存在 |
| docker_optional | 单项容器执行、受限挂载/网络 | 不要求整个工作台 Docker 化；不挂主机根、Docker socket 或任意设备 |
| remote | 指定服务/companion 的远端执行 | 远端的隔离审计能力需单独声明 |

原生部署优先不排斥个别不可信工具可选沙箱。业务只依赖 ExecutionAdapter 能力，不依赖特定隔离产品。Linux 可评估用户命名空间、挂载/网络限制与 cgroups；Windows 可评估受限令牌、Job Object 等；macOS 采用其实际可维护的执行隔离方案。上述是候选机制，不承诺同等能力或已实现；不足的功能标记 unavailable/monitor-only，不能移植一串 Linux 参数假装三平台支持。

### 6.2 适配器接口与能力探测

probe 返回平台、架构、版本、文件/网络隔离、取消、资源限制、可对账程度和不可用原因。execute 接受固定上下文；cancel 终止受管动作；inspect 查询真实状态；reconcile 和 cleanup 明确是否支持。

项目与制品目录分离，沙箱只挂必需内容。导出主机前再次检查路径、类型与大小，不能让容器输出链接指向主机任意位置。资源限额区分真正硬限制与监测阈值；不能称“保证最多使用某内存”却只有周期采样。

Spark 的 ARM64 二进制、内核配置、用户命名空间与图形栈要部署实测，其余平台逐项验收。当前规格不构成已验证安装。工具不支持的平台不阻塞其他独立功能。

### 6.3 不允许无提示降级

要求 os_sandbox 而创建失败，返回 SANDBOX_UNAVAILABLE，列明原因与可选下一步：修复配置、换可用隔离后端、或用户理解后选择原生限制模式。不能在主机悄悄重跑相同命令。

网络规则、只读挂载、隔离权限建立失败时应在副作用前拒绝。执行中失效则停止新增动作，尽力终止/隔离已有执行，报告可能影响。profile 变化改变风险，旧批准不自动覆盖降级。

## 7. MCP 集成与协议兼容

### 7.1 接入层和配置

工作台作为客户端接入已配置 MCP 服务，工具成为 ToolSpec 候选，经本地策略启用后才暴露给模型。MCP 不取代 LangGraph，不重定义 run、预算、批准或子 agent 调度。

连接配置包括稳定 connection_id、名称、transport、命令/URL、secret_ref、账户/权限、允许工具、兼容模式、连接/调用超时、资源/输出上限和启用状态。stdio 服务本身执行本机代码，需与进程一样受控；HTTP 服务受网络与认证策略限制。

### 7.2 新旧协议必须同时适配

本次核对日期 2026-10-17。官方 Python SDK 区分两代：2026-07-28 使用 server/discover，旧版使用 initialize；auto 可发现并回退，legacy 用于需要旧式服务主动请求等路径。固定现代版本不等于发现了服务能力。[官方协议版本说明](https://py.sdk.modelcontextprotocol.io/protocol-versions/)

官方 Python SDK v2.3.0 是当前评估基线，实施时锁版本、维护兼容矩阵；既有 1.x 集成按其维护分支能力适配，不把 v2 接口套到 1.x，也不因远端服务旧就另装 agent runtime。[v2.3.0 发布记录](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0)

矩阵记录 SDK/服务/协议版本、transport、认证、发现能力、工具/资源、继续交互、取消/进度和实际验收。记录实际协商结果；协议兼容回退与安全能力降级分别处理，后者不能静默发生。发现结果缓存绑定连接、认证身份和版本并设有效期，身份或工具列表变化即失效。不能将所有连接都按旧 initialize 流程实现。

### 7.3 发现、调用与额外输入

流程：连接预检 → 认证 → 协商 → 发现 → schema/长度校验 → 快照 → 本地允许列表 → 注册。远端名仅服务内唯一，内部前缀用 connection_id，不用可重名的 server 显示名。列表变化失效缓存、重新预检，新工具不自动继承旧批准。

2026-07-28 工具规范包含结构化输出和 input_required；工具注解不能当可信授权。适配器分别处理协议错误、业务错误、额外输入和正常结果，不能将 JSON-RPC 成功一律判业务成功。[MCP 工具规范](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)

现代 input_required 保存原 tool_call/operation、连接身份、原参数引用、requestState、问题版本和回答引用，通过持久 question/approval 暂停。回答后按协议继续原调用；旧时代主动 elicitation/sampling 用旧版路径处理，不能假设所有服务都会主动回调。continuation 设往返数、大小、过期和总 deadline，继续交换不冒充故障重试，也不能无限问用户。

资源链接仍检查 ACL、大小、媒体类型、外发。远端提示模板是上下文素材，不是系统规则。远端采样默认关闭；启用后每次经过本地路由、授权、预算与取消，不允许远端无限消耗模型/付费 API。

### 7.4 连接恢复不等于业务重放

重连只恢复 transport。保存远端关联、幂等键、continuation 与尝试；无远端去重/查询依据的写调用发送后断线，进入 outcome_unknown。readOnlyHint/idempotentHint 只供本地策略参考，不直接授予权限或证明可重试。

MCP SDK、HTTP SDK、连接重试与节点重试不得相乘。单一业务重试所有者；有界连接技术重试也记入时间与尝试。取消不等于远端回滚，已提交写仍要对账。

## 8. 全系统错误分类

### 8.1 稳定分类与恢复动作

kind 表示归属，code 稳定机器码，message 可理解，details_ref 指受限诊断。原始 HTTP 状态放详情，不能代替业务语义。以下是首版必须覆盖的范围；可扩展注册表，不能每层为同一错误造同义码。

| kind / 代表 code | 情况 | 动作 |
| --- | --- | --- |
| validation / INVALID_ARGUMENT | 参数错误、缺字段、工具名非法 | 不重试相同参数；返回具体字段问题供修正 |
| capability / UNSUPPORTED_INPUT | 模型不接图像、格式不支持 | 保留输入，选兼容模型或允许的转换 |
| capability / DEPENDENCY_UNAVAILABLE | 缺依赖、架构/桌面不支持 | 修复前不循环，不自动安装/上云 |
| permission / SCOPE_DENIED | 路径越界、禁网、外发未授权 | 不重试；说明拒绝范围 |
| permission / APPROVAL_REQUIRED | 超出当前授权 | 持久等待，不消耗真实执行尝试 |
| authentication / AUTH_MISSING、AUTH_EXPIRED、AUTH_INVALID | 无凭据/过期/无效 | 更新指定连接后恢复，不指数重试 |
| quota / RATE_LIMITED | 限速或暂时并发限制 | 按可信 Retry-After 有界等待 |
| quota / INSUFFICIENT_CREDIT、HARD_QUOTA_EXCEEDED | 余额/月度额度耗尽 | 不当短期限速，等待处理或合法替代 |
| resource / MEMORY_PRESSURE、RESOURCE_BUSY | UMA 不足、GPU/写/桌面占用 | waiting_resource；先减负，不原样无限重试 |
| budget / DEADLINE_EXCEEDED、TOKEN_BUDGET_EXCEEDED、COST_BUDGET_EXCEEDED | 总预算不足 | 停新增调用，保留已有成果 |
| network / CONNECT_FAILED、DNS_FAILED、CONNECTION_RESET | 连接不可达/断开 | 纯读或确认未发送可有界重试；已发写对账 |
| timeout / CONNECT_TIMEOUT、IDLE_TIMEOUT、EXECUTION_TIMEOUT | 连接、无进度、执行超时 | 按阶段和副作用判断，不等于未执行 |
| upstream / SERVICE_UNAVAILABLE、UPSTREAM_FAILURE | 外部 5xx、服务暂不可用 | 有界重试，换路线需预检 |
| model / CONTEXT_LIMIT、MALFORMED_TOOL_CALL、INVALID_STRUCTURED_OUTPUT | 上下文超限、参数/输出无效 | 压缩或受限修复计预算，不执行猜测参数 |
| conflict / VERSION_CONFLICT、LEASE_LOST、STALE_OBSERVATION | 文件变化、失租约、旧页面/桌面 | 重读重规划，不覆盖/盲点 |
| execution / EXIT_NONZERO、PROCESS_CRASHED、TOOL_BUSINESS_FAILED | 断言失败、崩溃、业务拒绝 | 交 agent 分析，不靠原样重试修代码 |
| output / OUTPUT_LIMIT、OUTPUT_SCHEMA_INVALID、ARTIFACT_INVALID | 超限、schema 错、制品缺损 | 保留证据，标部分结果/失败阶段 |
| sandbox / SANDBOX_UNAVAILABLE、ISOLATION_FAILED | 隔离不可用/失效 | 拒绝或停止，无静默主机执行 |
| persistence / DB_UNAVAILABLE、CHECKPOINT_FAILED、RESULT_COMMIT_FAILED | 意图/结果/检查点不能落库 | 不新增不可记录副作用；恢复后对账 |
| protocol / VERSION_UNSUPPORTED、SCHEMA_MISMATCH、REMOTE_PROTOCOL_ERROR | 协议不兼容 | 受控适配或停连接，不无限重连 |
| integrity / OPERATION_KEY_CONFLICT、UNEXPECTED_STATE | 同键不同参、非法迁移 | 拒绝并诊断，不清数据重跑 |
| unknown / OUTCOME_UNKNOWN | 写已发但结果未确认 | 冻结冲突写，先对账/人工确认 |
| cancelled / USER_CANCELLED、PARENT_CANCELLED | 用户/父任务取消 | 不重试，收尾并保留事实 |
| internal / INTERNAL_ERROR | 未分类程序错误 | 安全说明和诊断引用，不默认可重试 |

### 8.2 容易误判的情况

HTTP 429 可能表示限速，也可能是余额/硬额度；按提供方正式错误码解析，无法区分就报告原因未确认，不能快速循环请求。HTTP 200 + MCP isError 或进程非零退出不算完整成功；零搜索结果、空文件和测试发现失败则有具体业务含义。

OOM 不等于三个子 agent 一起重试：先减实际并发、回收可确认资源，不足就等待或选择已授权配置。用户拒绝、模型安全拒绝、服务政策拒绝不靠自动轮换提供方绕开。metrics/trace、页面连接故障不改变业务事实，不能触发重写文件或重提交表单。

## 9. 重试所有权、时间与资源预算

### 9.1 每个调用只有一个业务重试所有者

| 层 | 负责 | 不得 |
| --- | --- | --- |
| 前端/API 客户端 | SSE 重连、同键重传、查当前状态 | 断线就新建 run/重新发写 |
| LangGraph | 下一步、节点恢复、模型策略 | 整体重放含多个副作用的节点 |
| ToolExecutor | 工具分类、预算、尝试、对账 | 允许适配器隐藏重试 |
| 模型路由/适配层 | 一次模型交换及受控模型重试 | SDK + 路由器叠加重试/隐式付费 fallback |
| MCP/HTTP SDK | 一次可观察交换 | 无限重连或不记尝试 |
| 进程监督器 | 查询、停止、回收 | 心跳过期就启动同一写命令 |

ToolExecutor 拥有工具重试，模型路由器拥有模型重试，外层节点不再重复包同一调用。若某单元使用 LangGraph RetryPolicy，则它成为唯一所有者，其他层关闭自动重试并统一记账。

默认瞬态失败最多 **2 次额外重试，总计 3 次尝试**，同时受全局 deadline、费用/token 预算限制。这是产品策略，不是假定 SDK 默认就正确。LangGraph 的当前节点超时/错误处理存在版本条件，官方文档要求 langgraph>=1.2；实施核对锁定版本。节点内状态回退不意味着外部文件/API 副作用回滚。[官方容错说明](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)

### 9.2 重试判断顺序

```text
取消/总预算耗尽？ → 停止
可恢复瞬态错误？否 → 明确失败或等待配置
有副作用或可能已提交？ → 幂等查询/对账，不直接重放
参数/目标/批准/模型能力仍有效？否 → 重新预检
次数、剩余 deadline/token/费用足够？否 → 结束
登记 retry_scheduled → 可取消退避 → 新 attempt → 执行
```

指数退避加随机抖动，基础间隔/上限由工具和提供方配置，不给所有服务同一个值。有可信 Retry-After 优先考虑，但不超剩余 deadline。退避可响应取消，不用不可中断长 sleep 卡 worker。

转码、输出修复、重新规划、切模型都计预算，不借新 step ID 绕总上限。未配置授权费用时不发起未授权付费调用。

### 9.3 预算字段与结算

至少有 deadline_at、queue_timeout_ms、connect_timeout_ms、idle_timeout_ms、attempt_timeout_ms、max_attempts、输入/输出 token、外部费用、日志/制品容量。进程内间隔用单调时钟，跨重启截止时间持久化 UTC。配置了 deadline_at 时，它表示绝对截止时间，包含排队、退避和人工等待，暂停或重试均不延长。

需要排除人工等待的执行时长限制使用 active_execution_budget，另以 wait_expires_at 管理等待有效期；若同时设置 deadline_at，仍受绝对截止时间约束。不得用同一个字段混合两种计时口径。到期后的停止、终态和迟到答案处理统一按[Run 状态机](runtime-and-data.md#3-run-状态机)，等待不长期占用可释放资源。

预算先预留、后按实际用量结算、释放差额；断线可能已计费则记估计值与 usage_unknown，不记零，有后续证据再修正。子任务都计入父预算，父额度耗尽禁止新增调用。

### 9.4 流式输出不能拼成无缝重试

流中断后，第二次生成不能直接接在第一段尾部当同一答案。各 attempt 和消息版本独立；第一段标“生成中断，可能不完整”，新尝试完成后明确最终版本。

只有完整解析、验证并被调度接受的工具调用才执行。半个 JSON 或未闭合调用不猜测补齐；中断时冻结未完成调用。已执行调用查账去重，不能因为重新生成相似回复再执行。

fallback 重新检查模态、上下文、tool calling、结构化输出、外发与费用。纯文本替代图像模型不能静默丢图并称等价回答；不满足时保留附件并询问合法选择。

## 10. 幂等、结果未知与对账

### 10.1 三层去重

1. 入口 request/幂等键：创建 run、回答、批准、取消重复提交只得到原对象/当前状态。
2. 逻辑 operation_id：重试/恢复不变，绑定规范化参数和目标；同键不同参返回 OPERATION_KEY_CONFLICT。
3. 外部去重：服务支持则传稳定幂等键并记录作用域/有效期；不支持则用明确查询/回执对账。本地有 ID 不会令远端写自动幂等。

不能承诺任意外部系统恰好执行一次。应做到尝试可追溯、能去重则去重、不确定则暂停并确认。补偿删除/撤销也是新副作用，需权限、幂等与可理解后果，不假设能无风险回滚。

### 10.2 操作账本

```text
Operation（字段示意，枚举见运行时契约）
  operation_id, workspace_id, run_id, tool_call_id
  tool_spec_snapshot_id, normalized_input_digest, target_refs
  effect_class, idempotency_mode, remote_key?, remote_receipt_ref?
  execution_phase, outcome, reconciliation_state
  latest_attempt_id, result_ref?, confirmed_effects_ref?
  created_at, last_checked_at, resolved_at?
```

阶段说明提交前/已提交/已确认事实，不替代 attempt 终态。unknown 是用户可见真实状态；依赖该写结果的步骤暂停，无依赖的只读工作可继续。操作键用数据库唯一约束，登记提交后执行；外部完成与落库之间仍可能崩溃，所以先登记是为了可恢复，不是消除所有不确定性。

### 10.3 分类对账

| 操作 | 证据 | 恢复 |
| --- | --- | --- |
| 文件读/搜索 | 无副作用，必要时固定版本 | 有预算重读，说明版本可能变 |
| 条件写/补丁 | 原版本、预期新内容、目标现状、提交记录 | 已为预期则确认；仍为原版才考虑重试；第三种内容是冲突 |
| 追加 | 外部去重键或每条 operation 标记 | 无标记不重复追加，先检查已有内容 |
| 创建远端对象 | 幂等键、回执、按操作查询 | 查到即绑定；查不到考虑一致性窗口，不立即断言没创建 |
| 启动服务 | supervisor ID、启动时间、归属 | 防 PID 复用；端口空闲不能证明命令没执行 |
| 桌面提交 | 新截图/DOM、业务回执 | 不确定就停同类动作，不再点一次 |
| 批量写 | 逐项 operation 子记录 | 只恢复未执行项，未知项分别确认 |
| MCP 写 | 服务支持的键/查询/回执 | 无证据保持 unknown，不靠重连确认 |

文件替换优先同文件系统暂存，在受管写入锁内检查版本后提交；不将这一步称为对任意外部编辑器都成立的文件系统条件替换。需要崩溃持久性时正确 flush/fsync 并说明平台限制。跨文件、跨卷和任意多进程写不能假装数据库事务；外部编辑的竞争边界见[文件与数据库一致性](runtime-and-data.md#7-文件与数据库之间的一致性)。对账适配器负责底层事实，不能让模型凭描述猜原子性。

### 10.4 人工解决未知结果

UI 显示“此动作可能已生效，尚无足够证据确认”，列目标、最后阶段与证据；按场景提供重新检查、附证据确认已完成、确认未生效后重做。不能给语义含糊的通用重试按钮。

用户决定重做非幂等操作，保存决定及后果，建立关联新 operation，保留原 unknown，不篡改成失败未执行。人工确认与系统证据区分，用户输入不能伪造自动回执。

## 11. 取消、崩溃与恢复

### 11.1 取消必须包括收尾

```text
登记取消 → run=cancelling → 停排队/新提交
  → 传播子任务、模型、适配器、受管进程
  → 优雅退出 → 到宽限期终止进程树/隔离执行端
  → 收集已发生副作用和未知项
  → 清理后释放租约/槽位 → cancelled
```

取消 API 成功只表示接受请求，真正停止前显示正在停止。不能先释放槽位而旧进程仍写。外部不可取消时说明“已停后续步骤，外部动作可能仍在进行”，随后对账。

取消保留已完成文件/日志，不自动撤销用户可能依赖的修改。只清理由系统拥有的临时资源，不按宽泛进程名杀用户程序。回退另走权限和版本校验。

### 11.2 重启接管顺序

1. 先连 DB，加载未完成 run、租约、question/approval 与操作账本，不把所有 running 直接变重试。
2. 根据心跳、租约、supervisor 与执行端归属判断存活，使用递增接管代次防双 worker；不能仅凭浏览器断开接管。
3. 未提交动作重新预检；已完成复用；已提交但无确定结果进入 unknown 并对账。
4. 之后恢复 LangGraph checkpoint 和结果，继续未完成步骤，不整体重放已有副作用节点。
5. 待问题/批准保留 ID/版本；目标或策略变了就作废并解释。等待不占生成槽位。
6. PTY 不保证跨重启存活；可查询后台服务重新关联，不能仅凭旧 UI ID 假装恢复。

租约过期不等于旧 OS 进程停止。受管进程先停/隔离，远端靠 fencing/幂等或暂停冲突写；无安全接管证据就继续等待，不能启动第二个写入者。

LangGraph 中断可能重入节点，因此询问/批准与副作用拆节点，或由账本保证重入不重复写。恢复 checkpoint 不自动保证远端调用一次。[官方中断约束](https://docs.langchain.com/oss/python/langgraph/interrupts)

### 11.3 存储和观测故障

**DB 不可用：** 未记意图不启动副作用；结果无法提交不向后续步骤声称成功。已有进程尽力停止/隔离，受限本地 spool 可保临时日志，不能作为脱离账本继续执行的第二数据库。恢复后按原 operation 对账补记，不新建 run 从头重跑。

**制品存储失败：** 解析成功但导出缺失，不算完成交付。中间结果保留，恢复导出不重复前面外部写；临时文件、提交完成、可下载状态分开。

**SSE 断线：** 后端按策略继续；重连读持久状态/事件，页面刷新不重建 run。提交结果未知先按 request 查询。

**metrics/trace 故障：** 有限缓冲或丢弃遥测并告警，不回退业务、不重放工具。数据库业务审计与可丢性能遥测分开。trace 默认只含关联 ID、耗时、大小、错误码、版本和脱敏摘要，不要求原始私密文件、prompt 或模型内容。

## 12. UI 反馈与事件契约

### 12.1 始终可理解的状态

用户应知道当前步骤、工具/模型、等谁、是否已有副作用、能否停止和怎样恢复，不能长期只显示转圈。

| run 状态 | 显示 | 动作 |
| --- | --- | --- |
| queued | 排队原因/资源类别 | 取消 |
| running | 步骤、工具、有限进度、日志 | 取消、展开详情 |
| waiting_user | 持久问题、选项/自由文本、版本/有效期 | 提交一次回答、取消 |
| waiting_approval | 目标、diff/外发、权限差额及策略来源 | 批准/拒绝 |
| waiting_resource | GPU/内存/项目写/桌面占用原因 | 取消、查占用；不虚构准确等待时间 |
| cancelling | 正停止及仍需确认的外部动作 | 查看收尾 |
| succeeded | 制品、完成事实和验证依据 | 打开结果/新任务 |
| failed | 失败点、已完成内容、可恢复条件 | 针对性恢复 |
| cancelled | 已停止、保留修改与未知项 | 查看/对账 |

操作级 unknown 即使 run 终止仍可访问、醒目呈现，不藏开发者控制台。区分代码已修改、辅助检查通过和真实环境验收。

### 12.2 事件封套

通用事件信封、类型表和 seq 语义统一引用[运行时事件契约](runtime-and-data.md#9-事件与流式输出)，本章只补充工具专用 payload。状态与事件同事务提交，DB 为事实源，SSE 只是传输；step.finished 的 payload 携带实际结果，适配器不另发同义业务状态。

payload 带相关 step/attempt/tool_call/operation ID、脱敏摘要和结果引用，不含完整文件。日志按 stream/offset，模型文本按 attempt/消息版本，防止重试混拼。

### 12.3 重连、去重与 API 语义

全部 API 以 `/api/v1` 开头，覆盖 run 创建/查询/取消、事件订阅、问题回答、批准、operation 查询/对账、工具目录与制品读取；具体路由以运行时契约为准，本文不宣称已存在。

SSE 重连带已确认游标，重新鉴权后补发。前端按 event_id 或 run+seq 去重，遇缺口补拉；保留窗口过期时读当前快照与游标再订阅。重复传输是允许情况，不能假设网络恰好一次。

创建响应丢失沿用原 request/幂等键查询/重传，不生成新键。回答和批准带 ID、版本、request_id，重复同答返回原结果，不同答冲突。取消幂等，终态再次取消返回现态。HTTP 202 代表接受异步工作，不能显示已完成；人工等待不保持一个超长同步 HTTP 请求直到浏览器超时。

### 12.4 易懂的错误和安全渲染

先告诉用户事实与下一步，详情可展开。例如：“文件在等待期间被修改，补丁尚未应用；已保留拟议差异，读取新版本后可重新生成。”比裸 409 更可执行。重试显示原因/次数，额度区分限速与余额；部分成功列范围。复制诊断默认只含 ID、时间、版本、码和脱敏配置。

系统提问、批准和业务动作使用受控组件及动作白名单。可选模型 HTML/JS 走独立渲染路径，不可直接调用 Executor、读 secret 或伪装系统批准；问题、批准、日志、制品链接来自后端验证契约，详见[生成页面的边界](files-computer-ui.md#95-可选-htmlcssjs-隔离渲染)。

## 13. 关键场景与验收要求

以下保留2026-10-17制定的工具与故障验收设计。1.0.0 已有多轮真实浏览器、本地Qwen与实际Worker链路证据，完成范围和仍未覆盖的故障条件见[状态清单](../implementation-status.md)；本表不作为全部已通过的声明。复用已有框架，按实际风险验证，不因文档同步重新运行有效的旧检查。

通用创建去重、SSE 恢复、模型能力不匹配和平台覆盖统一使用[真实 UI 核心验收矩阵](deployment-and-validation.md#113-真实-ui-核心验收矩阵)中的 U05/U06、U09 及对应平台记录；下表仅补充工具与恢复的具体故障证据。

| 场景 | 必须行为 | 证据 |
| --- | --- | --- |
| 临时 5xx | 最多两次额外重试/总 deadline | attempts、退避事件 |
| 429 限速/余额 | 正确区分，不无限请求 | 正式错误映射 |
| 写已发后断线 | unknown，先对账 | 外部次数/回执/页面 |
| 提交前发现批准目标已被修改 | 冲突，不覆盖，保留候选差异 | 实际内容、版本、diff |
| 两 child 同项目写 | worktree 或排队 | 规范资源键、租约 |
| 沙箱失败 | 无静默主机执行 | profile、进程、拒绝 |
| 取消长进程 | 先停进程树再释放槽位 | 监督记录、文件状态 |
| 崩溃/旧 worker 复活 | 安全接管无双写 | 代次、归属、账本 |
| DB 执行前/后故障 | 前不提交，后对账 | 阶段和外部实际状态 |
| metrics/trace 故障 | 不重放副作用 | 调用数/业务记录 |
| MCP 新旧与额外输入 | 正确协商、持久继续原调用 | 版本、continuation、页面 |
| 模型流中断 | 独立尝试，不重复工具 | 消息/attempt/去重 |
| 恶意网页/秘密输出 | 不越权、不外发密钥 | 策略/网络/脱敏记录 |

UI 最终验收在真实页面操作创建、刷新、回答、批准、取消和恢复；脚本/API 只辅助。缺 Spark、桌面设备、登录或真实服务时，交付已授权实现与辅助检查，明确“等待真实环境验证”，不凭模拟响应宣布完整能力可用。

## 14. 复用、实施边界与来源

### 14.1 复用与自定义的范围

LangGraph 是唯一任务图/中断/恢复运行时，PostgreSQL 保存任务、租约、检查点和操作；账本补齐框架不负责的外部副作用。文件和搜索优先标准库、受控 API、rg，进程复用原生监督能力；浏览器、解析用成熟库，不重新自研底层引擎。官方 MCP SDK 封装协议差异，本地保持稳定契约。

DeepSeek Harness 可借鉴注册表、插件生命周期和状态分离。官方为 developer preview、MIT；快速变化及第三方依赖许可分别评估。不导入其 Cordis 成为第二运行时，不声称兼容全部插件。[官方仓库](https://github.com/deepseek-ai/deepseek-harness)

ToolSpec、权限、操作账本需本产品定义，因为现有底层库不会替本工作台统一项目单写、Spark 预算、批准、未知副作用和页面恢复。自定义跨工具契约，底层解析/浏览器/协议继续复用。

### 14.2 实施依赖顺序

1. 注册表、契约、账本、权限、文件/进程适配器、统一错误、有界重试：先让一次本地任务可记录/取消/恢复。
2. 模型预算与 attempt、真实 UI 事件恢复、持久 question/approval、冲突/unknown 的用户路径。
3. 文件处理、搜索/浏览器、MD 记忆/知识库、子 agent、MCP，全部复用治理链。
4. 桌面和可选隔离按平台实测启用；不将安装 Docker、控制桌面设为全部产品的前置。

这是工程依赖顺序，不削减已确认需求。每阶段分别报告规格/代码/辅助检查/真实验收实际状态，不因文档写完宣布功能已实现。不额外扩展插件市场、远程集群或企业组织功能。

### 14.3 权威来源

核对日期 2026-10-17；来源支持框架/协议事实，本文产品字段与默认策略是设计，不是来源项目替本工作台实现的能力。

| 来源 | 范围 |
| --- | --- |
| [LangGraph Fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance) | 重试、超时、错误处理、版本前提 |
| [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | 中断重入、持久化与副作用约束 |
| [MCP 2026-07-28 Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) | schema、工具结果、额外输入、注解边界 |
| [MCP Python SDK Protocol versions](https://py.sdk.modelcontextprotocol.io/protocol-versions/) | 两代协议与模式 |
| [MCP Python SDK v2.3.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0) | 本次评估基线 |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | 插件参考、preview 和 MIT |

文件、桌面、模型与 UI 的专门来源沿用相应章节，不重复安装或运行已调研系统。
