# Agent Services

将 owner 明确发布的模型连接、工具、公开规则、运行预算和只读来源组合为可调用 Agent。此模块保存配置和版本，公共执行仍进入已有 Run/Job/Worker/LangGraph 链路。

## 文件与职责

| 文件 | 职责 |
| --- | --- |
| `domain.py` | `AgentService`、只追加的 `AgentServiceRevision`、`AgentSessionBinding` |
| `dto.py` | 严格发布与公共 DTO、当前可安全发布的工具目录 |
| `service.py` | owner 发布/修订/启停、capabilities、纯查询 `runtime_policy` |
| `sources.py` | 明确来源路径验证、发布时冻结 canonical、复制只读 namespace 快照 |
| `public.py` | 公共 Session/Run 适配、scope 检查、问答审批、公开输出及事件投影 |

owner 路由在 `entrypoints/routes/agent_services.py`，独立 Bearer ASGI 在 `entrypoints/public_api.py`。公共应用不导入 owner 应用或其 Cookie 依赖。

## 发布与运行合同

`definition` 包含 `model_connection_id/allowed_tools/instructions/runtime_options/source_workspace_id/source_paths`。发布先验证模型归属和实际注册工具；Revision flush 后冻结来源，成功才更新当前版本。修订创建新 Revision，不覆盖旧定义。Session 固定 Revision，后续 Run 使用该定义；每个 Run 按原模型机制冻结实际 ModelRevision。

`runtime_policy(db, service_id, revision_id=None)` 只返回服务启用状态、owner 和当前/指定版本定义，不依赖 access，避免授权循环依赖。ExecutionBinding 由 access 创建，绑定 Client、Agent、Revision、namespace 和执行快照。实际工具权限还会与当前 Agent/Grant 的白名单取交集；发布新能力不会自动扩大旧绑定。

当前安全目录包括受限文件读写/搜索、文件展示、计划、问答及子 Agent 协作。子 Agent 要求显式工具及非零 `child_agent_limit`；host shell、电脑操作、MCP、媒体生成尚未开放对外发布，不能用本机能力状态推断公共授权。

## 来源快照

- `validate` 校验 owner 的项目归属和明确相对路径，不允许默认整个项目、越界、受保护路径或符号链接。
- `prepare_revision` 在发布/修订事务内冻结 `data_dir/agent-api/source-revisions/{revision_id}`；源读取逐层使用目录描述符及 `O_NOFOLLOW`，stage 完成后只读封存并原子发布。
- `materialize` 只从 canonical 复制到独立工作目录的 `_sources/{revision_id}/原相对路径`，返回 `source_revision/source_paths`；不重新读取原项目。
- 单文件 2 MiB、总量 16 MiB、512 文件、4096 扫描条目、5 秒；受保护条目、链接、二进制、非 UTF-8、并发变化或超预算均整批失败。
- 目录/文件为 0500/0400。已存在的同版本只核对只读 marker，不重新哈希 payload。来源规则仅为资料，不增加执行权限；当前来源收缩由 ExecutionScope 和工具文件边界执行。

数据库回滚可能留下未被有效 Revision 引用的私有 canonical；它不因存在磁盘目录就成为公开版本。canonical 是有效 Revision 的持久输入，维护备份必须纳入，不能依赖已变化的原项目重建。现有产品备份的 schema/凭据和媒体限制本轮未修复。

## 公共投影

公开响应不返回模型地址、凭据、原始内部配置或 owner 全局规则。未知持久事件保留原游标并投影为 `run.progress`，隐藏推理和原始工具结果不公开。最终答复以 Run 的持久锚点和 outputs 的 `final_answer` 为准。

只有成功 `present_file` 发布的 Artifact 可下载；同 namespace 的子任务产物可由根任务公开。内部上下文及变更快照不因属于工作空间就自动公开。SSE 断连不取消 Run，手动取消、问答和审批沿原服务处理。

完整路由、操作顺序及撤权语义见 [对外 Agent API](../../../../../../docs/technical/agent-api.md)。来源验证使用真实临时文件；schema 040 的临时 PG 往返验证不代表正式部署或远端验收。
