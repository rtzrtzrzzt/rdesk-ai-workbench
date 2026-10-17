# 只读 AG-UI 事件投影

入口：`from workbench.modules.events import translate`。

```python
translated = translate(run_id, seq, type, payload)  # list[dict]，无 I/O/DB/运行状态
```

仅传入已经提交的真实 RunEvent。当前原生事实为 `modules/runs/service.emit/event_view` 和 `entrypoints/api._event_batch`；本模块不改变这些schema、任务状态、权限、问题/批准或恢复方式，不写另一套运行时。没有AG-UI/CopilotKit SDK依赖。

## 固定映射

每个原生事件的第0项始终是 `CUSTOM`，`name='workbench.'+type`，`value={run_id,seq,payload}`。payload是明确公开字段的投影，**不是原始payload无损透传**；未知/敏感字段省略，`metadata.workbench.payload_fields_omitted=true` 可见。支持的标准事件在第1项，因此主原生事件信息及标准展示事件均有独立稳定定位。

| 原生事实 | 额外标准事件 | 条件与保留的ID |
| --- | --- | --- |
| `attempt.started` | `TEXT_MESSAGE_START` | 必须同时有有效attempt_id/step_id/message_id和number>=1；实际`models/execution.begin_attempt`先创建空assistant Message再持久此事件，messageId完全复用message_id。文件/工具尝试没有此消息证据则只有CUSTOM。 |
| `message.delta` | `TEXT_MESSAGE_CONTENT` | 有真实message_id和字符串delta，空字符串合法。保留原字符串，不补start/end。 |
| `message.completed` | `TEXT_MESSAGE_END` | message.id存在、role=assistant、status=completed。原生finish_attempt也会用这个事件名报告interrupted，因此不只根据事件名判断。 |
| `run.state_changed` status=failed | `RUN_ERROR` | 仅已持久失败终态。固定消息只说明“已失败，详情见原生任务状态”，code=workbench.run_failed，不伪造异常原因。run_id在metadata保留。 |

其他事件都只返回CUSTOM，包括：

- run.accepted、running/queued/waiting/succeeded/cancelled等状态。目前这些事件没有标准RUN_STARTED/RUN_FINISHED必需的threadId；running可在恢复时再次出现。既不猜threadId，也不把等待/取消当成功结束。没有造新runId。
- error.reported可能带retrying=true；它本身不能证明终态，因此不直接产生RUN_ERROR。
- tool.progress是工具**执行**阶段，不是AG-UI工具参数开始；TOOL_CALL_END只表示参数完整，不表示执行完成。tool.result又不带标准结果事件必需的原生tool messageId，因此不造ID/开始/参数/结束/结果。保留operation_id/tool_call_id/tool_name等可公开引用；结果和参数仍从原生受权资源查询。
- question/approval仅公开id/版本/状态/提示等受控摘要，交互仍走现有工作台命令。context/usage等没有兼容公共schema的字段保留空payload+省略标记，不冒充完整state snapshot/JSON Patch。

这是可重放的 **AG-UI事件子集投影**，不是完整AG-UI invocation/SDK的即插即用服务。严格要求完整Run生命周期的客户端必须理解本适配约束；缺少真实字段时应使用原生查询/CUSTOM，不能为通过客户端校验而合成生命周期。

## 身份、重放和晚订阅

每项都有标准BaseEvent允许的 `metadata.workbench`：adapter_version=`workbench.ag-ui:1`、run_id、seq、subindex、event_id、native_type、payload_fields_omitted。event_id恒为 `run_id:seq:subindex`；不添加AG-UI schema未声明的顶层id。相同输入重复调用结果完全一致，不读时钟/随机数、不修改输入，不跨事件缓存猜状态。

主 HTTP 已提供独立 `GET /api/v1/runs/{run_id}/ag-ui/events` 适配路由，保持原生 SSE 不变。订阅与客户端遵循以下契约：

