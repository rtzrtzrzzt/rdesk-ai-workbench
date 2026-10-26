# 原生源码发行包

本目录配套 `scripts/release/package.py` 和 `scripts/bootstrap.py`。它是包含已构建前端的**原生源码发行包**，不是签名的 Windows/macOS/Linux 二进制安装器。构建/stage 不修改数据库、用户数据、版本入口或现有服务。日常 start 不执行依赖升级或 npm 构建。

当前应用版本为 **1.0.0**。已准备环境的一键启动、进度显示及 `stop.sh` / `stop.ps1` 关闭行为见[一键服务说明](one-click-services.md)。当前源码包构建规则会包含这些入口；本次只同步文档与版本并提交Git，没有构建1.0.0发行包，旧包不代表当前源码。

## 包含与排除

包包含 Python 源码、迁移、pyproject/uv.lock、可重建的前端源码/package-lock、当前真实 `apps/web/dist`、必要脚本、设计/组件来源文档、项目 LICENSE/NOTICE/THIRD_PARTY_NOTICES，以及本机可读取且锁定版本匹配的 npm LICENSE/COPYING/NOTICE。前端产物是已有文件快照，不重写正在试用的 dist，也不声称证明了它与当前所有源码的构建关系。

明确排除根 models 权重、`.local/.runtime/.tools/.venv/node_modules/.git/.agents/.codex`、缓存、用户文件、凭据、运行日志和 Agent 临时记录。注意 `runtime/` 是源码说明，和 `.runtime/` 安装状态不同；`apps/server/.../modules/models` 是必要应用代码，不会因目录名 models 被误删。本机 `runtime/model/deployment.json` 含主机部署/能力证据，故不复制；将已批准基线的少量公开 revision/引擎/体积固定在本目录 external-components.json 作为 external reference，不复制或重新宣称本机已验证能力；构建不依赖读取本机运行状态，这份公开输入也随源码包保留。

`offline_complete=false`：没有随包 Python/uv、Python wheels、132734506847 字节主权重、推理环境、原生 PostgreSQL、Chrome、Office/OCR/ASR/FFmpeg。不能从“前端可离线读取”推导整套安装或模型可离线运行。平台条件依赖不等于已安装或已打包到三平台。

## 构建、校验与独立暂存

生产者需要 Python3.12、已有真实 dist、锁文件和对应本机 npm 许可材料（或发行包已携带的同版本许可材料）；构建工具只用标准库，不下载、不运行 npm、不装模型：

```sh
python3.12 scripts/release/package.py build
python3.12 scripts/release/package.py validate /path/to/release.zip --sha256 <reviewed-sha256>
python3.12 scripts/release/package.py stage /path/to/release.zip --sha256 <reviewed-sha256> --destination /path/to/NEW-version-container
```

默认产物在 `.local/releases/<release_id>/`，包含 zip、外层 `.sha256` 与 `BUILD-REPORT.json`。从已审阅报告或可信渠道取得 expected SHA256；与包一起被改掉的 checksum 不能证明发行者身份。本轮没有签名/公证/代码签名证书或自动更新服务。

ZIP 只含一个版本根目录。构建固定文件排序、权限及时间；默认 source epoch=0，ZIP 时间规范化到格式允许的1980年；可显式 `SOURCE_DATE_EPOCH`/`--source-date-epoch`。相同源码/前端/许可输入、epoch 与 Python/zlib 工具组合可重复生成相同字节；不承诺不同 zlib 实现压缩结果一样。版本 ID 同时绑定输入摘要与构建元数据。读取期间及收尾检测到源文件变动即拒绝，不把逐文件捕获当成整个开发工作区的原子快照。

`RELEASE-MANIFEST.json` 为每个 payload 文件登记真实 bytes、SHA256 与规范 mode；清单本身由外层 archive SHA256 覆盖，避免自摘要循环。记录应用/API、实际 Alembic heads、外部组件与模型引用、离线/平台边界、checkpoint/升级兼容未知状态。当前没有宣称已验证的旧版升级矩阵。

validate 核验真实 archive digest、大小/文件数、每项 hash/bytes/mode、完整成员集合和必需文件。拒绝绝对/穿越/非规范/平台保留路径、符号/硬链接等非普通 ZIP 项、加密项、重复及大小写冲突、未列入清单或超出源码 allowlist 的数据路径。单文件/压缩包上限64MiB、总展开上限256MiB、10000项。stage 使用同一份已捕获校验字节，不在校验后重开可变输入；仅创建全新 destination，逐项独占写入，不调用 `extractall`。失败留下明确 `STAGE-FAILED.json`，不执行或激活半包，也不覆盖/自动清理未知现有目录。代码包提取限制不是抵御恶意同用户进程的 OS 沙箱。

## SBOM 与许可事实

`SBOM.cdx.json` 使用 CycloneDX1.6：Python和npm名称、实际锁定版本、来源、依赖关系、平台条件、锁定完整性资料及现有许可。Python组件是 `locked_not_bundled`；没有把本机venv当全平台安装清单。Python许可表依据现有 THIRD_PARTY_NOTICES 的相同锁定版本，不推定未知许可表达。

`ai-workbench-web-static` 是实际 bundled artifact，具体JS/CSS等逐文件在 manifest。npm条目是锁定依赖集合；缺少构建图时不谎称每个条目都在 bundle。为覆盖实际可能打入前端的许可，保守携带对应已安装包的许可/NOTICE（包括可用的构建依赖材料），但不复制 node_modules 代码；非dev条目缺实际许可时拒绝打包。许可证材料在 `THIRD_PARTY_LICENSES/npm`，与锁定包版本、路径对应。外部模型引用明确 external_not_bundled。

