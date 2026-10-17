# 可选命令隔离适配器

范围是 `modules/sandbox` 的命令准备和能力声明，没有第二套执行器、任务账本或进程终止逻辑。没有安装任何软件、修改 userns/AppArmor/系统策略；现有持久 command runner 已在实际启动命令时调用 prepare_command，统一用于 start_command/exec_command。Windows/macOS 的 OS 沙箱未实现，不能借用 Linux 参数声称支持；原生受限执行与 OS 隔离是两种明确不同的用户选择。

## 稳定接口和宿主接线

```python
from workbench.modules.sandbox import SandboxProfile, provider_for

provider = provider_for("linux_bwrap")  # 另一个合法 ID 只有 native_scoped
capability = provider.capability()
profile = SandboxProfile(workspace_root=approved_task_directory,
                         protected_roots=(application_data_directory, model_directory))
# 必须在真正执行命令的 runner 中准备，不能把 API 的 FD 整数写入持久 plan。
with provider.prepare_command(argv, cwd, profile) as prepared:
    # 由现有受管 runner 调用 subprocess；这里不启动另一套执行器：
    # argv=prepared.argv, cwd=prepared.cwd, env=prepared.env,
    # pass_fds=prepared.pass_fds, close_fds=True (Linux), stdin=DEVNULL,
    # stdout/stderr=PIPE；维持已有 timeout/output budget/Operation/取消协议。
    pass
```

公共对象是 dataclass：

- `SandboxProfile(workspace_root:Path, protected_roots:tuple[Path,...]=(), network='none')`。只由可信宿主创建；当前只接受禁用网络策略，没有任意挂载、环境键或 bubblewrap 参数入口。宿主必须注入全部受保护数据/密钥/模型目录；空 tuple 不表示自动发现所有秘密。
- `SandboxCapability` 包含 provider/platform/architecture/status/version、reason_code/中文 reason、filesystem/network/process isolation 布尔值、environment_filtered、hard_cpu/memory/workspace_disk_limit、外部取消/对账责任、probe_performed/checked_at。status 为 ready/unsupported/unavailable；没有原始 stderr、宿主路径或秘密。`dataclasses.asdict` 后日期交由既有 jsonable_encoder 编码。
- `PreparedCommand` 包含 argv/cwd/env/pass_fds/capability/sandbox_cwd/notes；context manager 离开即关闭自身 FD，`close()` 幂等。不可把它序列化后跨进程复用，也不可关闭后执行；需在命令实际启动完成继承 FD 之后关闭父 FD。子进程失败也必须清理。
- `SandboxProvider` Protocol 提供 `capability()` 和 `prepare_command(argv,cwd,profile)`。`provider_for` 严格识别两个 ID，未知值报 `sandbox_provider_invalid`，不会选择默认后端。建议一次运行器生命周期复用 provider 实例；每个 Linux provider 实例缓存一次探测，重启运行器后重新验证当前系统。

持久 plan 仅冻结已批准的 provider ID、工作区和受保护路径等可信配置，不能冻结宿主 FD 或 capability.ready 作为未来执行保证。每次真实执行仍应捕获隔离建立失败；退出非零是失败，不能重新将同一 argv 放回宿主执行。运行时若出现 `SANDBOX_UNAVAILABLE`，用户需明确另选 native_scoped，旧批准不能自动覆盖风险变化。API/RunOptions/审批/Operation/后代归属与停止复用主模块；持久命令驱动当前仅 Linux 可用，Windows/macOS 命令仍明确 unsupported。

## native_scoped 的实际边界

只验证 argv 格式、cwd 位于明确工作区、不经过路径符号链接、工作区与受保护目录不重叠，提供最小环境。它不扫描或限制任意命令内部系统调用，不能阻止读取其他宿主文件、联网、创建进程或写其他可写路径。`network='none'` 在此不构成网络保证，capability.network_isolation 明确为 false；选择此模式即必须展示该事实。

不复制宿主 PATH、HOME、代理、DISPLAY、D-Bus/SSH socket、API key、DB/session/vault 环境。provider 在 Linux/macOS 的准备语义是固定 PATH=/usr/bin:/bin、C.UTF-8，并将 HOME/TMPDIR/TEMP/TMP 指到所选工作区；这些变量并非不存在，命令仍能无视这些约定。现有持久命令驱动没有因此获得 macOS 支持。Windows 仅保留系统目录标识 SystemRoot 并构造 System32 PATH，不继承其他环境；这段设计待真实 Windows 设备验证。由宿主负责配置可信额外工具，不能把整个应用环境恢复进去。本模块不自动读取用户 shell 配置。

## linux_bwrap 的实际边界

