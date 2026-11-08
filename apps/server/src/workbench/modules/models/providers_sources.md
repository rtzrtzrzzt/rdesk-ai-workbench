# 模型流适配器依据与接入约定

官方依据核对日期：2026-10-17；引擎与计数依据补核：2026-10-18。实现位于 `providers.py`、`engine_adapters.py`，使用现有 httpx，不新增 SDK，未执行付费模型请求。原研究使用 OpenAI Docs 技能核对官方文档；来源与静态检查本身不证明模型能力，后续实际产品证据及未验范围另列于末节。

## 协议与官方来源

- [OpenAI Responses streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events)：使用 `response.output_text.delta` 和 `response.completed`，失败或 incomplete 保持失败；只使用 finalized output items，工具参数按完整对象处理。
- [OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)：无服务端存储的上下文须保留完整 output，包括 encrypted reasoning。请求使用 `store=false`，同时保留兼容性的 `include=["reasoning.encrypted_content"]`；不是只保存可见文本或工具名。
- [OpenAI Chat streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events) 与 [Chat request reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)：按调用 index 收集 `tool_calls` 增量，要求结束原因及 `[DONE]`；请求 `stream_options.include_usage=true`，尾部 usage 为整体用量。
- [Qwen SGLang 部署](https://github.com/QwenLM/Qwen3/blob/main/docs/source/deployment/sglang.md)：公开的 `reasoning_content` 独立于正文；工作台只展示服务实际返回的可读内容。Responses 仅提取可读 summary/reasoning text，Anthropic 仅提取 thinking 文本；opaque/signature/redacted 始终留在私有协议记录。
- [OpenAI Mid-turn steering](https://developers.openai.com/api/docs/guides/steering)：GPT-6 WebSocket 原生中途引导作为行为依据；本地 Qwen 使用关闭未完成生成、重新编译和发起请求的适配。SGLang 0.5.21 的 `rid`、`/abort_request` 与断流清理按本机已安装源码核对，只取消目标 Attempt，不使用 abort_all。
- [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)：内部工具格式为 OpenAI function schema；Responses 编译为平面 function 字段，Chat 保留外层 function 包装。
- [Anthropic streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)：按 `message_start`、content block 生命周期、`message_delta`、`message_stop` 重建消息；usage 更新为累计值，不逐事件相加；保留 thinking signature 与 redacted blocks。
- [Anthropic tool definitions](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)：函数参数映射至 `input_schema`；工具回合使用 assistant `tool_use` 和 user `tool_result`。
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)：完整输入量是非缓存输入、缓存读取与缓存写入之和；任一计数组件缺失则总量保留未知，不能将部分计数冒充总量。
- [Anthropic API errors](https://platform.claude.com/docs/en/api/errors)：区分鉴权、权限、额度、速率及服务暂不可用；流中也可能收到 error；thinking/签名内容须原样回传。
- [SGLang Chat 模板参数](https://docs.sglang.io/docs/basic_usage/openai_api_completions)与 [vLLM reasoning outputs](https://docs.vllm.ai/en/stable/features/reasoning_outputs/)：`chat_template_kwargs` 的键和实际效果取决于模型模板，推理解析器不能代替启用思考。工作台仅映射已确认的 Qwen3 家族参数，不把引擎支持模板参数等同于任意模型支持相同开关。
- [SGLang 0.5.21 tokenize 实现](https://github.com/sgl-project/sglang/blob/v0.5.21/python/sglang/srt/entrypoints/openai/serving_tokenize.py)与 [请求/响应结构](https://github.com/sgl-project/sglang/blob/v0.5.21/python/sglang/srt/entrypoints/openai/protocol.py)：消息计数使用 Chat 的 `_process_messages` 渲染路径，接口返回 `tokens/count/max_model_len`；依据同时与本机已安装 0.5.21 源码核对。此扩展不是通用 OpenAI API。
- [NVIDIA Qwen3.8-Flash-Next 模板](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4/blob/main/chat_template.jinja)：本机固定模型模板原生接受 `low/medium/xhigh`，其他强度会触发模板异常；原生自动适配在保存和请求阶段提前拒绝 `high`，不改写为其他原生等级。工作台/自定义预设的指令档位另见下节，不能与此原生参数集合混同。

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

## 工具集合与协议纠正

`discover_tools` 是工作台自身的加载策略：`action=add` 为默认值，增量保留已有工具；`names=[]` 且 `action=add` 只查目录。卸载须显式 `remove`，替换额外集合须显式 `replace`，其中 `replace` 配合空列表清空额外工具。核心工具仍受任务允许范围约束；子任务不能借发现扩大 `allowed_tools`，搜索加载仍要求用户已启用连接。新 schema 仅进入下一轮模型请求，不能在发现工具的同一批调用中调用尚未提供 schema 的工具。注册目录中的名称并不等于本次请求已提供的工具。

同一对话创建新 Run 时，从所选完整历史中与 assistant 的 `discover_tools` 调用 ID 匹配的真实工具回执恢复 `loaded` 集合，再按当前注册目录、`allowed_tools` 和搜索开关过滤并持久保存。历史分支仅使用锚点内的不可变前缀，不读取父 Run 后续加载的末态；用户 JSON、其他工具内容和没有结果的发现调用均不能选择工具。Run 已显式设置的集合（包括空集合、卸载和替换结果）保持原意，恢复不扩大执行权限或绕过 ToolExecutor 审批。

三个适配器先校验整批调用，全部通过后才发出 `tool_call`；身份校验失败前已收到的真实 usage 仍会发出并由运行层保存。`provider_tool_identity.details` 区分未知名称、无效名称、缺失/无效调用 ID 和重复 ID，提供协议类型、批内从零开始的调用序号、最多 128 字符的工具名、允许集合数量及指纹，不放入调用 ID、原始参数或 SSE 全文。此处序号不等于 Responses 的 `output_index`。

执行层仅在 `provider_tool_identity` 且本批没有暴露工具调用时，记录本次失败和真实用量，加入当前工具定义约束后重新生成一次；再次失败保留结果并暂停，允许用户继续重新规划。这是工作台的有界纠错策略，不是厂商协议承诺，也不是 HTTP transport 重试。它不执行失败批次、不猜工具别名、不造调用 ID、不自动加载 schema，纠错分支不主动重放此前已完成操作；参数 JSON 错误等其他失败不因此获得自动纠正。

## 引擎与思考参数

`Generation.engine_kind` 独立于 `provider_type`，可为 `auto/sglang/vllm/llama_cpp/tensorrt_llm/other`。默认 `auto` 仅在 loopback 地址与本机部署清单的地址及模型标识匹配时识别引擎；旧配置在本机显式选择 `cache_observation=sglang` 可作为兼容提示，远程地址不据此推断。明确选择引擎不更换端点或进程，也不证明该部署的模型能力。

原生预设使用`thinking_mode=default`、`reasoning_effort=null`时保留服务模板默认行为，旧冻结配置缺字段也使用这些默认值。`reasoning_adapter=auto`只映射SGLang/vLLM Chat协议与已确认Qwen3家族模板的非默认开关，排除固定思考变体；使用`chat_template_kwargs.enable_thinking`。Qwen3.8强度同步设置模板内与顶层`reasoning_effort`，实际允许`low/medium/xhigh`。关闭思考与非空强度冲突时明确拒绝；其他未确认组合的非默认自动映射报错，不静默忽略。

当前实现另提供显式`openai_effort`和`anthropic_adaptive`原生适配。它们要求协议匹配并使用连接中声明的`native_reasoning_efforts`，不会因服务名自动获得能力：前者对Chat发送`reasoning_effort`、对Responses发送`reasoning.effort`；后者使用`thinking`与`output_config.effort`。OpenAI式关闭思考必须明确声明`none`，Anthropic式关闭使用`thinking.type=disabled`。这些是实现合同，缺真实外部提供方的验收边界保持不变。

2026-10-19增加的`prompt_template`支持原生、工作台五档（低/中/高/极高/Max）及自定义通用/分档行为指令。工作台与自定义预设改变的是受控提示词，不执行任意Jinja模板，也不将五档当作所有模型的原生请求参数。已确认Qwen3.8自动适配在非原生预设下以原生medium作为中性基底，避免原模板默认xhigh抵消指令。任务/子任务选择会按连接实际选项验证并冻结，主模型与独立审核的配置分开处理；接口、预览/保存及说明入口见[AI配置说明](../../../../../../docs/technical/ai-configuration.md)和[AI操作手册](../../../../../../docs/ai-guide.md)。

SGLang 的请求 `rid` 和 loopback `/abort_request` 定向取消按引擎识别，与 `cache_observation` 解耦；仅选择当前请求 ID，固定 `abort_all=false`。其他引擎未接入专用取消扩展，采用关闭 HTTP 流的边界，不能据此声称服务端立即停止推理。

## 输入计数

`engine_adapters.count_input_tokens(config, messages, tools)` 同步返回 `(count, source)`，`source` 仅为 `tokenizer` 或 `estimated`。精确路径限于 loopback SGLang、Chat 协议及纯文本输入，复用 `_request` 的真实消息与工具转换，向相同 API 前缀的 `/tokenize`（默认 `/v1/tokenize`）发送 `model/messages/tools/chat_template_kwargs/reasoning_effort`；不发送生成、流式、缓存观察或 `rid` 字段。因此工具 schema 与思考模板条件也计入输入，而非只统计可见正文。返回计数用于预算，不冒充生成请求的实际 usage，也不据 `max_model_len` 自动提升用户冻结的上下文上限。

计数在数据库锁外通过线程调用；请求配置 HTTP 超时 3 秒，禁用环境代理与重定向，响应上限 4 MiB。最多缓存 128 项、有效期 30 秒，仅保存请求摘要键、计数及时间。其他引擎/协议、远程端点、原生图片/PDF/音频/视频或其他非文本块均使用现有 UTF-8 估算并保留附件预留，标记 `estimated`；tokenize 网络失败、无效计数或响应超限也按此回退。旧版或未提供同模板 tokenize 的兼容服务不能标为精确计数；未经确认的多模态计数不走纯文本精确路径。

## 限制与验证状态

2026-10-28本机兼容增量：`managed.verified_contract`只对严格匹配清单的本地配方GET`/server_info`，有界读取并短期缓存版本/模型身份。已审0.5.21的[`get_cached_tokens_details`](https://github.com/sgl-project/sglang/blob/v0.5.21/python/sglang/srt/managers/scheduler_components/output_streamer.py)在零命中时返回None；仅完整usage/DONE采用该零值语义。`chat_replay`以已过滤实际输入的连续指纹限定Qwen当前工具链思考，跨新输入、任务、模型或来源变化不重放；计数和请求复用同一投影，摘要不读入这些私有字段。这些行为不改变未知引擎与远程协议，不修改MTP、缓存容量或服务端模板。

HTTP transport `retries=0`，适配器没有隐藏重试；生成超时按无有效事件的空闲时长计算，同时保留 HTTP 分阶段超时，持续正常输出不会因为总用时超过该值被中断。依据：[HTTPX timeout](https://www.python-httpx.org/advanced/timeouts/)。运行层对安全的 Chat 纯文本 length 片段最多自动接续 16 次，每次重新排队并计实际模型尝试；不完整工具或不透明协议不会盲目续接。SSE 按 UTF-8 增量读取，支持 LF、CRLF、CR 与多行 data；未结束的帧被丢弃，缺少协议终止事件会报错。单请求上限 16 MiB、总流上限 32 MiB、单事件/行上限 2 MiB、单工具参数上限 1 MiB、输出块/工具索引上限 128。超限明确失败，不能执行部分工具参数。

Python 编译及项目 Ruff 辅助检查已通过。后续本地 OpenAI-compatible / SGLang 连接已有真实文字流、工具往返、图像及工作台持久问答证据；2026-10-18 的 `audit-chat-ui-evidence.json` 还记录默认模型继承、连接/版本预检、补充输入与刷新正文。没有使用模拟服务或假模型响应填补证据。外部 Responses/Anthropic 的真实调用、reasoning opaque 回放、额度错误及原生 PDF/音频理解仍 **等待真实模型连接验收**；本地 Chat 通过不等于跨厂商所有扩展均支持，也未执行付费 API 验收。

本轮工具加载、协议纠正、引擎参数和计数边界已有受控协议单元检查；这些 fixture 仅验证辅助协议约定，不作为真实模型或 UI 验收证据。跨引擎思考参数、其他 SGLang 版本、远程 tokenizer 与多模态精确计数均未由这些检查证明。固定模型 MTP、128K 真实长输入及工作台流程的实际结果与边界见[状态清单第9节](../../../../../../docs/implementation-status.md#9-2026-10-18-上下文与推理增量)。
