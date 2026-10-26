# 2026-10-17部署的缓存与视频调查

调查日期：2026-10-17。初始调查仅检查本地安装源码、包元数据和非推理接口 `/server_info`、`/metrics`。随后主 agent 明确交接唯一推理槽，授权执行下述一次真实冷/热两请求，结果见实测表；没有追加SSE或视频请求，没有修改运行配置、清缓存、安装依赖或重启服务。

本页运行值与“当前”描述均指当次调查快照，不是1.0.0的实时配置。现行本机部署为131072上下文、MTP开启、推理并发1，后续metrics与真实流式缓存证据见[模型部署说明](README.md)及[实现状态](../../docs/implementation-status.md)。保留下表和两请求原始数值，不把8K样本换算为128K性能；视频仍未取得真实输入验收。

适用范围：SGLang **0.5.21**，官方提交 `e00930c5489053f26d86b179cee0d087f846acbb`；模型固定 revision `fc694b54fb0174e0913e6adf86691ef85a4ead47`，当前实际类 `Qwen4ExpForConditionalGeneration`。本地源码根为 `.runtime/model-env/lib/python3.12/site-packages/sglang/srt/`。链接均指向同一提交，不能用其他版本文档覆盖这里的实际实现。

## 已确认的运行值

2026-10-17T12:33:53 UTC，`GET /server_info` 返回 200；`GET /metrics` 返回 **404**。下表区分 tokenizer 顶层配置与 scheduler 的 `internal_states[0]`，后者包含加载后覆盖值。

| 字段 | 顶层 | scheduler 实际值 |
| --- | --- | --- |
| `enable_cache_report` | false | false |
| `enable_metrics` | false | false |
| `disable_radix_cache` | false | false |
| `uses_mamba_radix_cache` | true | true |
| `mamba_radix_cache_strategy` | extra_buffer | extra_buffer |
| `page_size` | 64 | 64 |
| `disable_chunked_prefix_cache` | false | **true** |
| `max_mamba_cache_size` | null | 70 |
| `mamba_track_interval` | 256 | 256 |
| `enable_hierarchical_cache` | false | false |
| `enable_unified_memory` | false | false |
| `enable_prefix_mm_cache` | false | false |
| `mm_process_config` | `{}` | `{}` |

**当次调查** context 8192、chunked prefill 1024、并发 1、MTP 关闭。后续部署先经过32768，再更新为131072/MTP；下面冷/热两请求数字仍属于原8K配置，不能改写成新配置的基准。`max_mamba_cache_size=70` 是状态槽数，不能解释成 70 个 prompt 或 token。`enable_unified_memory` 是 SGLang 的统一 KV 分配功能开关，与 GB10 硬件 UMA 是两个概念。

## 缓存上报与控制

**现在即可按请求取得缓存证据，无需打开全局 metrics 或重启。** OpenAI chat 请求接受 `return_meta_info: true`，非流式响应在 `choices[0].meta_info` 保留原始 `cached_tokens`。`return_cached_tokens_details: true` 可额外返回 `sglext.cached_tokens_details`，包含 `device`、`host`，启用 L3 时才有 `storage/storage_backend`。冷请求细分可能缺失；先用存在的原始 `meta_info.cached_tokens` 判断，不把缺失字段填零。源码路径：`entrypoints/openai/protocol.py:890`、`serving_chat.py:2293,2392`、`managers/tokenizer_manager.py:2422`、`scheduler_components/output_streamer.py:86`。

