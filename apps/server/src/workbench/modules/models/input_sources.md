# Directional capabilities and native input codecs

核对日期：2026-10-18。本轮已使用 **OpenAI Docs** skill 核对当前官方请求说明；不安装 OpenAI/Anthropic SDK，服务仍使用已有 httpx。以下记录协议结构与本地编码检查，**没有发付费 API 或当前模型请求，也不是远程模型能力验真**。

## 公共接口与职责

`provider_inputs.encode_extra(block, provider_type) -> dict` 为纯函数。当前 `providers._input_content` 已在 user 消息的原生块分派中调用此函数。输入恰为 `{type, mime_type, filename, data}`，type 取 native_document/native_audio/native_video，data 是非空规范标准 base64。输出为一个新的 provider user-content wire object；不修改 block，不读文件、发网络、上传文件、执行文档或转换成提取文本。

provider_type 同现有适配器：openai_compatible（别名 openai_chat）、openai_responses、anthropic（别名 anthropic_messages）。openai_compatible 指 wire 协议而非端点供应商认证；任意同协议服务不能因此自动获得 PDF/音频能力。

`files.inputs` 已接入 artifact/version身份、真实 MIME/格式、远程同意和预算检查，仅在 user 内容中编译原生块。其有界元数据入口复用受限 file_processor 产生的可用 extracted 子制品，API 不直接解析原件：PDF最多8MiB/10页；MP3/WAV最多4MiB/120秒且仅含音轨；没有合格元数据就不开放该路线。发送前重新核对原件hash/版本与能力/同意，二进制上下文合计最多8MiB；这些限制不等于所选外部模型已通过原生输入验证。本 codec 再拒绝未知字段/URL/file_id/路径、无效或非规范base64、未知协议、MIME/后缀不匹配与内容头不一致。filename 仅普通文件名；不接收路径/URL或控制/代理码点。base64最多16Mi字符，沿用现有16MiB请求上界的防御性单块限制；完整JSON/消息聚合仍由宿主/现有provider请求上限约束，不能将单块上限理解为保证整个请求可接受。

内容头仅做 PDF `%PDF-`、WAV RIFF/WAVE、MP3 ID3/合法MPEG header 基础一致性检查，**不是完整文件解析、安全扫描或模型支持证明**。不会执行 PDF JavaScript/宏/嵌入链接。音视频提取、ASR和文件转换属于另一路显式选择，不能暗中作为原生模式回退。

错误为现有 AppError：`provider_native_input`（422，validation）或 `provider_unsupported_content`（422，capability），不把原始base64、文件名/路径或底层异常内容附在错误中。

## 当前实际 wire 子集

| canonical 输入 | Chat Completions | Responses HTTP messages | Anthropic Messages |
| --- | --- | --- | --- |
| native_document / application/pdf / .pdf | file，内层file.filename+file.file_data | input_file，filename+file_data | document，source.type=base64/media_type=application/pdf/data |
| native_audio / MP3、WAV | input_audio，内层data+format=mp3或wav | 本轮明确unsupported | 本轮明确unsupported |
| native_video | 本轮明确unsupported | 本轮明确unsupported | 本轮明确unsupported |

PDF 两种OpenAI路线的file_data采用 `data:application/pdf;base64,<data>`；Anthropic source.data 使用裸base64，不添加dataURI。最小Anthropic document协议没有本codec所需的filename字段，不传自造filename；canonical filename仅参与本地一致性验证。Chat input_audio也只传官方data/format字段，输出仍是文本，本轮不启用audio输出modalities。

音频 MIME 白名单为 audio/mpeg、audio/mp3（.mp3），audio/wav、audio/x-wav（.wav）；格式字段严格为mp3/wav。不接受PCM裸流、FLAC、OGG、AAC或视频容器，不猜自动转码。

### 官方依据与证据边界