1. 沿现有鉴权/归属/一致性短事务读取已持久批次，检查seq连续/保留窗口。每批重新鉴权，DB会话不跨网络yield。
2. 对每条调用translate，逐项发送 `id: <metadata.workbench.event_id>` 和 `data: <event JSON>`；沿现有默认message事件/heartbeat方式即可。不要把heartbeat、网络断开或读错误翻译为RUN_FINISHED/RUN_ERROR。
3. 新订阅先读取原生一致性snapshot及last_event_seq，再after该seq订阅。晚订阅即使先收到CONTENT/END也不补造START，snapshot负责已有消息。投影不是自包含消息快照。
4. 断点 `run_id:seq:subindex` 必须校验同run及subindex为0或1，并重新取**该seq**原生事件，例如调用原批次读取after=seq-1，然后仅发送 `(seq,subindex)` 大于游标的项。不能直接after=seq，否则断在第0项时会丢掉第1项。不要把适配游标误传现有原生SSE的run_id:seq解析器。
5. 客户端按完整event_id去重，不能只按原生seq去重（同seq有两项）。收到standard CONTENT重复不能再次追加。run之间独立排序；根/子run不能混合解释为全局seq。游标缺口/过期沿原契约要求重新snapshot，不重新创建任务。

适配版本/字段投影在当前v1内保持稳定；未来改变事件数/顺序/映射必须更换版本并要求重新snapshot，不能使旧游标静默指向另一项。DB状态是最终事实，适配流结束不是业务完成证据。

## 公开范围与限制

`_FIELDS`及嵌套消息/问题/批准字段都是显式白名单。provider_message、provider_output、raw/opaque、凭据、headers/cookies、未知metadata/metrics、工具arguments/result、表单schema/answer/附件对象都不递归穿透。未知事件保留name/run/seq，payload={}并标记省略；未知字段名也不回传。没有THINKING/REASONING事件，没有读取或输出隐含推理/加密推理连续性数据。

已确认的可见正文、错误说明、用户提示或摘要仍可能包含用户主动粘贴的内容；此适配器不是语义秘密检测器，不自动改写这些已授权可见文字。HTTP必须先执行与原生Run相同的用户归属/权限校验，不能因为使用标准协议就公开全局事件。真实角色/状态或字段不足时降为CUSTOM；不补default业务状态。

参数要求规范小写UUID run_id、正整数seq（JSON安全整数范围，拒绝bool）、受限原生事件名、dict payload；非法入口ValueError无回显输入。公开文本预算与原生模型2百万字符一致；超预算字段省略并标记，不截断delta然后当完整片段输出。无任意JSON递归遍历、磁盘、网络或模型调用。

## 辅助检查与待验收

2026-10-17：纯结构探针通过确定性重放、输入不变、完整subindex定位、晚订阅仅CONTENT、interrupted不END、重试错误不RUN_ERROR、工具缺ID不伪造标准事件、未知嵌套字段/凭据/provider reasoning不透传、坏seq拒绝。已对照下面固定官方schema的required/允许字段校对所输出事件；这只是必要schema字段检查，不宣称官方SDK端到端兼容或真实产品验收。

上述探针使用明确的结构测试标识，不创建业务记录，不产生模型响应；没有写 DB 或重启服务。HTTP、归属鉴权与断点投影已接入实际入口。

2026-10-18 主 agent 的真实 Chrome 流程读取实际任务的 AG-UI 事件，核对 `CUSTOM` 中的 succeeded 业务终态及完整事件 ID 唯一性；本机证据 `.local/installation-acceptance/generated-memory-ui-evidence.json`。这里没有标准 `RUN_FINISHED` 全生命周期承诺，不能将 CUSTOM 终态通过说成完整 AG-UI invocation/官方 SDK 协议通过。事件保留窗口过期、全部断点位置与第三方客户端仍各有验证范围。
