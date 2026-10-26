# 架构决策、开源复用与研究依据

设计基线日期：2026-10-17；当前应用版本1.0.0（2026-10-26）。本文保留架构决定、原研究快照和历史文档验收；当前AI操作见[说明书](ai-guide.md)，最新工具/设置与真实证据见[状态第22节](implementation-status.md#22-2026-10-26-跨对话读取与-ai-操作说明)。用户确认、本方案采用、上游声明与本机验收分别记录，版本更新不扩大未实测平台或组件的能力。

## 1. 已确认需求

整体目标是本地可部署的 Codex 类工作台，并争取在任务完成体验、可调节性、透明度和维护性上做得更好。用户确认了 Web 形态、模块化、LangGraph、最多 3 个子 agent、无向量库、Markdown 记忆、本地与远程模型、可调能力、缓存与数据看板、工具/沙箱及全系统恢复、文件和电脑控制、交互式提问与智能 UI。

2026-10-17设计阶段追加确认：首版单人优先，多工作区与LAN访问，预留多人边界；三平台原生运行设计，Spark优先实测；尽可能将已研究能力纳入本版；最终设计和验收由主agent亲自完成。1.0.0当前本机免密码、跨设备LAN及Windows/macOS验收限制仍按状态清单保留；这些目标不等于所有环境已通过。

## 2. 架构决策记录

每条决策说明采用方式、原因、代价和何时重新评估。改变决定时更新对应需求及技术文档，而非在某一适配器内添加永久特判。

### ADR-01 模块化单体与独立运行进程

**决定**：业务采用明确模块边界，共享一套后端发布物；API、RunWorker、模型和必要执行器按生命周期分进程。

**原因**：降低本地安装和维护成本，同时支持长任务、取消和故障隔离。**代价**：必须维护进程监督和内部模块边界。**重评条件**：文件处理等模块出现实际独立扩容、依赖冲突或发布需求时，再拆独立服务。

### ADR-02 LangGraph 唯一 agent 运行时

**决定**：使用 LangGraph 图、中断和持久化机制；DeepSeek Harness、OpenClaw、Open Intelligent UI 只借用设计和适合的外围能力。

**原因**：避免多套状态机、任务 ID 和重试循环互相嵌套。**代价**：工作台需要自己维护跨工具权限、操作账本和交互桥接。**重评条件**：现有运行时有经验证的核心缺口时再评估，不因另一个框架更热门就并装。

### ADR-03 PostgreSQL 为任务事实源

**决定**：业务、任务队列、事件、配额和检查点采用 PostgreSQL；通知可用数据库机制，Valkey 为可选加速。

**原因**：减少本地必装服务，保持事务内一致性。**代价**：任务领取和租约需明确设计，长期数据需维护。**重评条件**：真实队列负载或服务边界超过此模式时再引入独立队列，不预先强制 Celery/Redis 等多套基础设施。

### ADR-04 无向量库的记忆与知识资料

**决定**：Markdown 是长期记忆内容源，`rg`/路径/标题/关键词是初版检索基础，可选文本索引可重建。

**原因**：符合用户要求、可读可编辑、便于审计与迁移。**代价**：语义相似搜索不是强项，需要高质量索引和多轮精确检索。**重评条件**：用户明确修改范围且真实资料规模/检索效果证明需要其他路线时再评估。当前不引入 pgvector、Qdrant 或仅为向量 RAG 存在的框架。

### ADR-05 规则、记忆、任务摘要、检查点分离

**决定**：AGENTS.md 管用户规则，MEMORY.md/主题文件管长期知识，STATE.md 管当前目标与进度，数据库检查点管精确恢复。

**原因**：避免模型学到的事实变成强制规则，也避免可读摘要承担副作用幂等。**代价**：需要版本、来源、遗忘和跨存储恢复。**重评条件**：只有在职责仍清楚且减少真实维护成本时合并存储实现，不能合并语义。

### ADR-06 三个子 agent 与生成并发分别控制

**决定**：最多 3 个未释放子 agent 名额，深度 1；Spark 生成并发从 1 开始。

**原因**：子任务调度不等于复制模型或同刻推理；先保护单机资源。**代价**：部分子任务需要排队。**重评条件**：硬件实测后可调整生成并发；提高子 agent 硬上限或深度属于产品范围变更。

### ADR-07 模型能力绑定实际连接

**决定**：能力保存声明、部署、用户策略和证据，支持 unknown，输入/输出/工具能力独立。

**原因**：同名模型不同服务的功能可能不同，转换也不能冒充原生支持。**代价**：需要预检、测试和过期证据处理。**重评条件**：元数据来源升级仅改变导入适配器，不改变稳定内部 schema。

### ADR-08 统一工具执行与副作用账本

**决定**：所有工具经版本化注册表、权限、预算、租约、操作登记和结果持久化；仅有一个重试所有者。

**原因**：模型、MCP 和 UI 不得各自发起无法追踪的副作用。**代价**：多一些状态记录，但可解释失败、重复与恢复。**重评条件**：对纯读路径可做经过验证的优化，不绕过权限和归属。

### ADR-09 原生部署优先，隔离能力显式声明

**决定**：工作台无需整体 Docker；提供原生受限执行与可选 OS/Docker 沙箱适配器。

**原因**：降低新手安装与镜像获取成本，兼顾平台差异。**代价**：各平台的进程、权限和隔离能力需要分别验收。**重评条件**：特定模型或执行环境确实需要容器时按组件说明，不把它变成整套产品的硬依赖。

### ADR-10 持久交互优先于前端回调

**决定**：问题、答案和批准在后端持久化，LangGraph 恢复引用它们；AG-UI/A2UI 以适配器接入。

**原因**：刷新、断网或重启后仍需继续同一任务。**代价**：需处理问题版本、答案幂等、checkpoint 提交窗口。**重评条件**：可以替换 UI 库，不把唯一事实重新放回组件 Promise。

### ADR-11 智能 UI 分层

**决定**：固定 React 组件为基础，A2UI 可信组件组合为扩展，自由 HTML/JS 只有具备可验证隔离时才开放，当前禁用；可信组件不获得任意脚本和工具权限。

**原因**：同时获得交互能力、可维护性和执行边界。**代价**：不能允许模型任意访问主应用 JS 和密钥。**重评条件**：新渲染器必须通过同一权限、数据和恢复契约。

### ADR-12 文件处理能力拆分

**决定**：读取、预览、创建、编辑、导出分别声明；解析用 Docling 等成熟库，写入用格式专用库和可选 LibreOffice。

**原因**：避免“能转 Markdown”被误解为“任意格式可无损编辑”。**代价**：格式矩阵和保真验收较细。**重评条件**：针对真实需求新增解析/编辑适配器，不把所有文件都丢给桌面自动化。

### ADR-13 浏览器与桌面独立

**决定**：BrowserProvider 管浏览器；DesktopProvider 管被控设备和本机节点；复用 OpenClaw/CUA 的执行经验而非整套 agent 循环。

**原因**：权限、会话、平台和可靠性不同。**代价**：桌面需要单写者、观察版本和人工接管。**重评条件**：平台驱动升级可以替换适配器，不能静默扩大被控设备范围。

### ADR-14 缓存与看板使用真实口径

**决定**：前缀缓存优先，工具缓存按范围，完整回答缓存默认关闭；用量按 attempt 保存并区分未知。

**原因**：缓存优化必须服务真实时延和费用，而不是制造漂亮数字。**代价**：需要各提供方独立归一化。**重评条件**：新增指标先确定数据来源与口径，不直接把缺失字段变成 0。

### ADR-15 单人产品保留归属边界

**决定**：首版只有所有者、多工作区和 LAN 登录，资源始终绑定主体并检查授权。

**原因**：兼顾当前简单使用和未来扩展。**代价**：仍要维护认证、会话和资源检查。**重评条件**：用户明确要求多人协作后再引入成员角色、组织或 SSO，当前不做空壳功能。

### ADR-16 本版范围完整，组件按需安装

**决定**：搜索、文件、媒体、浏览器、桌面、多 agent、看板、记忆和智能 UI 都保留在本版阶段中；安装包不强制携带所有重组件。

**原因**：充分利用调研，同时适应 Spark 资源与新手安装体验。**代价**：能力状态和安装引导必须准确。**重评条件**：只有真实阻塞或用户调整范围才改变交付目标，不能把“按需启用”写成永久未实现。

## 3. 开源项目复用矩阵

Stars 为 2026-10-17 调研快照，表内使用近似值，未来会变化。它们用于衡量生态热度，不构成质量排序。仓库主许可证不自动覆盖所有模型权重、子包、商标或商业功能。

| 项目 | Stars 快照 | 在本方案中的角色 | 边界 |
| --- | --- | --- | --- |
| [SearXNG](https://github.com/searxng/searxng) | 约 38.5k | 首选自托管搜索连接 | AGPL-3.0；作为独立服务维护其许可和分发要求 |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | 约 85.7k | 网页抽取适配器 | Apache-2.0；选本地库路线，云服务并非默认依赖 |
| [Docling](https://github.com/docling-project/docling) | 约 69.0k | 文档解析和结构提取 | MIT 代码；OCR/媒体模型和额外依赖分别核对 |
| [OpenClaw](https://github.com/openclaw/openclaw) | 约 392.2k | 工具、设备节点和电脑控制设计参考 | 不嵌入第二个主运行时，不承诺兼容所有插件 |
| [CUA](https://github.com/trycua/cua) | 约 30.0k | 桌面执行驱动候选 | 总仓库与子目录许可分别核对，ARM64/Wayland 需实测 |
| [CopilotKit](https://github.com/CopilotKit/CopilotKit) | 约 38.1k | 交互模式和可选 UI 集成 | 仓库 MIT 不代表所有生产功能无商业限制 |
| [AG-UI](https://github.com/ag-ui-protocol/ag-ui) | 约 16.6k | agent 与 UI 的事件适配 | 不替代工作台任务持久化 |
| [A2UI](https://github.com/a2ui-project/a2ui) | 约 16.7k | 可信声明式 UI 描述 | Apache-2.0；按实际 renderer 版本接入 |
| [Open Intelligent UI](https://github.com/CopilotKit/OpenIntelligentUI) | 约 3.4k | 分层渲染与隔离桥接参考 | 新参考应用，不能照搬其默认云模型、内存检查点或示例数据 |

LangGraph、FastAPI、React、PostgreSQL、Playwright 等作为基础技术按其职责和已调查生态选用。本次没有为每个基础库重新统计 Stars，避免用不必要的数字刷新替代设计工作。

### 3.1 DeepSeek Harness 的具体借鉴

已有研究阅读其固定提交 `687f282e8625c6b8d11ff58211a90524be2e4317`。借鉴稳定服务接口、插件注册与清理、可读状态与模型历史分离、工具结果与 UI 表示分离、子任务先预留后执行、检查点与上下文压缩的生命周期。

工作台另外补齐多 Worker 的数据库名额、外部副作用账本和持久 UI 交互，不能用上游进程内状态直接替代。上游 developer preview 的接口演进不应成为内部公共 schema 的版本。[DeepSeek Harness 官方仓库](https://github.com/deepseek-ai/deepseek-harness)。

### 3.2 智能 UI 需要保留的事实边界

本方案借鉴公开的生成式 UI 技术，不声称已经掌握 ChatGPT 专有 Intelligent UI 的内部实现。

Open Intelligent UI 的公开参考应用展示文本、组件和隔离 HTML/JS 路线，默认依赖 OpenAI 与 TypeSafe/Jev 等配置，内存检查点不等于可重启的生产持久化。[公开发布说明](https://www.copilotkit.ai/blog/introducing-open-intelligent-ui)、[架构说明](https://github.com/CopilotKit/OpenIntelligentUI/blob/main/docs/architecture.md)。

CopilotKit 文档把生产直接 `selfManagedAgents` 集成列为 Enterprise Intelligence 功能，开发用 unsafe 选项不能当生产替代。采用开源 Runtime 仍需评估增加的 Node 服务与兼容成本，因此默认链路使用本工作台 React、后端持久交互和开放协议适配。[Self-managed agents](https://docs.copilotkit.ai/backend/self-managed-agents)、[Copilot Runtime](https://docs.copilotkit.ai/backend/copilot-runtime)。

A2UI renderer 专项表列出 React v0.9.1 稳定、v1.0 为计划支持；这比旧 README 路线说明更具体，实际安装时锁定对应版本。[A2UI renderers](https://a2ui.org/reference/renderers/)。

## 4. 关键事实与验证层级

1.0.0当前结果统一记录在[实现与验收状态](implementation-status.md)，最新证据截至2026-10-26；各历史阶段保留原日期和条件，避免本研究文档重复维护另一套完成度。

- 固定`Qwen3.8-Flash-Next-NVFP4`已在Spark实际加载；当前131072上下文、MTP及单推理许可，128K真实长输入、后续多轮协作与跨对话读取已有证据。文本、SSE、工具、图像、前缀缓存按各自部署和日期保留原始结果，历史8K/32K测量不改写成128K基准。原生视频及综合性能仍待验证。
- 实际文件路线使用已有专用库和可选原生组件；Crawl4AI、Docling、CUA、OpenClaw 是调研或未来适配参考，没有因此整套引入。
- MCP多轮表单与资源、持久问题、实际浏览器下载、受控生成界面、记忆候选/遗忘、真实缓存看板已有端到端结果；后续已实现插件/Skill、AI配置、交互PTY、文件工具、独立生成和本机FLUX、多轮主子协作及用户可控的跨对话读取。桌面按输入环境单独列示，不能从API成功推导应用收到了文字。
- 独立 Python/npm 锁、模型环境与各原生组件分别记录版本。Windows/macOS 真实设备、外部模型凭据、OCR 安全组件和部分发行条件仍缺失。

本章 Stars 和外部资料保留原调查日期与固定版本，不因本次文档同步刷新数字。已有模型完整性结论复用，不重复全量哈希。

## 5. 权威来源导航

### 5.1 运行时、工具和恢复

- [LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)：检查点和恢复基础。
- [LangGraph 中断](https://docs.langchain.com/oss/python/langgraph/interrupts)：人工参与与节点重入约束。
- [LangGraph 容错](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)：当前版本重试、超时与错误处理。
- [MCP 2026-07-28 Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)：工具结果、schema 和协议边界。
- [MCP Python SDK 协议版本](https://py.sdk.modelcontextprotocol.io/protocol-versions/)：当前和旧协议适配。
- [MDN SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)：事件 ID 与浏览器重连；工作台仍需自行实现服务端回放。

### 5.2 模型能力、缓存和用量

- [LangChain Model Profiles](https://docs.langchain.com/oss/python/langchain/models#model-profiles)、[models.dev](https://github.com/anomalyco/models.dev)：能力元数据导入参考。
- [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)：提供方工具调用契约，不能直接代表所有兼容模型。
- [OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)：请求限额和错误处理参考。
- [Claude API errors](https://platform.claude.com/docs/en/api/errors)、[Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)：独立提供方的错误和流式差异。
- [NVIDIA 模型仓库固定修订](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4/tree/fc694b54fb0174e0913e6adf86691ef85a4ead47)：与已下载资产对应；真实加载依据后续兼容矩阵。

### 5.3 文件、电脑与 UI

- [Docling 支持格式](https://docling-project.github.io/docling/usage/supported_formats/)、[安装](https://docling-project.github.io/docling/getting_started/installation/)、[音视频](https://docling-project.github.io/docling/usage/processing_audio_media/)：解析及可选依赖。
- [openpyxl 教程](https://openpyxl.readthedocs.io/en/stable/tutorial.html)：电子表格读写及保留边界。
- [OpenClaw Computer use](https://docs.openclaw.ai/nodes/computer-use)、[Browser](https://docs.openclaw.ai/tools/browser)、[Node host](https://docs.openclaw.ai/nodes/node-host)：节点与执行能力。
- [CUA 官方仓库](https://github.com/trycua/cua)：桌面驱动候选与各子包边界。
- [XDG RemoteDesktop Portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)：Linux 桌面权限参考。
- [AG-UI](https://docs.ag-ui.com/introduction)、[A2UI](https://github.com/a2ui-project/a2ui)：事件与声明式 UI 两层职责。
- [CopilotKit useHumanInTheLoop](https://docs.copilotkit.ai/reference/v2/hooks/useHumanInTheLoop)、[useInterrupt](https://docs.copilotkit.ai/reference/v2/hooks/useInterrupt)：公开交互模式。

## 6. 保留的验证与后续接入条件

1.0.0本机已完成范围见状态清单；以下事项按实际组件、平台和授权保留验证条件，不能提前填写假定答案：

1. S0已形成固定模型的实测引擎组合；当前上下文131072、MTP开启、整实例生成容量1。该配置已有真实长输入及后续产品流程，进一步扩容、原生视频和综合性能须另有证据。
2. 各平台确认原生发行包、PostgreSQL 获取方式、浏览器和桌面驱动的具体组合；失败只影响对应矩阵项。
3. 采用商业功能或新的外部服务前确认许可与用户配置，不把研究时的免费演示路线当正式依赖。
4. 用户已选择 Apache-2.0，仓库 LICENSE 已落实；第三方组件保留各自许可证，详见根目录 THIRD_PARTY_NOTICES.md。
5. 性能目标依据真实基线设定，运行验收记录由主 agent 实际执行和整理，不能根据本篇规格自动标通过。

## 7. 2026-10-17设计文档交付与验收记录（历史）

本节只记录当时设计文档交付，不代表1.0.0当前仍未实施。该轮交付范围为9份相互链接的需求与技术设计文件。主 agent 负责总体架构、需求、运行契约、模型/记忆/计量及本决策文件，亲自审阅和整合工具、文件/电脑/UI、部署验收章节。

2026-10-17 首轮文档验收已由主 agent 完成：

- 逐项检查 22 项需求的模块归属、实现阶段和验收映射，保留 16 项架构决定与 30 个计划真实 UI 场景。
- 亲自阅读全部辅助章节，统一 Run/Attempt/事件名称、MCP continuation 粒度、数据目录、配置作用域和 S0～S5 阶段。
- 补齐数据库未就绪时的本机设置入口、账号与密钥边界、备份与停止请求的协调、终态重试创建关联 Run、专业格式的明确支持边界。
- 检查 9 份文档的 63 处本地链接及章节锚点、代码围栏、编号和本轮修订契约，检查结果无错误。抽查关键上游依据；没有重复执行无关全量调查。

同日按用户要求，主 agent 亲自完成最终复核并修正具体问题：

- 幂等响应清理后保留最小去重记录，明确有效、过期和未找到的响应，防止旧请求重新执行。
- 补齐排队与等待状态的截止时间处理，区分用户取消、期限失败、执行收尾及迟到答案。
- 将 MD 文件并发保证限定为受管写入，明确外部编辑竞争窗口与仅生成候选的原件保护路径。
- 修正 iframe 网络与资源隔离的过度保证，写明自由脚本的启用条件、静态降级和页面恢复方式。
- 删除工具章节重复的通用事件结构和通用验收行，改为引用权威章节；澄清可选 Node Runtime 及测试样本授权边界。

对应 U05、U11、U15、U22 已同步修订。最终文档检查覆盖 9 份文件、71 处本地链接及章节锚点、22 项需求、16 项架构决定和 30 个计划真实 UI 场景，未发现结构或引用错误；修改差异及直接受影响的跨文档契约已核对。本次没有增加功能范围。

当时验收结论：该轮需求与技术文档交付完成，2026-10-17文档任务本身未执行产品安装、模型推理、真实UI或电脑操作；模型资产保持原状。30个真实UI场景在当时是后续计划。后续已执行部分及未覆盖条件以[实现状态](implementation-status.md)为准，不从本节历史结论推断当前产品状态。

## 8. 2026-10-18 界面收敛依据

本节补充实施阶段的界面决定；上一节仍是设计文档交付时的历史记录。目标用户是独立使用本地模型、可能不熟悉开发工具的人，常用路径为打开工作区、描述任务、必要时回答或授权、查看成果、继续修改。高级用户仍能调整模型与执行参数。

本轮核对当前官方说明，并查看了 OpenAI 公开的 Codex 浅色界面参考图；没有登录外部产品账号，也没有将本机截图发给远程模型。产品存在版本和灰度差异，参考交互原则，不假定所有用户所见界面相同。

| 官方观察 | 本项目的实施决定 |
| --- | --- |
| Codex 公开界面以侧栏、对话正文和一个输入框组成，参数集中在输入框附近。[公开参考图](https://developers.openai.com/images/codex/app/codex-app-basic-light.webp) | 保留已有浅暖色体系；对话、文件、记忆为常用入口，其余工具按需展开。任务参数放在输入框工具按钮内。 |
| 项目组织共享文件与对话，历史可继续访问。[Projects and chats](https://learn.chatgpt.com/docs/projects) | 最近对话始终可达，顺序与服务端一致；筛选明确限定为当前工作区已加载的对话标题。 |
| 同一任务中可以继续补充约束；桌面设置区分当前任务补充和后续消息。[Long-running work](https://learn.chatgpt.com/docs/long-running-work)、[Settings](https://learn.chatgpt.com/docs/reference/settings) | 合并消息与补充要求输入框，保留两个独立草稿；任务仍可停止，未确认的请求复用原内容和标识。输入补充要求不会替代授权或问题答案。 |
| Claude 将短问答和长任务放进同一对话入口，明确允许停止和调整方向。[Claude 官方说明](https://support.claude.com/en/articles/16761823-claude-cowork-and-chat-are-one-claude) | 不要求用户先理解 Agent、Run、Step、Attempt。正文默认显示回答、进度和待处理事项；内部记录放进任务详情。 |
| 文件和可视化有独立预览、来源及导出入口。[Work with files](https://learn.chatgpt.com/docs/artifacts-viewer)、[Claude Artifacts](https://support.claude.com/en/articles/17153992-what-are-artifacts-and-how-do-i-use-them) | 真实生成的成果直接显示，空产物区不占位；源码、版本、协议等放到详情，来源与下载仍可访问。自由脚本继续遵守原隔离边界。 |
| 弹窗、预览和编辑器需要清楚的键盘进出路径。[Claude 键盘与读屏说明](https://support.claude.com/en/articles/17457950-use-artifacts-with-a-keyboard-or-screen-reader) | 原生 dialog 提供可访问名称；Escape 关闭后焦点回到触发按钮，手机宽度检查溢出与关键操作可达性。 |

设置以任务偏好、连接与安全、备份与维护、高级四组呈现；主题入口独立保留。收起和切页保留已挂载的草稿及待核对状态；失败、未确认请求不得随着折叠消失。授权卡仍展示精确命令、文件路径、内容变更和远程目标，不能为了简洁隐藏决策所需事实。

以上是基于公开资料和本项目流程做出的设计决定，不是用户调研统计或可用性提升百分比。主 agent 已在真实 Chrome 验证单输入框、草稿保留、折叠设置、Escape 焦点返回、390px 窄屏和实际任务补充要求；页面无脚本错误，截图留在本机，仅由本地视觉模型辅助核对。系统性代码审查按用户要求使用三个 gpt-6-astra / max 子 agent，主 agent 编写核心修复并核对跨模块行为；覆盖范围及限制见统一状态清单。
