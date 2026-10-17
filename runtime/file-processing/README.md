# 原生文件增强组件

`workbench.modules.files.advanced.dispatch(plan, staging=None) -> dict` 是同步、无数据库的 **一次性文件处理子进程** 适配器。由主 FileRun 服务负责归属、计划持久化、资源配额、取消、租约、操作账本与 Artifact 发布。此模块不直接接受 HTTP 路径、不创建第二套发布协议，不向外部 OCR/ASR/API 服务传输文件。

## 接线协议

公共计划沿现有 processor：`input`、`output`、`filename`、`artifact_id`、`expected_hash`、`expected_bytes`、`operation`。路径必须在 `get_settings().data_dir` 内，不经过符号链接；输入必须是独立受管原件，不在 output 内。可选 staging 必须等于 output。调用前后核对真实文件 SHA256/字节数。最多256MiB输入，单输出16MiB，临时与输出合计64MiB/最多4096文件（调用方更小的文件/进程预算仍生效）。写入使用独占新文件，不替换原件/旧候选。

- `operation='ocr'`, `ocr={languages:['eng','chi_sim','chi_tra']的非空子集, pages:[页码], dpi:150或至200整数}`。PDF必须显式1至8个不重复页，单页最多1600万渲染像素；图像只支持单帧、1600万像素，先 EXIF 转正。逐词返回真实 Tesseract TSV，`locator` 包含 page/dpi 或 image、bbox{x,y,width,height,unit:'pixel'}、block/paragraph/line/word、confidence。图片坐标基于转正后图像，PDF坐标基于指定dpi渲染。置信信号不是准确率。最多100万字/2万词；不合并原PDF文字层，不推断版面/印章/签名/公式。**当前缺安全更新版引擎，三种语言均 not_installed，未做OCR效果验收。**
- `operation='convert_pdf'`：仅 docx/xlsx/pptx/odt/ods/odp ZIP包；拒绝宏、活动/嵌入对象、外部关系、外部资源/XML资源基址/Excel外部连接、Word动态域、未批准XLSX公式；复用既有 FormulaCell 验证。ODF公式当前明确拒绝。允许格式自身固定路径的 printerSettings 数字编号二进制，不开放任意bin。旧DOC/XLS/PPT/RTF没有安全接入，unsupported。新私有profile设置宏安全级别3、无可信目录，禁止自动恢复；源复制到私有临时目录，生成新 `export.pdf`。字体替换、动态字段或公式显示可能变化，不能承诺无损回环。源文件的 format/mime 保留，另有 output_format='pdf'/output_pages。
- `operation='video_frames'`, `video={start_seconds:number,end_seconds:number,max_frames:int}`：显式范围0..86400秒，区间>0且<=120秒，最多12帧；源视频最多1600万像素，输出等比最大1600×1600 PNG。每个采样位置取不早于它的实际帧，保留 copyts/showinfo 的源PTS，未能确认范围内时间戳则失败。输入格式白名单mov/matroska/webm/avi/mpeg/mpegts/ogg，协议仅file；不接网络流、设备、播放列表或任意FFmpeg参数。字幕/音轨仅独立stream元数据，不提取或编造字幕正文、ASR或完整音视频理解。非零起始PTS/特殊流时基需真实样本核对，不能假定采样请求秒数就是实际PTS。
- `operation='transcribe'`：显式本地CPU语音转写，公开选项 `asr={language:'auto'或固定上游语言码,timestamps:true}`，另需由服务端生成 `asr_runtime`，详见下节。此路线与视频抽帧、模型原生音频输入分别计能力。

返回统一 `{segments,metadata,warnings,complete,exports}`。`complete`表示所选处理范围成功，不代表版面保真/业务成功；OCR只选部分PDF页则false，帧丢失则false并警告。metadata包含 processor_revision='native-advanced:1'、source_artifact_id/source_version、input_version、source_refs。`input_version`始终为实际输入hash。可信 worker 可传 `source_refs=[{artifact_id,version},...]`（最多8、必须含主父原件），例如结构导出的中间Office尚未发布：引用必须保留真实JSON输入Artifact的版本，不能拿中间文件hash冒充原件版本。调用方负责授权这些引用。

