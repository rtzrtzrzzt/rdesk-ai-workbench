# 原生源码准备与启动入口

这些文件提供应用 0.1.0 的源码 bootstrap 和可复用 native helper。Launcher 已接入标准数据目录、受管数据库、迁移/就绪、API、两类 Worker 和凭据服务；原生源码发行工具见[发行说明](../distribution/README.md)。当前不是签名二进制安装器或完整离线安装包，Windows/macOS 成套安装仍未取得真实设备证据。

## 日常入口与显式安装

POSIX入口已可执行；脚本定位自身目录，不要求用户先cd或激活venv：

```sh
./start.sh --check
./start.sh --install doctor
./start.sh --prepare-database doctor
./start.sh
./start.sh status
./start.sh doctor
./start.sh stop
```

Windows对应已审阅的 `start.ps1`：

```powershell
.\start.ps1 --check
.\start.ps1 --install doctor
.\start.ps1
.\start.ps1 status
```

日常 start/status/doctor 使用版本目录的 `.venv`，不调用 uv sync/npm，不要求 Node 常驻，不重装模型。显式 `--install` 按锁文件准备 Python 环境；已有 dist 时复用，缺失时才构建前端。`--rebuild-web` 显式重建前端。准备后继续运行指定 CLI 命令；默认 start 会启动工作台，只准备并诊断应使用 `--install doctor`。需要 Python3.12；bootstrap 宿主可用已有 Python 启动后检测目标3.12，不自动下载解释器。显式 `--python <已安装3.12路径>` 不会被悄悄替换。

Python 安装及需要构建前端时的等价命令（uv/python/npm 从实际 PATH 或项目私有工具环境查找，代码没有旧项目路径）：

```sh
uv sync --locked --python /path/to/python3.12 --no-python-downloads --default-index https://pypi.org/simple
npm ci --registry https://registry.npmjs.org --engine-strict --no-audit --no-fund
npm run build
```

npm 两条仅在前端缺失或显式重建时于 apps/web 执行；已有 dist 的安装和普通检查不要求 Node/npm。uv 目标固定当前版本目录 `.venv`，保留现有默认依赖组，不触碰独立模型环境；锁文件不匹配时失败而非更新。npm ci 不修改 package/lock，不能并发其他前端依赖/构建任务。bootstrap 构建检查要求 Node>=22.12.0、npm>=9.5，适用平台的其他锁定 engine 条件由 engine-strict 校验。Windows 显式用 node 运行随附 npm-cli.js，避免把参数交给 cmd 批处理解释。

安装器临时移除应用数据库/密钥环境及额外PyPI/npm索引覆盖，明确使用默认PyPI/npm源；不自动运行sudo、修改全局PATH、安装全局包、停其他数据库或拉Docker。第三方安装器原始stdout/stderr只进入本轮未命名临时文件，不回显或保存到诊断包；失败给出工具阶段/退出码，需在本机按上面的精确命令看工具自身诊断。现有CLI输出经过DSN/凭据相关行过滤，不打印环境快照。过滤器是有限诊断处理，不声称任意用户内容的通用脱敏器。

## 缺失工具与数据库

缺uv时给出项目私有安装命令，而非暗中全局pip：

```sh
python3.12 -m venv .tools/bootstrap
.tools/bootstrap/bin/python -m pip install --index-url https://pypi.org/simple uv==0.9.25
```

Windows使用 `py -3.12 -m venv .tools\bootstrap` 与 `.tools\bootstrap\Scripts\python.exe -m pip ...`。Ubuntu如缺venv组件，由用户/管理员明确安装发行源的python3.12-venv；脚本不提权。Python与Node缺失时链接官方本机安装路径。当前已有工具链可复用，因此没有新增下载或把旧Node目录写进源码。

`--check` 只读检查锁文件、所需工具、项目 runtime/静态 bundle 及 SELECT1 数据库连接；不安装、建表、迁移、启动服务或下载权重。缺依赖/未就绪返回非零；`--check --rebuild-web` 只检查构建前提。普通 start 将数据库启动交给 Launcher：可以核验并恢复已有受管私有集群，缺失文件、归属不明或非受管连接不会触发隐式初始化，也不会改换连接。

首次在 Ubuntu24.04 ARM64 准备标准数据根内的私有 PG，显式运行：

```sh
./start.sh --prepare-database doctor
```

