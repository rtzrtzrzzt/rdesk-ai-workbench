# 本机生成环境

API与普通Worker不导入Torch。独立媒体Worker接收任务，按阶段启动由现有原生命令监督器拥有的进程，阶段结束即卸载自身权重，保留主Qwen运行。

当前使用BFL FLUX 3 Image Klein蒸馏NVFP4。源码固定`bfc8889a4497f846416f253a3972a1941ac698b9`，HF配置/权重revision为`337337621b8526e120f2275d250242f4a6bedd75`。依赖见[requirements.in](requirements.in)与[requirements.txt](requirements.txt)，采用默认PyPI源，不修改主模型环境。

```bash
uv venv --python 3.12 .runtime/media-env
uv pip sync --python .runtime/media-env/bin/python runtime/media/requirements.txt
git clone https://github.com/black-forest-labs/flux-3-image-klein.git .runtime/media-source
git -C .runtime/media-source checkout --detach bfc8889a4497f846416f253a3972a1941ac698b9
```

权重放在`models/black-forest-labs/flux-3-image-klein/`：根`ae.safetensors`；`variants/nvfp4/`中的模型、config与量化配置；`text_encoder/`中的两个分片、索引、config、tokenizer等官方小文件；`vae/config.json`。权重按上游许可证管理，不随Apache-2.0源码发行包分发，不在首次生成时暗中下载。

“设置 → 生成模型”可接入本机配置；“生成 → 生图”可独立使用。默认复用已配置的本地对话模型按BFL官方指南扩写提示词，不另加载9B PUP。编辑时实际提供安全参考图；当前对话模型图像输入预算为两张，更多参考图可关闭自动优化，直接交FLUX参考图编码器。自动优化可关闭，关闭后不声称做过扩写或视觉理解。

先在无权重阶段准备NVFP4内核，再按编码、参考编码、NVFP4采样、FP32 VAE解码依次加载。隔离环境的受控PATH包含已安装Ninja，MAX_JOBS=1；内核缓存位于`.runtime/media-cache`，由上游按依赖和设备版本区分。当前Spark使用safetensors0.8的pread和Transformers同步分片加载，避免mmap文件页与CUDA权重叠加峰值，不改原生数学。

2026-10-20内核准备与GPU执行分开：通过已有CUDA Driver绑定读取真实设备架构，在导入FlashInfer前显式选择相同架构缓存；内核阶段只编译、加载库并注册运算，不初始化PyTorch执行上下文或做无作业的CUDA同步。完成记录明确`host_compile_and_load`和GPU执行未验证，不以零峰值冒充测量；编码、参考编码、采样和解码继续保留原内存额度、同步和真实峰值统计。共享库加载仍有CUDA模块注册成本，不能承诺绝不占用内存。阶段入口将CUDA OOM转换为中文说明，原Worker仍在确认完整进程关闭后按原失败合同收尾，不自动重提；完整异常栈留在本机受管日志。新阶段进程直接读取更新的入口，无需重启模型或工作台服务。

默认单阶段预留10GiB、CUDA allocator额度9GiB、系统余量9GiB，依据实际MemAvailable准入并持续监测，只停止自身阶段。主模型占用已计入MemAvailable，不重复扣除权重大小；CPU offload不会增加统一内存。未证明完整进程树关闭时保留预留，阻止后续重推理。没有启动意图的预留经Run/Job所有权锁确认后可安全回收；Linux真实boot不同也可证明旧GPU进程已不存在，后者尚无实际重启验收。

本机已实际生成1024×1024、16步PNG并预览下载，参考图编辑也已生成512×512产物，主Qwen保持运行。首次内核编译实测约202秒，后续同环境缓存复用约0.67秒；这些是单次样本，不含全部提示词、装载与文件处理时间，不承诺固定总延迟。生成记录支持批量管理/删除，删除记录保留文件库文件。

2026-10-20优化后，真实页面独立生成及AI工具发起的1024×768/16步任务均成功；内核准备实际未建立活动CUDA上下文，随后编码、采样、解码均完成，原图下载尺寸核对正确。原Qwen NVFP4/131072/MTP及原内存保护保留。该验证排除了本次多余初始化路径，不保证任何负载或尺寸都不会耗尽内存。

对话中AI发起的图像、视频、音频及3D任务跟随各自生成工具的时间位置显示；等待、完成预览和失败反馈原位更新，不再统一放在对话末尾。任务自身状态决定刷新，父AI结束后仍可继续观察；独立“生成”页面保留原入口。

“检查配置”只验证文件和环境存在。只有实际生成完成才标记“已有成功生成记录”。设备限制与真实验收见[实现状态](../../docs/implementation-status.md)。