`exports`：PDF `{filename:'export.pdf',display_name,mime_type:'application/pdf',kind:'export',source_refs}`；帧 `{filename:'frame-0001.png',display_name,mime_type:'image/png',kind:'frame_0001',timestamp_seconds,requested_timestamp_seconds,source_refs}`，逐帧唯一kind。主 worker 必须允许这些固定安全文件名、按唯一kind产生稳定 Artifact ID、保留每项时间戳与引用，沿原有 intent/publish/available 日志发布。此模块不发布、不注册DB表。

结构导出→PDF时，先在 attempt/generated_office 创建真实Office文件，再向 sibling attempt/pdf 调用转换（禁止输入在输出目录内）；实际生成文件hash作为expected_hash，原始JSON Artifact作为source_refs。主worker在受管staging规范收集输出；不得把JSON输入伪装为Office或把候选当已发布Artifact。

## 可选离线 ASR / whisper.cpp CPU

公共入口位于 `workbench.modules.files.asr`；导入不探测文件、执行程序或加载模型：

- `read_plan(*, environ=None) -> dict`：服务端私有配置与轻量真实引擎版本/常规文件预检，不加载模型。返回内容可能含本机路径，不能原样作为 API 响应。
- `capability() -> dict`：公开能力投影，沿用 processor 的 id/formats/operations/status/version/limitation，不返回路径。
- `freeze_plan(asr_options, *, environ=None) -> dict`：由可信 FileRun 服务/Worker在准入后调用；公开选项仅 language、timestamps，额外 engine/model/output 路径全部拒绝。返回冻结的 schema/version/revision、CPU/2线程、时长上限及真实 engine/model 文件身份。
- `advanced.dispatch(plan)`：消费已授权原件、`asr` 与 `asr_runtime`；子进程不从请求/环境重新选择模型，不查询数据库，不下载任何模型。

服务环境必须由本机所有者显式设置 `WORKBENCH_ASR_MODEL_PATH` 为已有 whisper.cpp **GGML Whisper 文件**的绝对路径；可选 `WORKBENCH_ASR_ENGINE_PATH` 为经过核对的1.9.5引擎。未设置模型时没有默认模型名称、Hub仓库或自动搜索回退。默认引擎是本项目独立 `.tools/asr/whisper.cpp-d1be6fde11ac6e0407606b4e42fe72d34add8037/build-cpu/bin/whisper-cli`。不读取 Flash-Next 权重，也不借用其 Python/CUDA 环境。

`freeze_plan` 的 engine/model/ffmpeg/ffprobe 记录包含 path、device、inode、bytes、mtime_ns、ctime_ns；引擎/模型拒绝符号链接/路径别名、空文件、非普通文件、不可执行引擎及非 GGML magic 模型。解码工具由服务端查找已有私有组件或PATH后冻结其真实canonical文件，而不依赖child的最小PATH，也不接收请求路径。仅读取模型4字节标头与stat，不把它称作权重完整校验。开始处理、解码后、识别后均核对四类冻结身份，发生改变则候选不能发布。该检查防止可检测的配置/文件替换，不承诺抵御同 OS 账号恶意竞态或取代操作系统权限。

主服务接线须在已有操作账本中保存选项/计划，并将它们写给一次性子进程：

```python
from workbench.modules.files.asr import freeze_plan

plan['asr'] = selected_asr_options  # 已经DTO限制的language/timestamps
plan['asr_runtime'] = freeze_plan(selected_asr_options)
```

引擎或模型未就绪不会伪报可用：未设置模型为 `not_configured`，配置模型但缺引擎/解码器为 `not_installed`，格式/预算/版本不符为 `not_configured` 并给明确原因；组件预检通过才提供 `transcribe` 操作，limitation明确尚不代表任意音频效果通过。**不设置模型的默认进程仍为not_configured/operations=[]**；本轮实际转录检查只在独立进程显式配置下文授权tiny模型，未自动修改正式或验收服务环境。

