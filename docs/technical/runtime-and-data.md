# 运行时、数据模型与接口契约

本文件是内部实体标识、Run 状态、事件信封和通用 API 的权威规格。总体边界见[架构](../architecture.md)，工具的专用结果和重试见[工具与恢复](tools-and-recovery.md)。设计约束与当前实现须区分：本章主要 HTTP 路径已按生成的 OpenAPI 校准，完整类型以 `apps/web/openapi.json` 为准；当前实机范围见[状态清单](../implementation-status.md)。

## 1. 先区分六个概念

- **Workspace，工作区**：一组项目目录、设置、记忆和会话的资源边界。
- **Thread，会话**：有顺序的交流记录，可持续很多天，也可以创建分支。
- **Run，任务运行**：一次被接受的目标及其持续执行过程；可以包含模型调用、工具、子任务和等待用户。
- **Step，步骤**：Run 中具有明确目的的执行单元，例如一次模型生成、一次工具执行或等待答案。
- **Attempt，尝试**：步骤的某一次实际尝试；发生重试时新建记录，保留上一次状态与消耗。
- **Operation，外部操作**：需要记录副作用及核对结果的逻辑动作，例如一次文件修改或表单提交。网络重试不能随意把它当成另一个新动作。

一个会话内的根 Run 默认按顺序推进，避免两段生成同时覆盖同一会话上下文。多个工作区或不同会话可并行，仍受模型和其他资源许可限制。执行中的补充要求进入当前 Run 的输入队列，用户也可以明确选择另起任务。

## 2. 标识与版本

所有 ID 使用不携带用户隐私的不可猜测标识，例如 UUID；具体编码统一在基础库处理。对象 ID 不能代替权限检查。

| 字段 | 作用 |
| --- | --- |
| user_id / workspace_id | 主体与资源归属 |
| thread_id | 会话及其历史 |
| run_id / root_run_id / parent_run_id | 当前运行、根运行和直接父运行 |
| step_id / attempt_id | 逻辑步骤及每次实际尝试 |
| tool_call_id | 模型产生的工具调用在工作台内的唯一标识 |
| operation_id | 同一个逻辑副作用的登记和结果核对标识 |
| request_id | 一次客户端逻辑命令的幂等标识，重复提交时保持不变 |
| question_id / approval_id | 澄清问题与授权请求，分别建模 |
| artifact_id / artifact_version_id | 文件对象及不可变版本 |
| event_id / seq | 事件唯一标识与同一 Run 内的递增顺序 |
| trace_id | 一次技术调用链的关联标识，不承担业务幂等 |

`version` 用于乐观并发控制：修改对象时提交预期版本，只允许修改当前版本。`schema_version` 表示数据结构的解释版本。`graph_version` 表示运行图代码的版本。三者不能混为一个版本号。

同一模型服务返回的原始工具 ID 和请求 ID 作为适配器元数据保留，与内部 ID 建立映射；不假设跨提供方永远唯一。

## 3. Run 状态机

公开状态固定为：

```text
queued
  ├─ running
  ├─ waiting_resource
  ├─ failed（截止时间到期或确认无法继续，且无需停止在途执行）
  └─ cancelling

running
  ├─ waiting_user / waiting_approval / waiting_resource
  ├─ succeeded / failed
  └─ cancelling → cancelled / failed（依终止原因，完成停止与资源核对后）

waiting_* → queued 或 running（恢复条件满足后）
waiting_* → failed（截止时间到期或确认无法继续，且无需停止在途执行）
waiting_* → cancelling
```

| 状态 | 用户含义 | 后台行为 |
| --- | --- | --- |
| queued | 已接受，等待执行 | 有持久任务，尚未取得执行时段 |
| running | 有步骤正在推进 | Worker 持有有效所有权，可进行允许的动作 |
| waiting_user | 需要回答问题或核对结果 | 有持久 question，保留上下文，不占模型生成许可 |
| waiting_approval | 等待特定操作授权 | 有持久 approval，不执行等待中的动作 |
| waiting_resource | 等模型、依赖、子任务或执行资源 | 记录 wait_reason，不以忙轮询占住线程 |
| cancelling | 已收到停止请求，正在清理 | 不启动新业务动作，传播取消并核对释放 |
| succeeded | 目标运行正常完成 | 最终输出已保存，完成必要清理 |
| failed | 需要停止的失败已经确认 | 保存失败原因、已完成结果与可恢复边界 |
| cancelled | 本地受管执行已经停止 | 保留已发生的外部效果及不确定结果说明 |
| held | 恢复后的任务等待人工核对 | 隔离恢复时保留历史，不自动重放工具或启动模型 |