SBOM 是追溯材料，不是漏洞扫描或无漏洞证明。当前 pyproject 的 `build-system.requires` 包含未单独版本锁定的 hatchling，manifest 如实记录：应用依赖锁定不等于整个首次构建工具链完全锁定；本包没有编造其版本。

## 首次准备和日常启动

已带 dist 时普通安装/检查不要求 Node/npm：

```sh
./start.sh --install doctor
./start.sh --prepare-database doctor
./start.sh --check
./start.sh start
```

Windows使用对应 `start.ps1` 参数。`--install` 按既有语义执行 `uv sync --locked`，默认 PyPI，不下载 Python、不改锁；保留现有默认 dev group 语义，但不代表 Chrome binary 已安装。只有 dist 缺失或显式 `--rebuild-web` 才要求 Node>=22.12/npm>=9.5 并执行默认 npm 源的 `npm ci --engine-strict` 与 build。`--check --rebuild-web` 只读检查构建前提。普通 start 不自动安装或更新；可核验并恢复已有受管私有 PG，缺 runtime/dist、数据库必要文件或未就绪的非受管连接时明确停止，不隐式初始化或接管实例。

新 `--prepare-database` 仅在已验收的 Ubuntu24.04 ARM64 路径，调用现有 helper `ensure --data-dir <Settings.data_dir绝对路径>`。不 sudo、不改系统包/全局配置、不停止其他数据库。显式配置 `WORKBENCH_DATABASE_URL` 时不切换该连接。其他平台按 [PostgreSQL 官方原生下载](https://www.postgresql.org/download/)准备独立应用库和最小权限角色，受保护连接配置/启动由主 Launcher 管理，Windows/macOS内嵌PG自动安装仍未实现/等待真实设备验收。

标准业务数据目录为Linux XDG_DATA_HOME/ai-workbench（默认`~/.local/share/ai-workbench`）、Windows LOCALAPPDATA/AIWorkbench、macOS Library/Application Support/AIWorkbench；尊重 WORKBENCH_DATA_DIR。原生 PG 新模式同根 `postgres/` 保存 cluster/凭据/settings，`components/postgres` 保存私有工具，`database.json` 0600 保存连接与严格派生的 managed_postgres 描述。用户数据不放进版本ZIP或新版本源码目录。首次空闲端口选择会先持久化，不是端口租约；启动时如果已被其他进程占用仍拒绝，不重新猜一个端口或接管它。

`configure_data_directory(data_dir,port=None,read_only=False)` 是维护与launcher的稳定helper接口；只显式 `--data-dir` 开启新布局。read_only不会mkdir/写；已有settings复用端口、显式不同端口拒绝、已有cluster缺settings拒绝、路径symlink/未知归属/非私有权限拒绝。默认不传参数仍是原legacy `.local/postgres` / 55432 / development JSON，绝不自动迁移或接管旧开发集群。标准数据根的私有 PG55433、真实 SQL 与原生应用启动已在 Spark 验证；隔离 stop/resume 和 `start.sh` 恢复四组件的辅助检查亦已通过。Windows/macOS完整安装仍无真实设备证据。已有数据库也可通过[本机 TTY 配置向导](database-setup.md)接入，BYO 连接不自动获得受管集群生命周期或维护权限。

## 升级、安全更新与失败恢复

1. 将新包 validate/stage 到独立新版本目录；保持旧实例和用户数据不变，审阅 manifest、许可、未知兼容性与临时空间。
2. 用产品已实现的维护功能制作**实际完整备份**，确认时间、schema/checkpoint与引用；等待无运行中外部effect并取得统一静止边界。仅有旧backup文件名不算完成这一步。
3. 由主launcher/操作人停服并核实本实例进程身份，指向同一标准数据根。准备新版本独立venv；显式按该版本既有 `workbench migrate` 做受控迁移。stage工具不会自行完成此步。
4. 数据备份/迁移/兼容检查完成后，才由用户明确批准切换正式入口。当前工具不生成或修改 active-version 指针，不能把本地stage当成已发布。
5. 用真实数据执行health与产品关键流程。health失败保持诊断/停写，不自动切旧代码去运行已经迁移的DB。需要恢复时用匹配备份恢复到**新空DB/新数据目录的隔离实例**，由用户评估新增数据和现实副作用；不覆盖当前DB、不做危险in-place downgrade。

安全更新先根据SBOM和官方组件公告识别受影响的实际组件，按明确版本/默认PyPI/npm或官方原生源更新受影响锁/许可材料，再真实重建和针对性验收，发布新目录包。模型/引擎/浏览器/原生工具的安全更新独立管理，不因日常打开Launcher浮动升级；本源码包不声称已扫描全部CVE或具有自动签名更新系统。

## 历史发行机制验证与未覆盖

2026-10-18发行机制核对曾采用当时源码与已重建 dist 的 build → validate → 独立 stage → 解包后 bootstrap --help；这一流程未安装或启动副本、创建账号或迁移数据库。该历史证据不能作为1.0.0包已构建或验证的证明。另行构建新包时，具体归档路径、版本和校验结果以该包的 `BUILD-REPORT.json`、`RELEASE-MANIFEST.json` 及暂存回执为准，不沿用旧包摘要。源码包包含 sandbox 运行说明，继续排除主机专属模型配置与私有验证资料。

原生应用的实际浏览器使用与备份隔离恢复已有独立产品证据，但不等于已从最终源码包完成升级切换。Windows/macOS 安装、签名、权限、完整启动，正式证书信任与另一台 LAN 设备访问，以及真实跨版本升级/回退仍等待对应条件。当前没有完整签名二进制或整包离线安装交付。