已有原生数据库使用 `./start.sh configure-database`，在本机 TTY 中输入连接信息和不回显口令，真实只读验证后保存新配置；详见[数据库配置说明](../distribution/database-setup.md)。其他平台按[PostgreSQL 官方 Windows](https://www.postgresql.org/download/windows/)与[macOS](https://www.postgresql.org/download/macosx/)路径准备应用专用库和最小权限角色。首次数据库配置目前是终端流程，不是无数据库 Web 安装向导。项目 PG 助手没有 Windows/macOS 自动安装实现；对应终端、ACL、完整安装与卸载仍等待真实设备验证。不要把 DSN/口令放进命令行。

离线所有者恢复使用 `./start.sh recover-account`，须先停服并核对所有执行资源；见[账号恢复说明](../distribution/account-recovery.md)。这些交互入口保留调用方 TTY，不捕获密码提示，不以固定启动超时中断交互。

## 进程身份helper

`workbench.infrastructure.native_process`公共接口：

- `capture(pid,nonce,*,nonce_flag='--instance-id')->dict`
- `matches(record)->bool`
- `terminate_owned(record,*,timeout=15,force=False,kill_timeout=5)->TerminationResult`

capture只接受本调用方刚启动的同用户直接子进程，记录`version=1/platform/pid/create_time/executable/nonce/nonce_flag`，可JSON序列化；可额外保留Launcher的instance_id/log字段。nonce必须16..256 ASCII字符且是唯一明确参数值，支持`--instance-id value`或`--instance-id=value`，不接受子串/其他参数/重复flag碰巧含nonce。由Launcher每次用UUID/强随机生成，而非固定名称。后续matches不要求原父进程仍存活，支持Launcher退出后读取私有状态核对。

核对实际原生executable、create_time、同用户、精确nonce；AccessDenied/读取缺失/exec改变/旧record/外平台一律failclosed。旧nonce-only记录不会被自动升级为可信身份，主Launcher必须保留未确认状态且不能据其杀进程。不要将matches=false简单显示成已停止；它也可能是归属不明。

终止只针对这个record本体，不枚举全系统、不杀进程组/children、不按PID或名字单独杀。psutil的terminate/kill再次执行PID重用检查；强杀前重新核完整记录。结果`status=confirmed|unknown/reason/signalled/forced`；默认不force，deadline后unknown；original已退出返回confirmed而不发信号。退出期间exe/cmdline先消失的短窗口仅继续有界轮询，不因此额外发信号或误报已停止。

Windows terminate是原生TerminateProcess而非可捕获SIGTERM，forced仅表示显式第二阶段kill，不能解释成Windows第一阶段一定优雅退出。主Launcher应先完成应用级停止/任务副作用核对。helper确认的是进程已退出，不证明业务事务已提交或外部效果撤销，也不是恶意同用户程序隔离机制。

依赖psutil7.2.2，BSD-3-Clause。官方默认PyPI提供Python3.12适用abi3轮子，LinuxARM64约157KB、macARM64约130KB、WindowsARM64约135KB；无新重型运行时。与自己解析Windows/mac私有结构相比，成熟库承担原生ABI维护。本模块只读取指定自有PID，不读environ、不持久化完整cmdline、不联网。针对性检查核对了安装版本terminate/_raise_if_pid_reused源码，使用新Process而非process_iter缓存。

## 纯平台目录helper

`application_paths(*,system,home,environ=None,cwd=None)->PlatformPaths`返回PurePosixPath/PureWindowsPath的data_dir/config_dir/cache_dir/state_dir/log_dir。调用方注入sys.platform、用户home、环境和cwd；模块不读取全局环境，不调用任何文件I/O、不创建目录、不移动旧数据或改配置。

| 平台 | 数据 | 配置 | 缓存/状态/日志 |
| --- | --- | --- | --- |
| Linux | XDG_DATA_HOME/ai-workbench，默认~/.local/share | XDG_CONFIG_HOME/ai-workbench，默认~/.config | XDG_CACHE_HOME，XDG_STATE_HOME；日志在state/logs |
| macOS | ~/Library/Application Support/AIWorkbench | 同应用根/config | ~/Library/Caches/AIWorkbench、应用根/state、~/Library/Logs/AIWorkbench |
| Windows | LOCALAPPDATA/AIWorkbench | APPDATA/AIWorkbench | 本地应用根/Cache、State、Logs |

WORKBENCH_DATA_DIR仅覆盖data目录，其他类别继续平台语义；相对覆盖必须有绝对cwd，Windows不同盘符相对路径拒绝，XDG相对base按规范忽略。只是词法解析，不对符号链接/权限/实际盘符作安全背书；后续Launcher必须处理现有`.local/data`的明确选择/迁移与原生权限，不能悄悄把原数据当作不存在。

## 真实证据与边界

2026-10-17 初版辅助检查使用 Linux/Python3.12.3、uv0.9.25、psutil7.2.2，临时 PATH 中 Node22.14.0/npm10.9.2；实际 SELECT1、`--help`、POSIX sh 语法、无 Node 下 doctor 委派通过。初版 check 曾要求 Node，此后已改为按是否需要构建判断；旧记录不表示随包已有 dist 时仍需安装 Node。源码未硬编码该本机工具位置。

只创建本轮短子进程核对身份：真实record匹配；篡改nonce/create_time/executable均unknown且无信号、子进程继续存活；nonce出现在无关参数不接受。精确TERM退出confirmed，重复停止不再发信号；忽略TERM的自有子进程deadline unknown、无隐式kill，明确force后同身份kill confirmed。全部辅助子进程已清理，不触碰现有服务。

2026-10-18 主 agent 已在新标准数据根的私有 PG55433 启动 API、两类 Worker 和 vault broker，并在真实 Chrome 完成账号、ASR 及维护流程。原生 `start.sh configure-database` 的真实 PTY 证明口令不回显、只读连接和0600新配置保存。另一次隔离 native PG stop/resume 与 `start.sh` 检查已验证已有受管集群恢复后四个应用组件可启动；缺文件或非受管配置拒绝且不初始化。后者是原生机制辅助证据，不代替浏览器产品验收。

Linux 路径/XDG 与指定自有进程检查已有证据；Windows/macOS 仅有路径词法和实现代码，process helper、PowerShell入口、原生权限/ACL、完整启动均等待真实设备验证。上述结果也不构成签名发行、全离线安装或跨版本升级/回退验收。