标准 `usage.prompt_tokens_details.cached_tokens` 由服务级 `--enable-cache-report` 门控；当前为 false，故既有响应的 null/缺失 **不表示零命中**。即使开关为 true，此版本也只在命中数大于零时创建该细分对象。多模态响应可能因 image/video 计数而有 details，但其中默认 cached_tokens 仍不能跨越该门控作为实际缓存证据。[对应官方实现](https://github.com/sgl-project/sglang/blob/e00930c5489053f26d86b179cee0d087f846acbb/python/sglang/srt/entrypoints/openai/usage_processor.py#L11-L51)。

`cache_salt` 是每请求缓存命名空间；空字符串按未设置处理。同一非空 salt 和同一 token 前缀可复用，另一个全新 salt 隔离匹配，不会清掉其他用户缓存。`extra_key` 也参与 key，但本次使用专用 salt 即可。OpenAI SDK 需将这些 SGLang 扩展放入 `extra_body`；直接 HTTP 则位于 JSON 顶层。没有在当前 chat schema 找到通用 `cache_control`、`prompt_cache_key`、TTL 或“本请求禁止写缓存”的契约，不能照搬其他提供方字段。新 salt 会留下正常 LRU 可淘汰条目；只建立一次测量对，不不断创建 salt。[请求字段](https://github.com/sgl-project/sglang/blob/e00930c5489053f26d86b179cee0d087f846acbb/python/sglang/srt/entrypoints/openai/protocol.py#L960-L964)、[RadixKey 命名空间](https://github.com/sgl-project/sglang/blob/e00930c5489053f26d86b179cee0d087f846acbb/python/sglang/srt/mem_cache/radix_cache.py#L61-L76)。

`--disable-radix-cache` 会关闭前缀复用，`--disable-chunked-prefix-cache` 是另一项特定优化，不能混同；这些是部署级控制，本轮不更改。不要调用 `/flush_cache`、`/set_internal_state` 或用重启制造冷态。

若未来明确安排一次部署更新，官方 `--enable-metrics` 才会安装 Prometheus 接口。源码定义 `sglang:cache_hit_rate`（prefill 报告区间的有效命中 token/有效总 token，非本请求专属）、`sglang:cached_tokens_total{cache_source=...}`（累计命中 token，来源 device/host/storage，无法细分时用 total）、`sglang:prompt_tokens_total` 和 `sglang:uncached_prompt_tokens_histogram`。不能把 fallback 的 total 与各来源再相加。当前 `/metrics=404` 应显示不可用。源码：`entrypoints/http_server.py:296`、`observability/metrics_collector.py:305,1632,1802`、`scheduler_components/metrics_reporter.py:780`。[官方指标实现](https://github.com/sgl-project/sglang/blob/e00930c5489053f26d86b179cee0d087f846acbb/python/sglang/srt/observability/metrics_collector.py)。未发现本次 chat 接口对缓存创建 token 的独立真实计量；Responses serializer 的 `cache_write_tokens: 0` 是硬编码兼容字段，不能当缓存写入事实。

## QSA / 混合线性注意力限制

本模型 48 层中每 4 层有 1 层 full attention，其余为线性 GDN；运行时使用 Mamba 状态池和 radix 组件维护混合状态。QSA 压缩比例为 4，full KV page 为 64。压缩 QSA 要求 paged full-KV 且 page 可被压缩比例整除，需要 extra-buffer 策略或关闭 radix；当前实际已选择 **extra_buffer + radix**，因此不能因为“混合模型”直接断言无前缀缓存。[Qwen4 参数约束](https://github.com/sgl-project/sglang/blob/e00930c5489053f26d86b179cee0d087f846acbb/python/sglang/srt/arg_groups/model_overrides/qwen4_exp.py#L20-L40)、[QSA pool](https://github.com/sgl-project/sglang/blob/e00930c5489053f26d86b179cee0d087f846acbb/python/sglang/srt/mem_cache/qsa_kv_pool.py#L70-L80)。

复用必须同时有可用 full-KV 和匹配的线性状态；有 KV 不等于整段全部可复用。Mamba 组件按 checkpoint 边界处理 Full-KV 命中，copy-on-write 到请求自己的状态槽，槽/缓存可因正常 LRU 逐出。当前 checkpoint 基础粒度为 max(FLA chunk64,page64)=64；decode 状态跟踪再与 interval256 取最小公倍数。因此热请求命中少于 prompt 总数可以正常，不能要求 100% 或臆造精确预期 token 数。源码：`runtime_context.py:1903`、`arg_groups/overrides.py:1796`、`mem_cache/unified_cache/components/mamba.py:158`。

当前 scheduler 的 `disable_chunked_prefix_cache=true` 来自加载期 gate：该项要求 MLA 和受支持 prefill backend；当前配置不满足。它不关闭 radix，也不等于关闭 chunked prefill1024。当前无 HiCache/外部存储、无跨服务持久前缀缓存；file-backed PLE 是权重 backing，**不是 prompt/KV 缓存**。`enable_prefix_mm_cache=false` 也不应被解释成所有图文前缀 KV 命中必然为零。[加载期 gate](https://github.com/sgl-project/sglang/blob/e00930c5489053f26d86b179cee0d087f846acbb/python/sglang/srt/model_executor/model_runner_components/misc_utils.py#L26-L50)。

现有工具验收日志出现过 `#cached-token: 384/1024`，这只能证明已有复用现象，未构成受控冷/热基准；本次不重新执行那些测试。

## 当次冷/热请求方法（已执行，结果见下表）

1. 主 agent 暂停新的工作台生成，保留当前服务/guard，确认唯一槽空闲。仅用 `/ready` 检查就绪；不用默认会生成 token 的 `/health`。
2. 从真实仓库文本读取一份固定上下文，例如 `runtime/model/README.md` 前 6000 字符，令实际 prompt 大于 512 token 且 prompt+8 输出不超过8192（可离线 tokenizer 确认）。只读取一次，两个请求的 messages 必须完全一致。
3. 创建一个从未使用的非空 UUID salt。顺序发送 **两次**相同请求，只让 `rid` 不同；第一份是命名空间冷态，第二份是该命名空间热态。两次之间不插入其他生成，不清任何缓存。不称为冷启动/JIT/磁盘冷态。
4. 分别保存完整请求/响应、UTC 时间和客户端 monotonic 总耗时；读取 `choices[0].meta_info.prompt_tokens/cached_tokens`，补充 `sglext.cached_tokens_details`。预期冷 cached_tokens=0、热>0，只有响应实际报告后才登记。若字段缺失记 unknown；若热=0，保留实际结果并检查匹配/逐出/状态边界，不自动重试或重启。
5. 计算各次 `cached_tokens/prompt_tokens`、未命中 token，以及同一输出上限的端到端耗时。两样本只是功能测量，不承诺显著加速或通用性能。非流式总耗时不是 TTFT；需 TTFT 时另经协调改成 SSE 并保存首 content 时间和最终 meta/usage，不额外补发隐藏请求。

请求模板（真实测量采用此形状，替换占位值）：

```json
{
  "model": "Qwen3.8-Flash-Next",
  "messages": [{"role": "user", "content": "<同一份真实仓库上下文>\n请只回答：已读取。"}],
  "max_tokens": 8,
  "temperature": 0,
  "stream": false,
  "chat_template_kwargs": {"enable_thinking": false},
  "cache_salt": "<本次唯一UUID，两次保持一致>",
  "rid": "<同UUID>-cold",
  "return_meta_info": true,
  "return_cached_tokens_details": true
}
```

目标 `POST http://127.0.0.1:30000/v1/chat/completions`。热请求仅将 rid 后缀改为 `-hot`；不要往 messages 填 cold/hot 标签。输出内容是否恰为“已读取”不决定缓存测量是否成立；以真实缓存字段为准。

### 实际冷/热两请求结果

2026-10-17T12:39:48–12:39:51 UTC，原服务 PID178083、原参数下顺序执行，唯一 salt 为 `workbench-prefix-measure-` 加一个 UUID。上下文只读一次真实 README 的全部4463字符（≤6000），两次messages完全一致，max_tokens8、thinking关闭。没有插入其他推理、flush、重启或重试。完整请求、响应及测量元数据位于 `.local/model/evidence/20261017T123948636746Z-prefix-cache/`，索引 `measurement.json`。

| 实际请求 | prompt tokens | cached tokens（原始meta_info） | 来源扩展 | 客户端端到端秒 | 输出 |
| --- | ---: | ---: | --- | ---: | --- |
| cold | 2100 | 0 | 缺失 | 2.521898 | 已读取。/4 tokens/stop |
| hot | 2100 | 2048 | device2048、host0 | 0.383122 | 已读取。/4 tokens/stop |

热请求真实命中97.52%，未命中52token；两者标准 `usage.prompt_tokens_details` 均为null，印证当前必须从扩展/原始meta取缓存数。两样本端到端比值约6.58，只作为这次固定短输出的观察，不是吞吐或一般加速承诺。冷态的0来自实际meta_info，不从扩展缺失推定。已将 manifest prefix_cache 更新为 supported/verified，指向本次原始证据。

### 工作台SSE适配的明确约束

当前 `serving_chat.py:1202` **明确拒绝** `stream=true` 与 `return_meta_info=true` 的组合，错误为 `return_meta_info is not supported with streaming`。不要将非流式测量选项直接用于工作台SSE请求。

流式可单独传 `return_cached_tokens_details:true`。源码 `serving_chat.py:2124,2188` 收集实际缓存来源，若有数据，在结束前发一块 `choices:[]`、顶层 `sglext.cached_tokens_details`，不要求打开 `enable_cache_report`；它与之后的 usage chunk 分开，最后才发 `[DONE]`。适配器要处理空choices扩展块，并按一个请求/attempt只结算一次，不能把每块当新增用量。初次调查没有追加 SSE；2026-10-18 工作台已完成真实流式缓存采集与看板核对，见文末补充。[流式入口与扩展实现](https://github.com/sgl-project/sglang/blob/e00930c5489053f26d86b179cee0d087f846acbb/python/sglang/srt/entrypoints/openai/serving_chat.py#L2124-L2200)。

`device/host/storage` 是互斥来源分解，可以相加得到这次扩展已上报的真实缓存读取token；没有 `total` 合计字段。`schedule_batch.py:223` 的 `split_cached_prefix_by_tier` 去除host/storage交叠后令 `device=prefix_len-host-storage`；`CachedTokensDetails` 的协议字段仅为device、host、可选storage及storage_backend。当前实测热请求扩展为 `{"device":2048,"host":0}`。若storage缺失，表示这个已确认来源细分没有L3字段，不另编造storage指标；整个扩展缺失则在SSE适配里保持unknown。当前 cache_report=false，忽略标准usage中的默认缓存0，避免覆盖已知扩展事实。[来源分解公式](https://github.com/sgl-project/sglang/blob/e00930c5489053f26d86b179cee0d087f846acbb/python/sglang/srt/managers/schedule_batch.py#L223-L259)。

## 视频协议与预处理（仅调查，未验证）

当前官方 OpenAI chat schema 接受 `video_url` 内容块，`video_url.url` 是字符串；不是 multipart，不是 image_url 数组。Qwen4 类注册在 QwenVLProcessor 中，`load_video` 支持服务端本地路径、`file://`、`data:video/mp4;base64,...` 或 HTTP(S) URL。URL 会下载实际内容；本地验收优先真实短片的 data URI，避免路径共享和外网依赖。[协议](https://github.com/sgl-project/sglang/blob/e00930c5489053f26d86b179cee0d087f846acbb/python/sglang/srt/entrypoints/openai/protocol.py#L587-L609)、[输入归一化](https://github.com/sgl-project/sglang/blob/e00930c5489053f26d86b179cee0d087f846acbb/python/sglang/srt/utils/common.py#L2028-L2094)。

本环境实际锁定 `decord2==3.4.0`，提供 `decord` Python 模块；真实 `import decord` 成功，版本3.4.0。`torchcodec` 不存在；官方 VideoDecoderWrapper 会 fallback 到 decord CPU 解码，并可为 bytes 创建临时 mp4。因此不能用 `metadata.version('decord')` 找不到发行包来断言缺失解码器。PyAV16.1.0存在，但不是此 wrapper 的备用分支。这里仅验证 import，没有解码样本，也没确认所有编码格式均可用。[解码实现](https://github.com/sgl-project/sglang/blob/e00930c5489053f26d86b179cee0d087f846acbb/python/sglang/srt/utils/video_decoder.py#L1-L95)。

**当前 per-item 采样参数存在实现限制。** schema 有 fps/max_frames/max_tokens_per_frame 等字段，模板解析会放进 VideoData.preprocess_kwargs；但是 QwenVLProcessor 实际调用 `preprocess_video(video, video_config=self.video_config)`，只读取服务级 `mm_process_config.video`。`load_video` 去掉 wrapper 后未把 per-item kwargs 合并到该配置。当前服务级配置为空，不能把请求中的 `fps/max_frames` 当可靠硬上限，不修改上游源码或运行服务来绕过。

实际 Qwen 默认：fps2，最少4采样帧、最多768帧，帧数向2的倍数对齐，均匀取样；源视频至少应有2帧、有效正fps。帧解码成 NHWC uint8，再转 TCHW，按像素预算 resize、pin memory，交给处理器形成 `pixel_values_videos`、`video_grid_thw` 和时间元数据。默认最小像素128×28²/帧、最大768×28²/帧；小片也可能被放大，不能仅凭文件尺寸估计token。源码：`multimodal/processors/qwen_vl.py:77,199,248,335,1024`、`parser/jinja_template_utils.py:240`、`utils/common.py:2069`。

待协调的最小视频模板：使用用户已有或获授权的**真实**短片，建议时长≤2秒、源帧数4–8帧、MP4/H.264常见编码、无须音频；先以现有 ffprobe 核实实际格式/时长/fps/帧数，不制作假视频、不下载替代内容。这种物理短片约束即使请求采样参数被忽略，也限制帧数。保留8192总上下文和8GiB内存保护，max_tokens128；视频输入/总token超限则停止并记录，不自动增加预算。模板先省略无法保证生效的fps/max_frames：

```json
{
  "model": "Qwen3.8-Flash-Next",
  "messages": [{"role": "user", "content": [
    {"type": "text", "text": "请按时间顺序描述这个真实短片中可见的变化；无法辨认的细节请说明。"},
    {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,<真实短片字节的base64>"}}
  ]}],
  "max_tokens": 128,
  "temperature": 0,
  "stream": false,
  "chat_template_kwargs": {"enable_thinking": false},
  "return_meta_info": true
}
```

须保存真实文件来源、ffprobe结果、请求、响应、usage及服务日志，人工对照真实时间变化。视频模型输入契约、decoder import和图片通过都不能替代视频实际验收；当前视频能力仍 unknown。音轨理解、任意长视频、屏幕坐标grounding也未验证。本轮没有真实短片，不执行视频测量，不修改视频能力状态。

## 2026-10-18 工作台流式计量验证

模型连接的 `generation.cache_observation=sglang` 现已通过真实界面保存，工作台发出真实 SSE 请求并读取缓存扩展。数据看板已有非空的可判断请求集合；主 agent 核对显示的 token 命中率等于这些真实请求的缓存读取总量除以输入总量。旧请求未返回字段时仍保留 unknown，不补零、不改写历史。此流程验证采集与聚合接线，不新增一组受控冷/热性能基准。证据为本机私有 `.local/installation-acceptance/memory-forget-cache-ui-evidence.json`，不随源码分发。
