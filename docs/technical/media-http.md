# workbench-media/1接入合同

这是1.0.0 已实现的媒体适配器接入合同；`workbench-media/1`是独立协议版本，不随应用版本改写。当前操作见[AI说明书](../ai-guide.md)，实际验收范围见[实现状态](../implementation-status.md)第16至18节；最新工具说明入口见[第22节](../implementation-status.md#22-2026-10-26-跨对话读取与-ai-操作说明)。本机FLUX已有真实生成证据，本合同及OpenAI Images外部提供方仍未完成真实远端生成验收。

这是本项目的媒体服务合同，不是第三方通用标准。用户可选择本机FLUX、OpenAI Images兼容接口或本合同。视频、音频、3D须接入实际实现相应协议的服务；填写模型名不等于完成适配。未接入时显示“没有模型接入”。

配置填写最终服务根路径，以下路径追加其后。可选凭据通过Vault注入`Authorization: Bearer ...`。无重定向或自动提交重试。

| 请求 | 合同 |
| --- | --- |
| GET /capabilities | 返回protocol="workbench-media/1"及capabilities字符串数组（image/video/audio/3d）。 |
| POST /jobs | 接收protocol、request_id、model、capability、input、references。另带Idempotency-Key，服务应按request_id去重。 |
| GET /jobs/{id} | 查询同一任务，返回id、status、可选progress、outputs。 |
| POST /jobs/{id}/cancel | 停止同一任务，返回相同格式；只有终态表示外部执行结束。 |

input包含prompt、width、height、seed、steps及对应类型的duration_seconds/voice/format；不支持的参数应明确拒绝。参考项含filename、mime_type、data_base64。status支持queued/running/succeeded/failed/cancelled；progress为可省略的0至1数值，缺失时界面不虚构百分比。id保持稳定，完成响应也须包含原id。

outputs至多8项，每项含filename、mime_type和data_base64或url。JSON响应上限128MiB，产物合计上限512MiB，大视频可提供下载URL。异源URL只允许公网并固定DNS结果，不转发API密钥；同源沿用明确配置的提供方边界；均拒绝重定向。产物进入统一Artifact文件库，前端只使用受保护的同源文件端点。

工作台先保存提交意图，再发唯一POST并持久保存回执。取得id后只查询或取消原任务；提交未知不重提。用户可在任务卡片填写真实提供方id恢复查询，或核实提供方未接受后关闭记录。明确取消不能被核对动作偷偷恢复。

OpenAI Images适配使用`/images/generations`和`/images/edits`，仅支持图像；同步API没有可假定的异步查询/取消入口，断线后须到提供方核对。[生成接口](https://developers.openai.com/api/reference/resources/images/methods/generate)、[编辑接口](https://developers.openai.com/api/reference/resources/images/methods/edit)。

参考图须核对归属和版本；远程传输同意绑定本次连接版本及文件版本，模型不能以布尔参数代替用户授权。截图及任意派生结果仅交本地模型。缺少真实服务或凭据时，协议与辅助检查不能代替实际生成验收。
