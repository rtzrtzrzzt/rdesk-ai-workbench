# 对外 Agent API

公共 API 提供完整的服务端 Agent 执行：复用已有 Run、Job、LangGraph、Worker、模型连接、工具、问答、取消及 Artifact，不转发本机 owner HTTP，也不是单次模型聊天接口。本轮协议为原生 `/agent-api/v1`；未提供 OpenAI Responses 或 A2A 兼容端点。

本机管理应用继续保持回环、会话 Cookie 与 CSRF 边界。独立公共 ASGI 应用只接受 `Authorization: Bearer …`；不能通过它 bootstrap owner、读取设置或取得模型端点、凭据及全局规则。公共 API 的 OpenAPI 由鉴权后的 `GET /agent-api/v1/schema` 提供。

## 配置顺序

通过本机 owner 管理入口按以下顺序配置；下表列出实际 HTTP 合同，设置界面的交付与验收状态以本轮记录为准。保存配置与真实启动、外部可达性验收是不同状态。

1. 发布 Agent：选择已有模型连接、工具白名单、公开专用规则和有界 `runtime_options`。模型选择来自现有连接；公共请求不能自行更换模型或运行选项。
2. 需要来源资料时，显式选择所属项目及非空 `source_paths`。只分享选定的相对文件或目录，不默认分享整个项目。
3. 创建 Client，设置每分钟请求数和活动根任务上限；默认分别为 120 和 3。
4. 为 Client 签发 Key，可设置带时区的未来 `expires_at`。明文只在首次签发响应中返回；重试同一签发请求只返回元数据，`key` 为 `null`。遗失时签发新 Key，再撤销旧 Key。
5. 为 Client 与 Agent 保存 Grant，明确动作及可选的更窄工具白名单。
6. 保存并启动独立 listener；默认 `enabled=false`、`host=127.0.0.1`、`port=8767`。

| 本机 owner 路由（均以 `/api/v1` 开头） | 用途与主要字段 |
| --- | --- |
| `GET /agent-services/capabilities` | 可用模型 `id/name`、安全工具目录、`runtime_options_schema`、`source_sharing` 预算及未开放能力 |
| `GET/POST /agent-services` | 列表；发布 `{name, description, definition}` |
| `GET /agent-services/{agent_id}` | 当前定义、`version`、`revision_id`、`revision_number` |
| `POST /agent-services/{agent_id}/revisions` | 完整发布字段加 `expected_version`，创建新版本 |
| `POST /agent-services/{agent_id}/state` | `{enabled, expected_version}` |
| `GET/POST /api-clients` | 列表；创建 `{name, request_id, requests_per_minute, max_active_runs}` |
| `POST /api-clients/{client_id}` | `{enabled, expected_version, requests_per_minute, max_active_runs}` |
| `GET/POST /api-clients/{client_id}/keys` | 元数据列表；签发 `{request_id, expires_at?}` |
| `POST /api-clients/{client_id}/keys/{key_id}/revoke` | `{request_id}`；撤销指定 Key |
| `GET /api-clients/{client_id}/grants` | 当前 Grant 列表 |
| `POST /api-clients/{client_id}/grants/{agent_id}` | `{enabled, actions, allowed_tools, expected_version}`；首次版本为 0 |
| `GET/POST /agent-api/listener` | 读取或保存独立监听配置；保存带 `expected_version` |
| `POST /agent-api/listener/start`、`/stop` | `{request_id}`；只管理 `public-api` 组件 |

`definition` 包含 `model_connection_id`、`allowed_tools`、`instructions`、`runtime_options`、`source_workspace_id` 和 `source_paths`。完整类型以当前 schema 和模块 DTO 为准。