接受 `.wav/.mp3/.flac/.ogg/.oga/.opus/.m4a/.aac` 与实际 `audio/*` 检测；ffprobe还须确认时长明确、仅一个音轨且没有视频/附件流、1或2声道、时长大于0且不超过120秒。只允许 WAV/MP3/FLAC/OGG/AAC/MOV 音频容器及 file 协议，不接 URL、播放列表、设备、任意 FFmpeg 参数或视频音轨。受限 FFmpeg 明确转换为16kHz、单声道、16bit PCM；再次验证真实WAV参数和帧数后，固定 whisper.cpp CPU命令执行转写，不翻译、不启用GPU/批处理/VAD模型下载/温度回退。

沿用父处理器 **RLIMIT_AS 2 GiB、CPU120秒、外层180秒**，引擎子阶段墙时上限120秒、2个计算线程。配置模型文件不得超过512MiB，这是本版本保守资源限制，不保证所有该体积模型/120秒音频都能在预算内完成。不自动扩内存、延长超时、改GPU或换模型；超预算保持明确失败，保留原件。解码20秒、probe10秒、单个ASR派生输出4MiB，分段最多2000、总文字最多100万字符。本轮只测下文特定tiny/英文录音，其他模型/语言/时长的加载与准确率不能从单次结果推断。

输出仍是统一 result。`segments[].text` 来自引擎真实 transcription，locator 包含 `audio_segment`、`start_seconds`、`end_seconds`、`timestamp_source='whisper.cpp.segment_offsets_ms'`、`time_basis='decoded_audio_zero_origin'`。时间直接取实际 JSON offsets 毫秒换算，允许最多1秒尾端容差且不夹断/补造值；它是模型估计的片段时间，**不是精确字词或容器绝对PTS**。metadata分别记录实际解码PCM时长与源容器时长，避免把编码器延迟误当同一时间轴；requested/detected language分别保留。语言码取固定1.9.5源码的100个代码与auto，未执行付费语言探测。

两个稳定ASCII派生件由主worker沿现有发布日志收集：`transcript.txt` / kind=`asr_text` / `text/plain; charset=utf-8`，`transcript.json` / kind=`asr_segments` / `application/json`。JSON仅包含规范化真实文本/定位、来源版本及非秘密引擎元信息；不发布含模型路径的原始CLI JSON/systeminfo。两者都带原件 source_refs，不改原件。没有非空文字时输出实际空结果并警告，不能据此推断原音无语音；ASR文本始终需要对照原音核对。

显式准备引擎（仅官方固定源码，不下载权重、不安装Python包）：

```sh
.venv/bin/python scripts/services/file_components.py plan asr
.venv/bin/python scripts/services/file_components.py prepare asr
```

whisper.cpp1.9.5固定revision `d1be6fde11ac6e0407606b4e42fe72d34add8037`，CMake/C++原生CPU构建 `--parallel 2`，关闭CUDA/Metal/Vulkan/OpenCL/BLAS/OpenMP/RPC/CURL和server；原始源码及许可证保留在 `.tools/asr/`。至少1GiB临时空间，构建阶段若Linux MemAvailable低于8GiB仅停止本轮自有构建进程组，不动当前模型/API。manifest记录真实版本、flags、来源归档收据与空间，私有日志位于 `.local/asr/`。Windows helper不假装已能原生构建；对应平台引擎/ACL/取消与预算等待真实环境验证。

### 本轮明确授权的独立 tiny 模型

按 `hugging-face:hf-cli` 工作流使用本机既有 `hf`（huggingface_hub0.36.0），没有安装/更新全局CLI。Whisper.cpp固定源码明确指向 `ggerganov/whisper.cpp` 维护的GGML转换仓库；本轮只取 **tiny多语言** `ggml-tiny.bin`，固定HF revision `5359861c739e955e79d9a303bcbc70fb988958b1`，实际 **77,691,713B**，不取tiny.en、不改Flash模型。模型卡MIT；原OpenAI Whisper官方README明确代码及weights均MIT，原LICENSE保留在私有模型目录。详见sources与 `.local/asr/model-source.json`。