排队和等待中的截止时间由持久调度任务检查，不依赖任务重新进入 running。到期或确认无法继续时，若没有在途执行可以直接 failed；仍有执行则先进入 cancelling，按统一停止流程收尾，再记录 failed。用户主动停止的目标终态为 cancelled；停止失败或效果不明须单独说明。终结时使该 Run 未解决的问题、批准和继续执行 job 失效，避免迟到答案重新启动任务；结果对账和隔离资源清理仍可独立进行。等待时间如何计入预算见[工具预算](tools-and-recovery.md#93-预算字段与结算)，到期从不表示用户同意。

`wait_reason` 是状态的细化字段，例如 model_capacity、child_completion、tool_installation、checkpoint_ready；不为每个原因创建一套前端状态枚举。

有并行分支时，只要还有可推进步骤，Run 可以继续 running，同时呈现待答问题。所有分支都被阻塞时，协调器按实际阻塞原因设置等待状态；存在必须授权的操作优先呈现 waiting_approval，其次 waiting_user，其余 waiting_resource。前端不能自行根据某一个子事件把整个 Run 标为完成。

终态不原地改回 running。用户选择重试整个失败任务时创建关联的新 Run；若是还未终结的可恢复等待，则在同一个 Run 中继续。避免一个已经导出或计费的终态记录被悄悄改写。

### 3.1 Attempt 与副作用状态

Attempt 状态为 created、running、succeeded、failed、cancelled、outcome_unknown。`outcome_unknown` 表示无法证明某次执行是否已经生效，例如提交表单后连接断开；它不是“没有执行”。

MCP 等工具的一次逻辑执行可能包含多轮协议交换和等待输入。此时保留同一 tool_call_id、operation_id 和 attempt_id，以 `execution_phase=awaiting_input` 表示尝试尚未结束；running 只表示该尝试生命周期未终结，不表示正在占用 CPU/GPU。每轮交换另记 exchange_id、序号、requestState 和传输结果。收到 input_required 不把 attempt 标为 succeeded，也不增加故障重试计数；回答后继续该交换链。只有真正因故障按策略重新尝试时才新建 attempt，且先核对已有副作用和 continuation。等待时间与实际执行时间分别计量。

操作记录进一步保存 effect_status（未开始、可能已提交、已确认、已补偿或需人工核对）和证据引用。Run 可以进入 waiting_user 等待核对，但不能把未知结果转换成可自动重试的失败。

### 3.2 取消的边界

停止命令先持久化 cancel_requested_at 和递增的取消版本；Worker 每个安全边界检查。模型流、子 agent、受管进程、浏览器和桌面动作都收到取消信号。资源仅在确认释放后回到可分配池。

取消不等于撤销已发送的邮件、已写入的外部数据或已被提供方接受的请求。若清理超时，未确认资源进入隔离记录，继续占用对应安全额度或要求核对；用户看到“本地执行已停止，外部结果待核对”等真实状态。不能一边显示完全停止，一边让旧执行者继续接受新动作。

## 4. LangGraph 运行设计

### 4.1 节点职责

```text
接受持久输入
 → 加载本次配置与当前权限
 → 取得规则 / 相关记忆 / 附件处理结果
 → 等待模型许可
 → 刷新当前规则、记忆、输入和权限，再编译与检查上下文
 → 模型生成
     ├─ 文本完成 → 结果整理 → 结束
     ├─ 工具调用 → 权限与执行 → 保存结果 → 回到上下文编译
     ├─ 子任务请求 → 名额预留 / 子任务入队 → 等待或推进其他工作
     └─ 澄清 / 授权 → 持久中断 → 接受答案后继续
```

节点承担可恢复的小段工作，不在一个节点里包含“调用模型、改文件、等待用户、再改文件”的不可分割长过程。所有副作用通过统一服务登记；节点恢复重放时先查已完成操作结果。

已接受的用户补充及协作消息保存独立 ID；真正纳入模型请求的 ID 保存于 `Attempt.input_message_ids`，仅成功请求写入 `AgentMessage.delivered_attempt_id`。这证明输入已送入成功调用，不证明模型已完成该要求。完成提交与补充输入共用 Run 行锁；结束图仍有未投递输入时，使用原有消息和递增轮次重新排队，不能直接发布成功。压缩保留待投递输入，历史与 mailbox 按 ID 去重。

Steer 在生成阶段每 250ms 独立检查当前 Attempt 尚未包含的新用户输入，不依赖模型继续吐 token。发现新要求后只关闭当前未完成生成：本地 SGLang 按 Attempt 的 `rid` 定向 abort，并关闭 HTTP 流；其他提供方关闭流。已显示的正文和公开思考保留为被引导替换的片段；重新编译上下文、当前权限和附件，以同一 Run 发起新请求。输入在成功后固定到图消息中的实际位置，后续轮次不再把旧要求移到末尾。未完成工具参数不执行，已开始的命令、MCP 交互等先到实际完成或结果核对边界；未开始的过期调用可跳过并重新规划。Queue 仍等待当前任务正常收尾。

父任务收到 Steer 时，正在生成的子任务可协作让出模型许可，但不会被取消；主任务优先处理用户新要求。普通请求按持久等待顺序取得 PostgreSQL 模型锁，当前整实例推理容量仍为 1；工具与任务等待可并行，不意味着四路同时推理。该实现适配现有 Qwen HTTP 服务，与 OpenAI GPT-6 WebSocket 原生 `response.steer` 的协议不同，依据见[官方说明](https://developers.openai.com/api/docs/guides/steering)。

### 4.2 图状态

图状态保存 schema_version、graph_version、run_id、消息/产物引用、配置快照引用、当前步骤、预算摘要、未完成工具调用和中断关联。大文件、完整长日志和图片不嵌入每个检查点，保存受控引用。

LangGraph 检查点表由相应持久化适配器管理，业务模块不自行拼写其内部表结构或篡改字段。工作台的会话 thread_id 不直接等同于每个图实例的 checkpoint thread 配置；以 run_id 派生稳定的图执行命名空间，同时显式保存映射，避免同会话不同 Run 相互覆盖。

检查点不是业务的唯一记录：Run、工具操作、问题和产物有各自持久实体，能够核对“图准备执行某动作”与“外部动作已经完成”之间的崩溃窗口。

### 4.3 问题与检查点的一致性

LangGraph 的中断恢复可能重新进入节点，因此中断前代码必须能重复进入。使用稳定 interaction_key（run_id + 图节点及逻辑中断序号）执行 ensure_question，已有问题就返回已有记录，不能每次恢复新建一个问题。

问题记录、答案接受和恢复任务存入业务数据库；LangGraph 检查点可能由另一个连接提交，不能假设两个提交天然原子。处理顺序如下：

1. 建立问题记录与中断关联，再请求图中断。
2. 用户可以看到问题并提交答案，答案一旦接受即持久保存。
3. 恢复 Worker 核对对应 checkpoint 与 interrupt 标识是否已经可恢复。
4. 检查点尚未就绪时延迟同一恢复命令，不丢答案、不新建 Run。
5. 崩溃恢复重新进入节点时查同一问题；若已回答，使用已保存答案继续。

恢复提交本身也幂等，不能因为 HTTP 或 Worker 重试使同一答案推进两次。图版本升级必须保留活动任务的旧执行版本，或提供经过验证的迁移；不能把旧 checkpoint 直接交给不兼容的新图。

官方中断与持久执行语义依据：[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution)。本节的一致性桥接是工作台的设计责任。

## 5. 持久队列、租约与子 agent

### 5.1 默认队列

使用 PostgreSQL jobs 表保存任务类型、资源需求、归属、优先级、可执行时间、状态、租约所有者、lease_epoch、租约到期时间和尝试次数。Worker 以短事务领取可执行任务，使用行锁并跳过其他 Worker 已锁定的行；事务结束后才做长时间工作。

事务内建立 jobs 和相关业务状态，通知只在提交后发出。`LISTEN/NOTIFY` 丢失或可选 Valkey 重启时，Worker 仍可通过有限频率扫描领取任务。通知内容不携带密钥或完整文档。

### 5.2 防止过期 Worker 提交

每次领取或转移所有权增加 lease_epoch。心跳、步骤提交和资源释放均带 epoch 条件；旧 Worker 失去租约后不能更新新所有者的状态。模型或外部执行器无法统一接受该 fencing 字段时，已发出的动作仍须通过 operation_id 核对，不承诺数据库令牌能撤销外部世界的动作。

租约过期只触发恢复调查，不直接重放所有 running 工具。纯读可以按政策恢复；已提交写操作先查询结果；无法确认则 outcome_unknown。

### 5.3 子 agent 名额

创建流程在同一事务中检查 root_run_id 的未释放名额数、深度及权限，预留名额、创建子 Run 和入队。最多 3 个名额；queued、running、waiting_* 和 cancelling 的子 Run 都占名额。终态且清理完成后释放，未知残留资源单独保留隔离记录。

不在 Python 进程内用一个列表充当全局名额表；多个 Worker 同时创建时必须由数据库约束和事务串行化这一资源判断。

子 agent 继承的是父级权限的子集和明确目标，独立消耗预算但同时受根任务总预算约束。主任务等待时不占模型生成许可，也不把 Worker 执行线程锁在阻塞式等待函数中。

### 5.4 资源许可与死锁避免

资源请求使用统一顺序或分阶段申请，失败时释放不需要继续持有的资源。不能拿着模型生成许可等待桌面授权，也不能拿着项目写锁等待子 agent 在同一目录写入。

共享项目变更按实际写操作持有租约；子 agent 默认独立 worktree，整合时主 agent 取得共享项目写许可。浏览器和桌面按会话/设备单写者；等待用户时根据操作连续性暂停并失效旧观察，恢复后重新取得租约和观察。

队列按交互优先、任务公平和后台最低服务机会调度，防止一个长任务或后台 OCR 独占所有执行机会。预算检查发生在每次新尝试和昂贵步骤之前，已经发生的费用仍照实记录。

## 6. 数据模型

下表列出逻辑概念及关键约束，不是当前物理表清单。实际使用迁移和 ORM：配置为 runtime_defaults/workspace_runtime_defaults/model_revisions；能力、消息内容块、价格快照及文件处理计划嵌入各自 JSON 字段；每个 artifact 行本身有不可变版本；并未机械建立 settings_revisions、message_parts、artifact_versions、price_revisions、outbox 等独立表。每张可归属业务表都必须具备归属字段，必要时用复合外键保证父子对象属于同一工作区。

| 逻辑表 / 记录 | 核心内容与约束 | 所有者模块 |
| --- | --- | --- |
| users、auth_sessions | 所有者、认证状态、会话吊销；不保存明文密码 | Identity |
| workspaces | owner_user_id、根目录登记、归档状态、版本 | Workspace |
| settings_revisions | 范围、配置 schema 版本、内容、来源；敏感字段仅凭据引用 | Settings |
| model_connections、capability_profiles | 端点、协议、模型标识、配置版本、能力证据 | Models |
| threads | 工作区、标题、分支父位置、当前上下文版本 | Conversations |
| messages、message_parts | 角色、顺序、结构化内容、附件引用、完成状态、生成来源 | Conversations |
| runs | 状态、父子关系、配置快照、预算、取消、graph_version、last_event_seq | Runs |
| run_steps、attempts | 步骤目标、每次执行状态、起止时间、错误、用量引用 | Runs |
| client_requests | 主体 + request_id 唯一、请求摘要、对应资源、响应状态；响应清理后保留最小去重记录 | Runs / 通用命令入口 |
| jobs、resource_leases | 持久队列、领取版本、续租、调度信息 | Scheduler |
| agent_slots | root_run_id、子 Run、预留与释放；上限由事务维护 | Collaboration |
| tool_calls、operations、tool_exchanges | 工具版本、参数、权限、幂等/副作用、结果；多轮协议交换独立记录 | Tools |
| questions | interaction_key 唯一、问题版本、选项、内嵌的唯一有效答案 | Interaction |
| approvals | 动作范围、参数指纹、授权主体、期限、接受状态 | Policy / Interaction |
| run_events、outbox | 同 Run seq 唯一、业务事件、待发送通知或导出项 | Runs / Events |
| artifacts、artifact_versions、artifact_refs | 归属、存储键、MIME、大小、来源、不可变版本和引用 | Artifacts |
| processing_jobs 的业务明细 | 与 jobs 关联的文件处理计划、解析器版本、定位和部分结果 | Files |
| memory_documents、memory_proposals | MD 路径、版本、索引元数据、候选写入与来源 | Memory |
| memory_write_journal、memory_tombstones | 跨文件/DB提交恢复与遗忘屏障 | Memory |
| usage_records、price_revisions | 按 attempt 去重的用量、来源、覆盖率及价格时点 | Usage |
| audit_events | 授权变更、敏感执行与配置操作的脱敏审计 | Policy |
| LangGraph 持久化表 | 由锁定的 checkpointer 版本维护 | Agent Runtime 适配器 |

可以用 JSONB 保存消息内容块、能力详情和适配器原始用量，但关键归属、状态、时间、关联与查询条件必须有明确列和索引。不要把所有业务数据都塞进无法校验的一个 JSON 字段。

### 6.1 关键约束与索引

- client_requests 对 `(user_id, request_id)` 唯一；同一 ID 不同请求摘要返回冲突。
- run_events 对 `(run_id, seq)` 唯一；按归属和游标读取。
- questions 对有效的 `(question_id, version)` 只接受一个答案；再次相同提交可返回原结果，不同答案返回已解决冲突。
- operations 对逻辑 operation_id 唯一；提供方支持幂等键时复用相应键。
- jobs 索引状态、可执行时间、优先级和租约时间；避免全表高频扫描。
- usage_records 按 attempt_id 唯一结算；同一调用的流式尾部用量合并后保存，保留原始提供方计量字段，避免终止回调重复记账。
- 产物版本不可原地覆盖；引用和下载都检查归属。

完整幂等响应的保留期必须覆盖对应任务和可重试窗口。过期后可以清理响应体及业务内容，但在同一 user_id 生命周期内保留最小去重记录（tombstone）：request_id、请求摘要、原作用域标识、已过期状态和必要时间信息；不保存原始提示词、密码或工具参数。不能把唯一键也删除，否则随机 request_id 无法区分旧请求和首次请求。

命中 tombstone 的重传或查询返回 410 与明确的“请求记录已过期”业务码，不重新执行；完整记录有效期间同键不同请求返回 409。查不到记录的查询返回 404，首次写命令仍须在事务内争取唯一键，处理两个请求同时到达的情况。删除工作区时清除业务内容、保留最小去重标记；旧工作区 ID 不复用。若删除整个用户及其去重记录，原身份与会话同时失效，重新创建使用新的 user_id。该策略约束本应用命令去重，外部副作用仍须 operation 对账，不能据此宣称外部系统恰好执行一次。

### 6.2 事务边界

一个用户命令的数据库修改在短事务内完成：例如接受答案同时写答案、更新问题状态、加入恢复 job 和记录事件。跨模块操作通过公开应用服务加入同一 UnitOfWork，不直接绕过模块更新私有表。

不在数据库事务中等待模型、网络、OCR、用户或桌面。外部操作采用“登记意图 → 执行 → 保存结果”的流程，崩溃窗口由恢复逻辑核对。数据库 deadlock 等可重试错误应重试整个短事务，而非只重复最后一条 SQL。

## 7. 文件与数据库之间的一致性

文件上传先进入受控临时区，校验完成后提交到不可变版本位置，再登记可见引用；崩溃留下的临时文件由受控清理处理。新文件未提交完成时，不发布指向不存在文件的成功事件。

记忆属于可编辑 MD，采用预期版本、单写者/锁、临时文件和原子替换。所有经工作台写入服务提交的修改，必须在同一文档锁内重新检查版本并发布；由此拒绝受管写入者使用过期版本覆盖。原子替换仅保证单次替换不暴露半个新文件，不提供“仅在目标版本未变时替换”的文件系统比较并交换操作。[Python os.replace](https://docs.python.org/3/library/os.html#os.replace)

应用内锁不能约束不参与该锁的外部编辑器。文件监测、提交前重读和版本比对用于发现外部修改，但检查与替换之间仍有竞争窗口，不能保证捕获所有外部并发写入。检测到冲突时停止覆盖，保留可读取的当前版本、基础版本和候选更改供比较；journal 与版本备份不等于保存了每一次外部写入。要求严格保留原路径内容时，生成独立候选文件并由用户安排写回，或让所有写入者经统一服务提交；不能仅靠“原子替换”承诺绝对无丢失。

跨文件和 DB 没有共同事务：写入 journal 保存操作、旧/新版本和阶段；恢复时检查真实文件与记录，补齐元数据或提示冲突。不要仅凭“最后写入时间较新”自动覆盖用户的外部编辑。

备份采用一致性窗口和清单，详见[部署与验收](deployment-and-validation.md)。向量索引不存在；文本索引与派生缓存可以重建，唯一原件不能随缓存清理删除。

## 8. HTTP API 规范

### 8.1 通用规则

- 统一前缀 `/api/v1`，JSON 响应带版本化结构；文件和事件流采用各自媒体类型。
- 客户端逻辑写命令提供 request_id，并映射到 `Idempotency-Key`；服务端保存请求摘要和结果。trace_id 另行生成。
- 涉及并发修改的接口提交 expected_version 或等价条件头；失败返回冲突和当前版本。
- 分页使用不透明 cursor，明确排序；不能靠前端全量拉取所有事件和日志。
- 凭据只提供创建、替换和吊销接口；读取仅返回名称、脱敏信息和是否可用。
- HTTP 成功只说明对应命令已被接受或查询成功；异步任务通常返回 202 和可查询资源位置，不等于工作已经完成。

### 8.2 主要端点

以下是当前实际 HTTP 入口的选摘，完整参数、状态码和类型由 OpenAPI 导出；不把规划中的端点当作已实现接口。

| 接口 | 行为 |
| --- | --- |
| POST /api/v1/auth/bootstrap；GET /api/v1/auth/session；POST /api/v1/auth/logout | 本机免密码会话、读取与撤销会话；回环访问与 CSRF 边界保留 |
| GET/POST /api/v1/workspaces | 列表与创建 |
| POST /api/v1/workspaces/{workspace_id}/rename、archive、restore、remove | 分别重命名、归档、恢复、移除登记；路径最后一段取其中之一 |
| GET/POST /api/v1/workspaces/{workspace_id}/threads | 会话列表与创建 |
| GET /api/v1/threads/{thread_id}/activity | 按工具操作游标分页的聊天活动，列表不返回完整参数 |
| GET /api/v1/threads/{thread_id}/activity/{operation_id} | 当前会话可见操作的参数和有界结果，展开时读取 |
| POST /api/v1/threads/{thread_id}/runs | 提交任务；可选模型覆盖，expected_model 绑定展示过的连接 ID/版本 |
| GET /api/v1/runs/{run_id} | 任务、问题、审批和事件水位 |
| GET /api/v1/runs/{run_id}/conversation-snapshot | 同一 REPEATABLE READ 快照中的任务、消息及事件游标 |
| GET /api/v1/requests/{request_id} | 查找本主体已接受的逻辑命令；404 不等于永远未执行 |
| POST /api/v1/requests/{request_id}/close | 和原提交共用准入锁，核对已接受命令，或封存未受理标识 |
| POST /api/v1/runs/{run_id}/inputs、cancel | 补充输入或停止任务；各自独立路径 |
| GET /api/v1/runs/{run_id}/events、trace、agents、surfaces | 原生 SSE、追踪、子任务与受控界面，各自独立路径 |
| GET /api/v1/runs/{run_id}/ag-ui/events | 有明确子集边界的 AG-UI 事件适配 |
| POST /api/v1/questions/{question_id}/answers | 提交指定版本答案 |
| POST /api/v1/approvals/{approval_id}/decisions | 决定一次精确操作 |
| GET /api/v1/workspaces/{workspace_id}/artifacts/{artifact_id}/content | 带归属检查的不可变原件或派生文件 |
| POST /api/v1/workspaces/{workspace_id}/artifacts/{artifact_id}/process | 创建文件处理任务 |
| GET/POST /api/v1/model-connections | 查询和添加连接 |
| POST /api/v1/model-connections/{connection_id}/configuration、checks | 配置修订或显式连接检查，各自独立路径 |
| GET/POST /api/v1/settings/runtime | 用户运行默认值 |
| GET/POST /api/v1/workspaces/{workspace_id}/settings/runtime | 工作区覆盖及有效值来源 |
| GET/POST /api/v1/workspaces/{workspace_id}/memory/documents | 记忆文档查询与创建；正文、修订、恢复和遗忘走对应子资源 |
| GET /api/v1/analytics/summary、calls、hardware | 真实聚合、请求明细和硬件，各自独立路径 |

上传先创建 `/api/v1/workspaces/{workspace_id}/uploads`，再写 `/{upload_id}/chunks`，最后通过 `/{upload_id}/complete` 发布原件；这些后缀均接在同一工作区 uploads 路径后。分片按已登记内容与边界核对，只有完整组装后才发布 artifact。当前内容版本由 artifact 的不可变版本字段表达，不存在这里早期设计的 `/versions/{version_id}/content` 路由。

未知提交不能靠重新分配 request_id 解决。`close` 与创建共用数据库锁：正在受理返回 busy；已有记录返回 recorded；确定没有记录时写入拒绝迟到提交的封存标记，返回 sealed。之后同一标识不能执行。封存只阻止将来的接受，不证明历史上从未产生外部效果，也不撤销工具的未知结果。MCP 配置、保险库和原生会话页面据此提供明确恢复入口。

当前没有通用 `/runs/{id}/retry`、`/operations/{id}/reconciliations` 或任意 UI action 调用入口。恢复按实际任务状态和对应模块的查询、回答、审批、停止或新任务处理，不能通过生成 UI 绕过普通业务服务。

### 8.3 错误响应

```json
{
  "error": {
    "kind": "conflict",
    "code": "QUESTION_VERSION_CONFLICT",
    "message": "这个问题已被更新，请查看当前问题。",
    "retryable": false,
    "retry_after_ms": null,
    "details_ref": "<受权限保护的诊断记录引用>"
  },
  "request_id": "<逻辑请求标识>",
  "trace_id": "<技术调用链标识>"
}
```

这是错误结构示意，不是实际故障记录。HTTP 400/422 表示输入问题，401/403 表示身份或权限，409 表示状态/版本冲突，410 可表示已知请求记录过期，429 需要根据业务错误码区分限流和配额，5xx 表示服务端故障。客户端是否可安全重试还必须看操作副作用和幂等状态，不能只看 HTTP 数字。

## 9. 事件与流式输出

### 9.1 事件信封

```json
{
  "schema_version": "1",
  "event_id": "<事件标识>",
  "run_id": "<当前运行标识>",
  "seq": "<同一运行内递增整数>",
  "type": "message.delta",
  "occurred_at": "<UTC 时间>",
  "payload": {}
}
```

示意中的 seq 占位符在真实 schema 中必须为整数。workspace_id、thread_id、step_id 等可按事件类型附带，但不能用任意 payload 覆盖顶层归属。

基础事件表：run.accepted、run.state_changed、run.input_received、run.cancellation_requested；message.delta、message.completed；step.started、step.finished；attempt.started、attempt.finished；tool.progress、tool.result、tool.retry_scheduled、tool.outcome_unknown；question.created、question.answered、question.invalidated；approval.requested、approval.resolved、approval.expired；artifact.created；child.state_changed；resource.waiting、resource.released；usage.updated；error.reported。完成事件在 payload 中给出成功/失败/取消结果，不再创建一套同义的 step.completed 或 run.status_changed。类型表由统一契约维护，适配器新增类型须明确兼容方式。

公开可读思考使用 `message.reasoning_delta` 和 `Message.reasoning`，与正文独立累积；不公开 encrypted reasoning、signature 或 redacted 块。`run.input_processing` 说明哪些输入 ID 已进入正在执行的请求，与接收和成功投递分别展示。暂停任务结束当前 SSE 订阅并核对权威快照，恢复后重新订阅，不能继续呈现为生成中。

### 9.2 持久化与回放

同一 Run 通过短事务分配 seq 并写事件。文本流按有界时间/大小分批保存，减少每 token 一次数据库写入；只有已持久化的批次才作为可回放事件交给客户端。产物和完整长日志另存文件，事件只保存索引或有限预览。

SSE `id` 编码 run_id 和 seq，浏览器重连使用 Last-Event-ID 或显式游标。客户端以 event_id / seq 去重，不能把同一 delta 追加两次。发现缺口时获取缺失区间或最新快照，不重新发出创建任务命令。

快照在一致性读事务中取得业务状态与 last_event_seq。先渲染快照，再订阅该游标之后的事件，避免边查询边发生更新造成遗漏。游标超出保留窗口时返回需重新取快照的明确响应。

根任务流可以包含子任务状态摘要和链接，但子 Run 的完整事件保留独立 seq。不能把多个 Run 的 seq 混在一起解释为全局顺序。

### 9.3 与 AG-UI 的映射

当前适配器输出已确认的文本 start/content/end、失败 RUN_ERROR，以及保存内部事实的 CUSTOM 事件；每个外发 ID 由原事件 seq 与子索引构成，支持游标重放。同一内部事件可投影多个输出，但不会产生新的业务执行。

当前内部事件缺少完整标准生命周期所需的 thread 上下文，因此没有伪造 RUN_STARTED/RUN_FINISHED。任务成功、等待和取消由 `workbench.run.state_changed` 的 CUSTOM 事实表达。真实浏览器已核对唯一事件 ID 和成功终态。后续增加标准映射须先补齐真实字段；不能把等待用户映射为内部 succeeded。详见[模块契约](../../apps/server/src/workbench/modules/events/README.md)。

## 10. 交互、授权与恢复命令

Question 包含 question_id、version、run_id、interaction_key、提示、类型、选项稳定 ID、必填性、推荐值、创建/失效时间和状态。Answer 记录回答主体、答案、所针对版本及 request_id。问题更新后旧版本失效，重新加载页面可取得当前有效版本。

Approval 另含动作摘要、参数/目标指纹、权限范围、有效期和是否一次性。修改目标文件、域名、设备或关键参数后需要重新核对授权；一个“可以继续”不能无限扩大为其他工具或其他项目的权限。

用户接受答案或授权后，只创建恢复命令；Worker 重新核对当前权限、配置兼容性、checkpoint 和资源许可再执行。收到答案不等于立刻拿到模型执行资源。

## 11. 前端状态管理与契约生成

后端导出 OpenAPI，`openapi-typescript` 生成 `schema.d.ts`；HTTP、下载和 SSE 仍由项目手写适配层封装，没有自动生成完整客户端。网络数据在所需边界进行运行时校验，生成类型不能证明任意 JSON 正确。

TanStack Query 维护查询，React hooks 管理布局、草稿和流状态。会话先读取一致 `conversation-snapshot` 再合并增量；重连缺口和终态重新核对快照，不用固定定时器反复覆盖流中文字。完成消息以权威正文收敛。查询支持取消和卸载保护。

登录/注销推进认证代次，旧请求不能发布新会话的 CSRF 或页面结果；注销清理当前挂载状态与待发草稿。未确认发送保留原 request_id、模型、附件和输入，关闭提示或导航后仍有可达的核对入口，不能把丢失响应解释为未接受。

客户端可显示“正在提交”，只有收到后端事实才显示“已接受”。待答问题和精确授权始终可达；内部步骤、原始 JSON、版本和调试记录默认收起。

普通聊天按持久时间混排模型进度正文、工具状态与最终答复；工具的名称、简短目标和状态直接可见，结果可展开，原始参数再折叠。可见思考默认折叠，点击显示提供方实际返回的文字。活动独立分页，用户分支只包含所选历史实际引用的工具，不引入源会话分支点之后的操作。子 agent 由 Run 的父子关联识别，普通侧栏不列为新会话；主任务中的子任务入口打开独立只读对话面板，使用子 Run 自己的状态和 SSE，不改变当前主会话。

## 12. 必须验证的故障窗口

1. 数据库接受任务后 HTTP 响应丢失：找回同一个 Run。
2. 工具已写文件但结果尚未提交：核对版本和 operation，不能再覆盖一次。
3. 问题已显示但图 checkpoint 尚未就绪：答案保留，稍后恢复。
4. Worker 租约转移后旧进程返回：拒绝过期提交，外部结果另行核对。
5. SSE 重连得到重复和缺口：去重、回放或重建快照。
6. 用户停止与模型完成同时发生：事务条件决定终态，保留实际结果和取消时间，不能出现终态来回翻转。
7. 第 3、4 个子 agent 同时申请：最多 3 个有效名额。
8. 数据库暂时不可写：禁止发起新的不可记录副作用，已在途动作保留核对线索。
9. 部署图版本升级时仍有等待问题的任务：使用兼容版本恢复，或明确暂停等待迁移。

这些是后续实现的验收场景，并非本轮已经执行的测试。实际界面流程与发布要求见[部署与验收](deployment-and-validation.md)。