Grant 的 `invoke` 允许创建执行，`read` 允许读取受限会话/运行/事件，`write` 允许补充输入、回答、取消和恢复，`files` 允许上传及下载公开产物，`approve` 允许提交手动审批。上传还通过 `invoke` 准备独立 namespace；Agent 列表要求同时具备 `read/invoke`。HTTP合同省略actions时默认为 `invoke/read/write/files`，不包含 `approve`；设置页新授权默认仅勾选 `invoke/read`，控制任务和文件权限需主动选择。`allowed_tools=null` 表示沿用 Agent 发布白名单，不代表开放全部工具。

## 版本、来源和执行边界

Session 固定创建时的 Agent Revision，之后的新 Run 继续使用该版本的模型连接 ID、公开规则和运行选项。每个 Run 仍沿已有模型机制冻结调用时的 ModelRevision。发布新 Agent Revision 不会把旧 Session 自动迁移到新定义。

执行工具取已绑定白名单、原发布版本、当前 Agent 定义与当前 Grant 的交集。因此收缩授权会限制已存在的任务，扩大授权不会自动扩大旧绑定。来源访问同样取原发布选择与当前允许路径的交集；更换当前来源项目不会让旧任务获得新项目。

来源在**发布或修订时**冻结为 UTF-8 文本/代码快照，所有 Client 使用同一 Revision 的 canonical 内容。原项目后续修改只在新发布版本中出现。快照复制到独立工作空间的 `_sources/{revision_id}/原相对路径`，只读；需要修改时，将结果写到其他工作文件。源项目规则不会作为执行权限加载。

来源默认预算为单文件 2 MiB、总量 16 MiB、512 个文件、4096 个扫描条目及 5 秒。遇到受保护目录、`.env`、链接、二进制、编码不符、来源在冻结期间变化或超预算时，整批失败，不静默分享一部分。相同 Revision 的重复物化只核对只读 marker，不重复读取原项目或重新哈希全部内容。

每个 Client/Agent 有独立 namespace 和 Workspace。Run、子任务、上传、文件、问题、审批和输出沿执行绑定检查归属；持有 UUID 不构成授权。子 Agent 还受发布的协作工具、`child_agent_limit` 和父任务范围限制。默认子 Agent 数量为 0，需显式配置后使用。

当前可发布工具覆盖受限文件读写/搜索、文件展示、计划、问答和受限子 Agent 协作，以 capabilities 返回的真实目录为准。host shell、电脑操作、MCP 和媒体生成的对外发布尚未就绪；本机存在这些能力不等于它们已授予外部调用方。

## Listener 与部署

监听配置字段为 `enabled`、`host`、`port`、`public_hosts`、`ssl_certfile`、`ssl_keyfile`；响应另带 `version/running/url/message`。有现存 `public-api` 组件记录时，先停止，再修改并启动；保存不会静默更换正在监听的地址。

`public_hosts` 是精确 Host authority，例如 `api.example.com` 或 `api.example.com:8767`，不接受 `*` 或带路径的 URL。非回环 bind 必须提供实际可读、权限合规且匹配的 TLS 证书/密钥；通配 bind 还必须指定精确公开 Host。TLS 预检不替代外部设备的证书信任、域名和连通性验证。

回环 HTTP listener 可接显式配置的反向代理或隧道。代理需保留已配置 Host，由外部入口提供 TLS；本应用不自动安装代理、创建隧道或公开端口。Uvicorn 使用 `proxy_headers=False`，不根据任意 forwarded 头扩大信任。当前面向服务间调用，不提供跨域 Cookie 登录流程。API Key 只放 Authorization 头，不放 URL。

CLI 的 `start` 在持久配置启用时追加可选 `public-api` 组件；`status/doctor` 显示独立状态，`stop` 纳入该组件。`workbench public-api` 是读取同一持久配置的子进程入口。所有权核对复用现有进程记录，未确认归属时保留记录，不按裸 PID 或进程名终止。管理 listener 不重启模型、PostgreSQL 或整个工作台。

## 公共原生路由

下表路径均以 `/agent-api/v1` 开头，均需要 Bearer Key。

