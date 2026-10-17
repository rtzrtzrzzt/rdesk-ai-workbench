# 模型流适配器依据与接入约定

官方依据核对日期：2026-10-17；实现状态更新：2026-10-18。实现位于 `providers.py`，使用现有 httpx，不新增 SDK，未执行付费模型请求。原研究使用 OpenAI Docs 技能核对官方文档；来源与静态检查本身不证明模型能力，后续实际产品证据及未验范围另列于末节。

## 协议与官方来源

- [OpenAI Responses streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events)：使用 `response.output_text.delta` 和 `response.completed`，失败或 incomplete 保持失败；只使用 finalized output items，工具参数按完整对象处理。
- [OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)：无服务端存储的上下文须保留完整 output，包括 encrypted reasoning。请求使用 `store=false`，同时保留兼容性的 `include=["reasoning.encrypted_content"]`；不是只保存可见文本或工具名。
- [OpenAI Chat streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events) 与 [Chat request reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)：按调用 index 收集 `tool_calls` 增量，要求结束原因及 `[DONE]`；请求 `stream_options.include_usage=true`，尾部 usage 为整体用量。
- [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)：内部工具格式为 OpenAI function schema；Responses 编译为平面 function 字段，Chat 保留外层 function 包装。
- [Anthropic streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)：按 `message_start`、content block 生命周期、`message_delta`、`message_stop` 重建消息；usage 更新为累计值，不逐事件相加；保留 thinking signature 与 redacted blocks。
- [Anthropic tool definitions](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)：函数参数映射至 `input_schema`；工具回合使用 assistant `tool_use` 和 user `tool_result`。
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)：完整输入量是非缓存输入、缓存读取与缓存写入之和；任一计数组件缺失则总量保留未知，不能将部分计数冒充总量。
- [Anthropic API errors](https://platform.claude.com/docs/en/api/errors)：区分鉴权、权限、额度、速率及服务暂不可用；流中也可能收到 error；thinking/签名内容须原样回传。

## 调用方约定

公共接口为 `ProviderConfig`、`ProviderEvent` 及 `stream_model(config, messages, tools)`。标准类型为 `openai_responses`、`openai_compatible`、`anthropic`；另支持 `openai_chat`、`anthropic_messages` 别名。内部文本消息采用 `role/content/tool_calls/tool_call_id`。文本、函数工具及经过输入计划校验的图像已接通；PDF 的三种协议编码和 MP3/WAV 的 Chat 编码、预算及远程同意链路也已接入，精确格式与限制见 [input_sources.md](input_sources.md)。协议路线存在不证明目标模型实际具备该能力。

基础 URL 无路径时附加 `/v1` 与对应资源；已有路径作为用户指定的 API 前缀，直接追加 `/responses`、`/chat/completions` 或 `/messages`。已有完整资源路径时直接使用，因此尾部 `/v1` 不重复。禁止 URL 中的嵌入凭据、查询参数和 fragment，不跟随重定向，不更换端点、协议或模型。兼容 Chat 使用 `max_tokens`，不按模型名称猜测其他厂商的特殊参数；不兼容此字段的服务应明确报错，不能静默修改参数重试。

收到 `provider_output` 事件时，其 `data` 是：

```python
{"provider_type": "openai_responses", "output": complete_output_items}
# Anthropic 的 provider_type 为 "anthropic"，output 是完整 content blocks。
```

将整个 `data` 原样持久保存到该轮 assistant 消息的 `provider_output` 字段。下次调用时，适配器使用这些完整项目，跳过该消息根据文本和 `tool_calls` 重建的路径，避免重复。工具结果仍作为后续 `role=tool` 消息传入。Responses encrypted items 与 Anthropic thinking/signature/redacted blocks 都属于协议数据；不可仅保存摘要，不应当作 UI 的思考日志。其他提供方不能直接重放这些数据，须由上层重新编译上下文。部署/模型更换的授权与兼容性由上层负责。

`tool_call` 仅在明确终止后发出，参数必须完整解析为 JSON 对象，工具名位于本次工具白名单，调用 ID 非空且不重复。适配器不会执行工具。执行器仍须按工具自身 schema 验证参数，并核验当前权限、审批和幂等约束；JSON 完整不等于获得执行许可。

计数未知均使用 `None`；OpenAI 输入/输出总量直接取官方总量字段，不再次加上缓存或 reasoning 子集；Anthropic 合并累计 usage，再按官方口径计算总输入。`raw` 保留该 provider 的 usage 对象，不伪造缺失计数。上层需保存各 attempt 的部分文本与错误，只有收到 `done` 才可视为协议完整结束；`length`/`max_tokens` 等结束原因也应在运行层单独处理。

## 限制与验证状态

HTTP transport `retries=0`，没有隐藏重试或自动续传；超时同时约束 HTTP 阶段和整次流。SSE 按 UTF-8 增量读取，支持 LF、CRLF、CR 与多行 data；未结束的帧被丢弃，缺少协议终止事件会报错。单请求上限 16 MiB、总流上限 32 MiB、单事件/行上限 2 MiB、单工具参数上限 1 MiB、输出块/工具索引上限 128。超限明确失败，不能执行部分工具参数。

Python 编译及项目 Ruff 辅助检查已通过。后续本地 OpenAI-compatible / SGLang 连接已有真实文字流、工具往返、图像及工作台持久问答证据；2026-10-18 的 `audit-chat-ui-evidence.json` 还记录默认模型继承、连接/版本预检、补充输入与刷新正文。没有使用模拟服务或假模型响应填补证据。外部 Responses/Anthropic 的真实调用、reasoning opaque 回放、额度错误及原生 PDF/音频理解仍 **等待真实模型连接验收**；本地 Chat 通过不等于跨厂商所有扩展均支持，也未执行付费 API 验收。
