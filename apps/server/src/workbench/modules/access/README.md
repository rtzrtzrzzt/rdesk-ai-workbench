# Public Access

为公共 HTTP 和后续后台执行提供同一持久身份边界。Key 认证 Client；Run 的权限来自 ExecutionBinding，不来自连接是否仍存在，也不依赖可变的父子 Run 标识。

## 数据与入口

| 实体/文件 | 作用 |
| --- | --- |
| `ApiClient` | owner 下的调用方、启用状态、请求频率和活动根任务配额 |
| `ApiKey` | Client 的随机 Bearer Key 摘要、前缀、到期/撤销和最后使用时间 |
| `Grant` | Client/Agent 对的动作集合与可选更窄工具白名单 |
| `ExternalNamespace` | Client/Agent 对应的独立 Workspace |
| `ExecutionBinding` | Client、Agent、Revision、namespace 和冻结执行定义 |
| `service.py` | 鉴权、owner 归属、授权交集、namespace、执行/文件权限与撤权收尾 |
| `listener.py` | 默认关闭的独立 listener 配置、TLS/Host 检查及组件所有权控制 |

owner 接口分别在 `routes/api_access.py` 与 `routes/agent_api_listener.py`，继续使用本机 Cookie/CSRF。`public_api.py` 只使用 Bearer，不暴露 owner bootstrap、设置或凭据入口。完整路由和字段见 [对外 Agent API](../../../../../../docs/technical/agent-api.md)。

## 授权合同

`authenticate` 校验 Key 摘要、撤销/过期和 Client 状态；普通请求更新额度及使用时间，SSE 续批 `count_request=False` 为只读重检。Key 明文只首次展示，不写数据库或幂等回执；同一签发 request 重试返回 `key=null`。

Grant 动作为 `invoke/read/write/files/approve`，默认不包含 `approve`。`allowed_tools=null` 沿用发布目录，空列表不授予任何工具。`external_request_id` 将外部 UUID 映射到 Client 独立的请求命名空间。

`create_execution_binding` 只信任已发布定义，校验 Client/Agent/namespace 和配额，并物化指定 Revision 的只读来源。Session 的 Revision 固定；`scope_for_run` 每次将绑定、原 Revision、当前 Agent 和当前 Grant 的工具权限取交集。来源取原选择与当前允许范围交集；模型连接 ID 保持绑定值。子任务和后台文件任务沿用外部执行身份，不能因恢复、派生或对话删除而变回 owner 权限。

两种撤权语义必须区分：

1. Key 撤销或过期：拒绝后续 HTTP，SSE 重检后断流；现有 Run 不因 Key 轮换自动取消，同一 Client 的新 Key 可继续访问仍被授权的资源。
2. Client/Agent 停用、Grant 停用或撤销 `invoke`：后续执行校验失败，Worker `maintain` 沿正常取消和资源清理路径收尾。工具/来源收缩限制后续使用；不回滚已发生的效果，也不保证在途外部动作瞬时撤销。

## Listener 与维护

配置位于 `data_dir/agent-api/listener.json`，以 0600 原子保存；默认关闭并绑定 `127.0.0.1:8767`。保存需要 `expected_version`，有任何现存组件记录时先停止再更改。`public_hosts` 为精确 Host authority；非回环 bind 必须完成真实 TLS 文件及密钥对只读预检，通配 bind 还需明确 Host。代理/隧道由外部配置，`proxy_headers=False`，不信任任意 forwarded 头。

启停只操作启动器记录中的 `public-api` 组件，复用 capture/matches/terminate_owned；未确认的进程记录保留。CLI 全栈 start/status/stop 纳入此可选组件，保留原五组件健康判断及模型/数据库生命周期。运行时不安全记录的 CLI `SystemExit` 被 HTTP 管理适配为 `AppError`。

普通公共请求因鉴权更新配额而进入维护 write gate；SSE 响应准备后释放 gate，后续只读对账不长期阻止维护。取消请求可优先中断维护。Uvicorn 关闭流的宽限为 10 秒，早于组件停止的 15 秒确认期限；关闭传输不取消持久 Run。

独立工作目录位于 `data_dir` 同级的 `{data_dir.name}-api-workspaces/{namespace_id}`，发布来源 canonical 位于 `data_dir/agent-api/source-revisions/{revision_id}`。完整维护必须覆盖这些磁盘输入和数据库关系。现有产品备份的 schema/凭据净化与媒体范围限制仍然有效；临时 PostgreSQL schema 040 往返验证不等于备份恢复或正式/远端部署验收。