| 方法与路径 | 请求或返回要点 |
| --- | --- |
| `GET /agents`、`/agents/{agent_id}` | 已授权 Agent、Revision 与有效 capabilities；不返回完整发布定义 |
| `POST /sessions` | `{agent_id, title?, request_id}`；返回 `id/agent_id/revision_id/title/created_at` |
| `GET /sessions/{session_id}` | 当前 Client 范围内的会话 |
| `POST /runs` | `{agent_id, input, request_id, session_id?, deadline_seconds?, attachments?}`；省略 Session 时一并创建 |
| `GET /runs/{run_id}` | 状态、Session/Agent/Revision、事件游标、问题、审批及最终答复锚点 |
| `GET /runs/{run_id}/events` | 原生 SSE；`after` 或 `Last-Event-ID: {run_id}:{seq}` 续读 |
| `POST /runs/{run_id}/inputs` | `{input, request_id}`；持久补充输入 |
| `POST /runs/{run_id}/answers` | `{question_id, version, answer, request_id}` |
| `POST /runs/{run_id}/decisions` | `{approval_id, version, approved, request_id}`；需要 `approve`，smart 模式不接受人工覆盖 |
| `POST /runs/{run_id}/cancel` | `{request_id}`；请求正常取消及资源收尾 |
| `POST /runs/{run_id}/resume` | `{request_id}`；沿已有安全恢复条件继续，不延长预算 |
| `GET /runs/{run_id}/outputs` | `final_answer/messages/artifacts`；Artifact 含版本和下载 URL |
| `GET /runs/{run_id}/outputs/{artifact_id}/content?version=…` | 受限公开产物下载；需 `files` |
| `GET /runs/{run_id}/changes` | `revision/coverage/gaps/files/totals`；覆盖率不完整时保留实际缺口 |
| `GET /runs/{run_id}/changes/{change_id}` | 相对路径、diff、截断及覆盖状态 |
| `POST /agents/{agent_id}/uploads` | `{filename, total_bytes, mime_type?, request_id}` |
| `GET /agents/{agent_id}/uploads/{upload_id}` | 实际上传进度与续传位置 |
| `POST /agents/{agent_id}/uploads/{upload_id}/chunks?offset=…` | 原始字节块，每块最多 4 MiB |
| `POST /agents/{agent_id}/uploads/{upload_id}/complete` | `{}`；返回文件 `id/filename/mime_type/byte_size/version` |
| `GET /schema` | 当前公共应用 OpenAPI |

附件沿已有 `artifact_id/version/mode` 及模型输入许可合同提交，最多 8 项；不能引用其他 namespace 文件。下载只发布成功 `present_file` 的产物，包含受同一 namespace 约束的子任务产物；内部上下文和变更快照不因属于工作空间就公开。

Run 返回 `final_message_id/finalized_event_seq`，outputs 的 `final_answer` 对应持久最终锚点。中间 assistant 消息或最后一个文本增量不能自行认定为最终答复。SSE 只投影公开字段；内部事件保留游标但转为 `run.progress`，不返回隐藏推理、原始工具结果或内部配置。完整消息和 outputs 会改写公开下载链接，增量消费方仍应以完整消息/outputs 核对最终内容。

断开 SSE、关闭客户端或停止 listener 都不会取消持久 Run；取消须显式调用 `/cancel`。客户端按逻辑命令生成 UUID `request_id`，网络重试保持同一个 ID 和内容，不把未知提交当作新命令。不同 Client 的请求标识隔离。

## curl 调用示例

先把首次签发的真实 Key 注入环境变量 `RDESK_API_KEY`；下列 URL、Agent ID 和 Run ID 均为待替换占位，不是已部署实例或业务结果。