本轮实际等价下载参数为 `hf download ggerganov/whisper.cpp ggml-tiny.bin README.md --revision 5359861c739e955e79d9a303bcbc70fb988958b1 --local-dir <项目私有目标> --max-workers 1 --quiet`，显式默认 `HF_ENDPOINT=https://huggingface.co`、独立HF_HOME、禁止隐式token，不执行shell下载脚本、不上传任何输入。此命令是用户明确准备组件时的手动动作，**应用运行时不会调用它**。

本机已显式配置此已授权tiny，并由主agent在真实ASR页面完成上传、持久转录任务、派生结果与下载验收。新环境仍须给需要ASR的服务显式设置 `WORKBENCH_ASR_MODEL_PATH` 到项目实际绝对路径 `.tools/asr/models/ggerganov-whisper.cpp-5359861c739e955e79d9a303bcbc70fb988958b1/ggml-tiny.bin`，无需engine override；CLI选择模型不代表同一配置已应用到另一个Worker或另一台机器。

引擎whisper.cpp1.9.5为MIT，版权 `Copyright (c) 2023-2026 The ggml authors`；固定tiny转换模型卡和原OpenAI Whisper代码/权重均为MIT，原权重LICENSE版权为 `Copyright (c) 2022 OpenAI`。原文与来源继续保留在各私有组件目录，未纳入Git或改授Apache；再分发时须附完整版权/许可，详见根 `THIRD_PARTY_NOTICES.md`。这不改变其他模型或系统组件的许可。

## 安装、环境和失败

仅项目私有 Ubuntu24.04原生包，无sudo/apt install/maintainer脚本/系统配置修改：

```sh
python3 scripts/services/file_components.py plan office
python3 scripts/services/file_components.py prepare office
.venv/bin/python scripts/services/file_components.py status
```

依赖求解使用系统python-apt、默认Ubuntu官方源、no-recommends；首次锁定具体包版本/原始来源到 `.tools/file-components/packages.json`，后续prepare复用锁，不自动升版。保留原deb、逐包copyright和引用的公共许可证。私有Ubuntu包的bootstrap/registry绝对路径仅在项目副本重定位：直接绑定包内`.registry`，避免执行原本负责向/etc复制配置的维护脚本；UNO库必须先从私有program目录解析。启动环境是固定allowlist，HOME/TMP/XDG均指向本次私有临时目录，不继承DB/provider令牌、代理、显示会话或用户Office配置。版本探测使用会自动清理的私有临时HOME。单动作timeout：PDF渲染20s、Tesseract35s、Office90s、ffprobe15s、逐视频帧20s；整个FileRun仍受既有180s/120CPU秒/2GiB限制。达到时间/磁盘/stdout/stderr预算停止本轮自有进程树；所有子进程继承worker进程组以供公共取消。Windows取消完整链需真实设备验证。

这些是原生解析限制，**不是操作系统沙箱或网络隔离**。Office有宏/外链预检查及私有高安全profile，但不能据此声称恶意文档无法访问进程用户可读文件/网络；当前未安装Docling，也未声称其结构提取能力已验收。ASR只启用用户明确配置的本地模型，不自动下载其他模型。

组件缺失/语言缺失/不支持/预算/版本冲突通过明确AppError失败，原件保留；不回显原始stderr、正文、路径或秘密。超时或失败的输出只是未发布候选，必须由主worker清理/核对，不能将文件存在或子进程exit0当产品成功。失败不会改服务或重试外部转换。staging应为本次独有目录，调用方不得发布异常退出留下的文件。

## 本机辅助与实际UI证据（2026-10-17–18）