只在 Linux 使用已安装 `/usr/bin` 或 `/bin` 的 root 所有、无组/其他写权限、非 setuid/setgid 的 bwrap；不下载安装或提权。先读取实际版本/help 参数，再以同一固定隔离参数运行一次 3 秒内的自有 `/bin/true`，没有业务文件、网络或模型输入。命令存在不等于 ready；当前系统拒绝即 unsupported，不尝试 `--unshare-user-try`、共享网络或宿主 fallback。

文件系统是空根加固定系统只读 `/usr`、发行版实际 `/bin /sbin /lib /lib64` 布局和必要 `/etc/ld.so.cache`；不会绑定整个 `/etc`、HOME、`/run`、D-Bus、Docker socket、数据库或模型目录。系统只读目录内容仍可读，因此受保护路径与这些映射相交也拒绝。本 profile 不挂项目 venv、用户安装目录或 GPU/桌面设备，缺少相应运行库时命令可能失败；不能自动扩大挂载解决。

仅一个宿主可写根通过已打开目录 FD 绑定到 `/workspace`，cwd 映射到该目录内。系统路径只读，`/proc` 是新 PID namespace 的只读 procfs，`/dev` 是 bwrap 创建的最小设备目录，`/tmp` 为本次独立 tmpfs（64MiB）且 HOME=/tmp/home。空根最终只读。用户应按 `/workspace` 编写沙箱内路径；只将 argv[0] 的工作区绝对路径转换为 `/workspace`，其他 argv/脚本文本绝不字符串替换。host cwd 为 `/`，不能拿它表示命令在宿主根执行。

要求 user/pid/ipc/network/uts namespaces，移除所有 capabilities，禁进一步 userns，开启 new-session/die-with-parent、清空环境。默认且当前唯一网络策略为新网络 namespace，仅其自身回环；不能访问宿主回环 API。没有 seccomp syscall allowlist、cgroup CPU/RAM/进程数/工作区磁盘硬配额，64MiB 只是 `/tmp` 挂载的大小。资源耗尽/内核共享风险仍存在；有界超时和日志不等于硬资源隔离，交现有执行器处理停止和后代归属。

工作区必须当前 UID 所有且不可被组/其他写入，不能是整个 HOME，不能与受保护目录重叠。打开目录逐段使用 O_NOFOLLOW，并用 FD 挂载及 inode 比较。准备阶段扫描最多 10000 项、64 层、2 秒；拒绝符号链接、文件硬链接、特殊文件、跨文件系统/嵌套挂载及 `.env`、`.git`、`.local` 等已知敏感名字，不静默忽略它们。含这些内容的大项目需宿主先提供明确、最小、无秘密的任务目录，本模块不自行复制或删除原件。检查文件名不等于识别所有正文秘密；调用方不得提供夹带任意秘密的已授权目录。

FD 和有界扫描降低路径替换风险，但不是抵御宿主同用户恶意进程并发改文件/挂载的锁或快照。假设宿主、系统只读目录与批准后的输入可信；不承诺对恶意同 UID 宿主写入者提供边界。发布输出仍必须由现有 artifact 协议重新检查类型/路径/大小，不能直接将沙箱输出链接作为宿主文件发布。

## 本机辅助证据与未通过条件

2026-10-18（Asia/Shanghai）实际 Linux aarch64 / kernel 7.0.0-1019-nvidia；Ubuntu `bubblewrap 0.9.0-1ubuntu0.3`。本机 `bwrap --help` 确认含 FD 绑定等必需参数；上游未打补丁 0.9.0 不一定具备，不能仅按版本号放行。已安装包 changelog 显示 Ubuntu 在 0.9.0-1ubuntu0.1 回移后续版本安全功能，本模块以实际参数及真实试跑共同判断。

真实极小试跑结果：`status=unsupported`、`reason_code=namespace_denied`、probe_performed=true；中文原因“本机内核或安全策略拒绝所需的命名空间，不能使用此沙箱”。只读 sysctl 观察到 unprivileged_userns_clone=1、apparmor_restrict_unprivileged_userns=1，不能由此断言唯一故障原因；未修改它们。没有通过真实文件/网络/进程隔离执行验收，也不把生成 argv 的检查叫隔离成功。用户可继续使用明确选择的 native_scoped；需要 OS 隔离的命令应停止等待可用平台。

使用真实 `docs/technical/tools-and-recovery.md` 的临时副本，完成路径/链接/特殊文件/敏感名字/受保护交集/未知 provider/环境过滤/关闭对象复用等纯辅助检查。只读官方文档与本轮自有临时目录，无源数据库、用户目录或现有服务操作。Windows/macOS OS 隔离尚未实现；主产品已接入 provider 选择和 runner 准备，但 Spark 的 namespace_denied 仍是隔离不可用；Linux 可用机器上的实际隔离和最终产品流程仍等待真实环境验证。
