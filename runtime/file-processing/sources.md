# 官方来源与许可证

核对日期2026-10-17。版本事实以本机执行与`.tools/file-components/packages.json`精确锁为准。没有新Python依赖。

- Tesseract官方CLI、TSV、语言参数：[Command-Line-Usage](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html)、[安装与Apache2.0许可证](https://tesseract-ocr.github.io/tessdoc/Installation.html)。中文与英文是独立traineddata能力，当前引擎未安装，未以语言文件存在冒充可用。
- Ubuntu官方安全事实：[CVE-2026-88047](https://ubuntu.com/security/CVE-2026-88047)标记noble修复`5.3.4-1ubuntu0.1~esm1`来自ESM Apps；[公开noble包](https://packages.ubuntu.com/noble/libtesseract5)、[官方ARM包目录](https://ports.ubuntu.com/ubuntu-ports/pool/universe/t/tesseract/)提供旧`5.3.4-1build5`。不修改APT配置、不跨Ubuntu发行版拼新glibc、不降级系统已修复库。
- LibreOffice官方：[headless/convert-to/UserInstallation参数](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html)、[宏安全等级](https://help.libreoffice.org/latest/en-US/text/shared/optionen/macrosecurity_sl.html)、[MPL2.0与第三方许可](https://www.libreoffice.org/licenses/)。本次Ubuntu默认noble-updates官方ARM64包由APT读取签名源元数据并下载；逐包URI/版本/架构/字节数/原档与copyright路径保存在本地锁。Ubuntu原生包不是可原地搬运的官方通用tar包：局部重定位bootstrap、UNO加载顺序和.registry绑定均记录在安装脚本中，没有改源系统。
- FFmpeg官方：[参数、seek、frames与stream选择](https://ffmpeg.org/ffmpeg.html)、[showinfo源PTS](https://ffmpeg.org/ffmpeg-filters.html#showinfo)、[protocol_whitelist](https://ffmpeg.org/ffmpeg-protocols.html)、[许可证规则](https://ffmpeg.org/legal.html)。使用既有二进制，不借官方文档为第三方构建背书；实际build启用GPL/version3，未由本模块重新分发/下载。
- Poppler官方：[项目](https://poppler.freedesktop.org/)，本机Ubuntu`pdftoppm -v`24.02.0；复用现有组件。

`.tools/file-components/root/usr/share/doc/<package>/copyright`保留56个包各自版权清单，`.tools/file-components/licenses/`保留被引用且系统实际提供的11个公共许可证副本。LibreOffice主代码MPL2.0，依赖并不统一为MPL；不得将汇总名称替代逐包许可。来源锁含公开包元数据，不含APT认证配置、Cookie、环境或账户秘密。

## ASR 原生可选引擎（2026-10-17）

选择 **whisper.cpp1.9.5**，官方2026-10-06发布，固定提交 `d1be6fde11ac6e0407606b4e42fe72d34add8037`。初始只构建CPU引擎；后续依追加明确授权准备下文独立tiny并做一次真实转录。没有新增Python依赖；应用运行时不下载或自动选择Whisper模型。

- [官方稳定发布](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.5)、[固定官方源码](https://github.com/ggml-org/whisper.cpp/tree/d1be6fde11ac6e0407606b4e42fe72d34add8037)：维护中的原生实现，ARM/NEON CPU路径；本机Ubuntu24.04/ARM64真实构建已通过，不据此声称Windows/macOS成套验收。
- [固定CMakeLists](https://github.com/ggml-org/whisper.cpp/blob/d1be6fde11ac6e0407606b4e42fe72d34add8037/CMakeLists.txt)、[固定GGML构建开关](https://github.com/ggml-org/whisper.cpp/blob/d1be6fde11ac6e0407606b4e42fe72d34add8037/ggml/CMakeLists.txt)：确切1.9.5版本、静态库、CPU-only、无CURL/server及不主动拉取OpenMP等组合，实际flags记录在本机manifest。
- [固定CLI解析与JSON输出](https://github.com/ggml-org/whisper.cpp/blob/d1be6fde11ac6e0407606b4e42fe72d34add8037/examples/cli/cli.cpp)：使用已核参数 `--version`、`--model`、`--file`、`--no-gpu`、`--threads`、`--processors`、`--language`、`--best-of`、`--beam-size`、`--no-fallback`、`--output-json`、`--output-file`、`--no-prints`。非full JSON的transcription[].offsets.from/to为毫秒，直接派生自引擎段时间单位乘10，不把网页示例当实际转录结果。
- [固定模型加载与语言码](https://github.com/ggml-org/whisper.cpp/blob/d1be6fde11ac6e0407606b4e42fe72d34add8037/src/whisper.cpp)、[GGML定义](https://github.com/ggml-org/whisper.cpp/blob/d1be6fde11ac6e0407606b4e42fe72d34add8037/ggml/include/ggml.h)：依据实际loader的GGML magic及100语言代码；预检标头和stat并不等于实际权重完整性/正确性或模型许可证核验。
- [官方MIT许可证](https://github.com/ggml-org/whisper.cpp/blob/d1be6fde11ac6e0407606b4e42fe72d34add8037/LICENSE)：原始源码归档、上游LICENSE及所含第三方声明原样保留，不重新改授Apache；当前只在Git忽略的私有组件目录安装，不作为本项目源码捆绑。

实际归档来源为固定提交的官方 codeload HTTPS URL；manifest中的archive_sha256是**本地下载收据**，不是上游签名或独立发布校验值。体量、版本与CLI参数由真实构建/执行产生。官方源码内的 `samples/jfk.mp3` 最初用于真实音频解码预检，随后仅按追加授权进行下文实际ASR；未下载其他媒体、未用于编造结果。

追加明确授权的tiny多语言模型来源：固定Whisper.cpp [models/README](https://github.com/ggml-org/whisper.cpp/blob/d1be6fde11ac6e0407606b4e42fe72d34add8037/models/README.md) 明确指向维护者 [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp)。本轮使用[固定HF revision](https://huggingface.co/ggerganov/whisper.cpp/tree/5359861c739e955e79d9a303bcbc70fb988958b1)中的 `ggml-tiny.bin`，不是英语专用tiny.en。官方HF API实际大小77,691,713B；一次下载后SHA256匹配LFS `be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21`，SHA1匹配上游模型表 `bd577a113a864445d4c299885e0cb97d4ba92b5f`。不重复哈希已验收的Flash权重。

HF转换模型卡标注MIT；原始OpenAI Whisper[固定README的License段](https://github.com/openai/whisper/blob/86098128c0b4f24f0e2aa2994de830614b474227/README.md#license)明确代码及模型weights均为MIT，[同修订原LICENSE](https://github.com/openai/whisper/blob/86098128c0b4f24f0e2aa2994de830614b474227/LICENSE)已原文保存在私有模型目录 `LICENSE.openai-whisper`。这是本tiny工具模型的具体许可证据，不能用于推断Flash-Next或其他模型的许可。

下载遵循已读取的 `hugging-face:hf-cli` 工作流，使用现有hf0.36.0、官方默认HF_ENDPOINT、固定revision/单一明确文件与README、独立HF_HOME、禁止隐式token；未执行shell下载器、未安装/更新全局hf工具、未外传用户文件。实际模型来源收据 `.local/asr/model-source.json`。源代码已有真实jfk.mp3在追加授权后用于一次实际ASR，原始文本/时间戳与资源证据位于 `.local/asr/transcription-evidence.json`；不是伪造样本或由另一个模型生成的参考结果。

对比候选未引入：官方 [faster-whisper1.2.1](https://github.com/SYSTRAN/faster-whisper/tree/v1.2.1) 采用MIT、Python>=3.9；[默认PyPI元数据](https://pypi.org/pypi/faster-whisper/1.2.1/json)列CTranslate2、huggingface-hub、tokenizers、onnxruntime、PyAV、tqdm等运行链。本机均未安装。官方 [CTranslate2安装文档](https://opennmt.net/CTranslate2/installation.html)及[4.8.3 PyPI元数据](https://pypi.org/pypi/ctranslate2/4.8.3/json)确认Python3.12 Linux ARM64轮子（17,565,485B）；这不代表整个依赖链或模型已经具备，也不是单包即可完成本需求。当前复用已存在CMake/C++/FFmpeg的原生CPU路线成本更低，不为备用候选安装依赖或借用Flash模型环境。以后需要Python可选组件时仍使用默认PyPI，不能改为不明二进制来源。