```bash
export RDESK_AGENT_API_URL='https://<你的API域名>/agent-api/v1'
export RDESK_AGENT_ID='<已授权的Agent UUID>'

curl --fail-with-body \
  -H "Authorization: Bearer ${RDESK_API_KEY:?请先设置RDESK_API_KEY}" \
  "$RDESK_AGENT_API_URL/agents"

RDESK_REQUEST_ID="$(python -c 'import uuid; print(uuid.uuid4())')"
curl --fail-with-body \
  -H "Authorization: Bearer $RDESK_API_KEY" -H 'Content-Type: application/json' \
  "$RDESK_AGENT_API_URL/runs" --data @- <<JSON
{"agent_id":"$RDESK_AGENT_ID","input":"<本次任务内容>","request_id":"$RDESK_REQUEST_ID"}
JSON

export RDESK_RUN_ID='<上一步响应中的Run UUID>'
curl -N --fail-with-body -H "Authorization: Bearer $RDESK_API_KEY" \
  "$RDESK_AGENT_API_URL/runs/$RDESK_RUN_ID/events?after=0"

curl --fail-with-body -H "Authorization: Bearer $RDESK_API_KEY" \
  "$RDESK_AGENT_API_URL/runs/$RDESK_RUN_ID/outputs"

curl --fail-with-body -H "Authorization: Bearer $RDESK_API_KEY" \
  "$RDESK_AGENT_API_URL/schema"
```

恢复 SSE 时提供最近确认的 `{run_id}:{seq}`。游标超前、格式错误或历史不可回放时，重新读取 Run 快照，不自行补造事件。

## 撤权与数据维护

撤销或过期 **Key** 会拒绝后续 HTTP 鉴权，并在 SSE 下一批重检时关闭流；已有 Run 的身份是 Client 执行绑定，Key 轮换本身不会取消后台任务。新 Key 属于同一 Client 时，可继续读取仍获授权的会话和运行。

停用 **Client/Agent**、停用 Grant 或撤销 `invoke` 会使后续执行权限检查失败；Worker 沿正常取消路径停止关联任务并确认资源清理。缩减工具/来源列表限制后续使用。撤权不回滚已经发生的效果，也不能承诺瞬间撤销已发出的外部调用。

数据库保存 Agent/Revision、Session 绑定、Client、Key 摘要、Grant、namespace、ExecutionBinding 和 Run 关系。Key 明文不写入数据库或持久请求回执。磁盘位置包括：

- `data_dir/agent-api/listener.json`：0600 原子保存的监听配置；TLS 材料只保存本机路径引用。
- `data_dir/agent-api/source-revisions/{revision_id}`：发布时冻结的 canonical 来源，目录/文件权限为 0500/0400。
- `data_dir` 同级的 `{data_dir.name}-api-workspaces/{namespace_id}`：独立工作目录；其 `_sources/{revision_id}` 是 canonical 的只读副本。
- `data_dir/runtime/processes.json` 和 `public-api.log`：沿用启动器所有权记录与独立组件日志。

canonical 是 Revision 的持久输入，不能依赖原项目恢复；完整维护方案必须纳入 canonical、独立工作目录和数据库关系。当前产品备份仍沿用既有 schema、凭据净化和媒体范围限制，本轮没有补齐这些备份/恢复能力；例如新增 Key 摘要字段仍受现有凭据 schema 审核门禁约束。不要把此处的持久位置说明当作完整备份已经受支持。

schema 040 已在真实临时 PostgreSQL 中完成升级/降级往返验证；当前本机运行实例也已迁移、重载并完成真实UI与独立客户端验收，验收后listener恢复关闭。产品备份限制仍然存在；远端TLS、反向代理与外部设备的连通性等待实际部署环境验证。具体证据见[本轮验收记录](../implementation-status.md#28-2026-11-02-最终答复文件变更与对外agent-api)。模块职责见 [agent_services](../../apps/server/src/workbench/modules/agent_services/README.md) 与 [access](../../apps/server/src/workbench/modules/access/README.md)。