- Ubuntu24.04 aarch64：私有LibreOffice **24.2.7.2 420(Build:2)**，官方noble-updates包版本 `4:24.2.7-0ubuntu0.24.04.7`。56包，deb下载 **105088402B**；APT标称解包372361216B，实际常规文件368034929B，保留deb105088402B、公共许可证216492B，总约473.34MB（十进制，不含少量目录/锁元数据）。未改系统Office/库。
- 实际批准设计文档 `docs/technical/files-computer-ui.md` 的正文生成临时Office后，新PDF读回：DOCX 3页/165771B/1803提取字符；XLSX 6页/150614B/3384字符；PPTX 1页/80824B/765字符，三种原文件hash均未改变。全部自有辅助临时文件清理；没有创建业务记录或假样本，不代表复杂版面/最终UI验收。
- 复用 `/usr/bin/pdftoppm` **24.02.0**；现有用户安装的FFmpeg/ffprobe **7.0.2-static**（二进制自报 johnvansickle.com 构建、GPL/version3选项），本模块未下载该第三方构建。只核实本机版本/参数；验收库只读查询真实video/* Artifact数量为0，未合成媒体。视频帧时间定位/效果 **等待真实视频样本验证**。
- 本机tesseract可执行不存在；系统libtesseract5是修复后的 `5.3.4-1ubuntu0.1~esm1`，官方ESM引擎下载401。官方公开noble可用 `5.3.4-1build5` 没有该修复。初步解包的本次旧OCR组件已按主agent要求精确撤回（未执行文档OCR），未更改系统ESM库。helper不回退到旧公开包；需要可授权取得的修复版官方组件/语言包后再运行OCR辅助验收。
- ASR准备阶段：官方固定whisper.cpp源码归档 **9,631,456B**，私有源码/构建累计 **63,944,979B**（首次manifest前计数），CPU-only/-j2构建 **29.314秒**，真实 `--version` 为1.9.5、所需 `--help` 参数检查通过；原始manifest `.tools/asr/manifest.json`，构建日志 `.local/asr/build-1792251289229775984.log`。该准备步骤不下载/加载权重；后续仅按追加明确授权准备上文独立tiny，未改uv锁或Flash模型服务。
- 官方固定源码自带真实 `samples/jfk.mp3` 录音 **76,447B**，初步 ffprobe+解码：容器11.088秒，原2GiB地址空间限制下得到16kHz单声道16bit PCM **176000帧/11.0秒/352078B**，本轮临时WAV已清理。记录 `.local/asr/precheck.json`。无模型配置准确返回not_configured；请求model_path、关闭timestamps、非法语言码及实际非模型文件作为模型均准确拒绝，没有合成音频。
- tiny实际一次转录：经主入口现有 `file_processor`，CPU-only/2线程、原 **2GiB/CPU120/墙180秒**，exit0，实际墙时 **1.858秒**，检测en，一段 **0.0–10.5秒**，生成真实transcript.txt/JSON及source refs。采样最大单进程RSS **154,275,840B**、VMS **765,988,864B**，owned进程树RSS和峰值 **189,739,008B**（不是唯一物理内存），系统MemAvailable最低 **21,992,697,856B**；未触发8GiB保护。原始结果/完整文本/定位见 `.local/asr/transcription-1792251960065972496/data/attempt-native-paths/result.json`，汇总 `.local/asr/transcription-evidence.json`。只有这段英语录音和该tiny配置得到实际证据，不外推中文/噪声/长音频准确性或吞吐。
- 首次严格子进程尝试在加载ASR前因最小PATH找不到用户已有FFmpeg以file_component_missing/0.164秒失败，证据同目录 `evidence.json`；已修复为服务端冻结两个解码器真实路径/身份，再按同模型同预算完成上项，没有静默放宽或换模型。所有检查均未创建业务DB记录/调用Flash或API，来源ID是明确的官方录音来源而非伪造业务artifact。静态检查通过；后续本机真实UI验收见下项，不能把早期机制检查本身当作产品验收。
- 主agent真实ASR页面验收（2026-10-18）：上游实际JFK录音原件 **76,447B**，`transcribe`持久任务状态 **succeeded**、无error；派生结果与 **107B** 转录文本下载已通过，记录浏览器page_errors为0。证据 `.local/installation-acceptance/native-ui-evidence.json`，与上述模型来源/原生转录机制证据分开保存。仅确认本机所选tiny的真实产品路径，不声称识别准确率、中文/噪声/长音频或跨平台已做基准测试。

本机ASR上传→队列→转录→不可变派生件→Chrome下载已由主agent真实验收；其他平台、取消/异常及更广语言/输入质量仍须对应真实条件验证。这里的CLI、库读回和版本探测继续只作辅助，不替代实际UI流程或准确率基准。

最终局部辅助检查：ruff/py_compile通过；真实截图请求在识别前因缺引擎拒绝，原件版本冲突拒绝，自有短进程达到timeout后已停止并回收。