- [OpenAI File inputs](https://developers.openai.com/api/docs/guides/file-inputs)：Chat仅PDF file parts；Responses有input_file。Base64示例明确两种OpenAI形状，且需合适的模型。Responses可接受的其他文档格式**不在本轮有界codec**。
- [OpenAI Chat create reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)：user内容有file和input_audio；input_audio.data为base64，format为wav/mp3。该user内容union没有原生视频内容块。
- [OpenAI audio Chat guide](https://developers.openai.com/api/docs/guides/audio-chat-completions)：原生音频输入选择Chat与音频模型，不应把输出音频配置强加给仅文本输出。
- [OpenAI Responses Python schema reference](https://developers.openai.com/api/reference/python/resources/responses)：索引包含ResponseInputAudio名称，但本轮核对的message content union及HTTP路线没有确立可用音频消息路线；**孤立类型名不足以证明此服务HTTP适配可用**，因此保守拒绝，不声称所有OpenAI产品永远不支持音频。已实际打开[ResponseInputContent schema](https://developers.openai.com/api/reference/python/__sdk_schema?declaration=%28resource%29+responses+%3E+%28model%29+response_input_content+%3E+%28schema%29&selected=%28resource%29+responses)，union只有input_text/input_image/input_file；另一个ResponseInputAudio独立schema确有mp3/wav，但不在此union。直接create reference本轮web fetch两次Internal Error，未借错误页猜结构。
- [Anthropic PDF support](https://platform.claude.com/docs/en/build-with-claude/pdf-support)：base64 document/source结构与PDF页图/文字处理，密码保护PDF需由调用方拒绝。
- [Anthropic Messages create reference](https://platform.claude.com/docs/en/api/messages/create)：content有document/image/text及工具等类型；本轮未找到原生audio/video块。此适配器显式unsupported，不把ASR或逐帧图片冒充原生音视频。

没有添加非标准SGLang video_url/video_data或其他厂商扩展。需要未来支持时应先按确切部署/版本核对协议、预算、预处理和真实短片，再增加独立受限路线；不能仅因用户把video声明为supported就发送猜测字段。

## 固定能力方向与旧数据

profile.CAPABILITIES现在共11项，方向由只读CAPABILITY_DIRECTIONS给定：text=input_output；image/audio/video/native_document=input；image_output/audio_output/structured_output=output；tool_calling/stream/prefix_cache=behavior。

`normalized(values)` 为旧8项数据补新项并保留support/enabled/evidence信息；旧存储没有direction或带错误direction时均按代码固定目录重建，不把旧值当authority。新增项默认support=unknown。Declaration仍仅support/enabled/note，extra=forbid，不能提交direction或伪造verified证据；Capability自身direction属性冻结。

`profile.adapter_route_matrix(provider_type)` 返回每项 `{direction,status,reason,formats,verification}`。verification固定not_evaluated，是连接无关的实现描述，不能覆盖某部署的实际证据。状态：

- available：已有provider请求/响应适配。text、image、tool_calling、stream，以及PDF（三路线）、MP3/WAV（Chat）为此状态；image仅当前已有PNG/JPEG/WebP。PDF/音频已接通编码、输入计划和provider分派；available只描述已实现路线，真实模型仍须独立验真。
- unavailable：未实现或未核实的路线；video、image_output、audio_output、structured_output均明确为此状态。工具返回JSON不是schema约束输出，不能据此声称structured_output可用。

prefix_cache的available只表示现有usage字段观察路线，不保证服务实际启用/命中，也不表示支持清全局缓存、强制缓存策略或跨连接复用。缺usage字段继续unknown。用户支持声明、代码路线、连接验真是三类事实，必须分别显示。

## 本地辅助检查与待验收

`/tmp/workbench-provider-inputs-20261018/results.json`：使用已有官方whisper.cpp源码内实际jfk.mp3，76447B，Chat input_audio编码/解码逐字节相等。没有再次下载录音/模型，没有调用ASR或生成模型。另以同上游源码内真实jfk.wav（352078B）核对audio/wav、audio/x-wav两别名及openai_chat别名，均原始bytes往返相等（wav-point.json）。

同一真实录音的11种无效/不支持协议形状被明确拒绝（跨协议audio、video、未知provider、URL字段/URL data/dataURI、路径filename、MIME/内容不符和无效Unicode filename等）。这是协议拒绝机制检查，不是制造业务音频或模型结果。旧profile保留enabled/evidence，固定11方向，新项unknown，Declaration拒绝direction，矩阵不跟随用户支持声明。

`pdf-point.json` 已记录主agent真实Chrome Office-to-PDF制品 `project-design-export.pdf`（5页、209614B）：Chat/Responses/Anthropic三路线编码后解码逐字节相等，network_calls=0。这证明真实原件的本地编码，不证明外部模型理解结果。

主适配、输入计划、远程同意和有界元数据入口已接通；仍缺外部凭据进行所选模型的真实原生PDF/音频请求验证，最终UI闭环由主agent在真实条件下验收。没有使用外部业务凭据或发模型请求。
