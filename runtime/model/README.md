# 当前 Flash-Next 的 S0 部署

只使用本机既有 NVIDIA 权重，固定修订见 `deployment.json`。本页针对当前 Spark 项目部署；通用源码发行包刻意不携带这份主机专属配置和验证证据，使用模型辅助脚本前须另行准备匹配的本机配置/环境，不能直接把其他机器的 verified 标记当成自己的验收结果。独立虚拟环境位于 `.runtime/model-env`，权重保持原目录；PLE backing、编译缓存、日志与真实结果存放在 `.local/model`。不自动下载模型、切换模型、连接外部 API 或启用 MTP。

官方 SGLang `v0.5.21` 的确切提交为 `e00930c5489053f26d86b179cee0d087f846acbb`。其 `qwen4_exp.py` 从 NVIDIA MIXED_PRECISION 配置识别 FP8 PLE；`qwen4_exp_ple_table.py` 提供 `file` backend，明确面向 GB10 UMA。主机 CUDA 13.0.3；约束将 NVCC、NVRTC、CUDA 绑定固定在 13.0，其他 NVIDIA 组件按实际依赖锁记录，不把所有组件版本统称为 13.0。安装完成后生成本环境的 `requirements.lock`，实际已安装包证据见 `.local/model/evidence/environment-packages.json`，pip安装来源报告见 `installed-packages.json`。文件存在和安装成功均不代表模型真实验收通过。

```sh
bash scripts/model/install.sh
python3 scripts/model/modelctl.py probe
python3 scripts/model/modelctl.py precompile
python3 scripts/model/modelctl.py plan
python3 scripts/model/modelctl.py serve --fresh-ple
```

`serve` 前台运行；从另一终端查看 `python3 scripts/model/modelctl.py status`。它先执行小型 CUDA 环境检查，监听 loopback 30000，当前限制 32768 上下文和并发 1。官方非生成入口 `/ready` 成功才记 `ready`（此版本 `/health` 默认会生成一个 token）；每 2 秒采样系统内存。MemAvailable 低于 8 GiB 即停止本次监管的进程树，不自动重载；`stop` 只向本 runner 记录的、启动标识一致的进程请求停止。这套 CUDA 模型监管依赖 Linux subreaper 与 pidfd，不声明可直接运行在 macOS 或 Windows。

### 管理命令的短锁与启动意图（2026-10-18）

`serve`/`precompile`、`stop`、状态核对及PLE清理使用同一个 `.local/model/control/management.lock`。目录0700、锁文件0600且由当前OS账户所有，逐级拒绝符号链接；锁文件保留稳定inode，不靠删除锁或PID文件解锁。锁只覆盖管理决策与状态提交：先在短锁内核对engine和runner的实际PID/start tick，并原子fsync提交含runner身份与本次内核boot_id的starting/reserved意图，再释放锁做环境检查。真正Popen与PID/start回执仍在同一短锁内；guard采样、ready查询及原生退出等待均在锁外，只有状态落盘时短暂取锁。

专用guard在启动任何检查或引擎子进程之前启用Linux subreaper，并拒绝接管已有子进程。停止时只向内核确认属于本guard的直接或收养子进程发送pidfd信号，持续收养、发现和回收后代；不以进程名、一次/proc快照或历史进程组号作为终止授权。即使根进程先退出、后代setsid或收到SIGTERM后再fork，监管仍持续到`waitpid(-1, __WALL | WNOHANG)`返回ECHILD。只有这个内核结果才允许写`resources_closed=true`和`closure_method=linux_subreaper_echild`。正常退出、启动前检查失败及预编译退出码0都遵循同一边界。机制依据见[Linux subreaper手册](https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html)与[wait手册](https://man7.org/linux/man-pages/man2/waitpid.2.html)。

`stop`在preflight期间也会先持久写入stop_requested，再通过pidfd向严格匹配的自有runner请求停止。后续fresh-PLE及Popen均重读该意图；未dispatch时记录not_started，但仍须关闭检查阶段产生的后代，不能因为没有engine PID就把正在启动误认为空。停止请求返回pending，最终状态由guard实际回收后提交。runner已消失时，仅可向身份仍匹配的引擎根PID请求SIGTERM；新controller无法继承旧guard的子进程所有权，因此不会据此宣称整树已关闭。

guard崩溃、回收超时、无法读取/匹配身份、损坏/旧不完整记录、dispatch中断或Popen已返回但PID回执写失败，均保留意图并阻止新启动和PLE清理，不能自动覆盖或重试。只有明确尚未调用Popen、或Popen报告exec/setup失败且未返回child时才记录not_started；其他无回执dispatch保持outcome_unknown。fresh generation与cleanup共用锁；启动意图尚无engine身份时，cleanup预览和apply都暂时拒绝。正常运行仍只保护实际活跃PLE目录，允许检查/清理其余符合原规则的generation。

此前短锁验证在隔离临时目录让两个真实自有Python进程竞争实际flock：第二进程在第一进程释放临界区后、第一进程仍存活时即取得锁；0700/0600与稳定inode核对通过，非私有目录、symlink祖先及symlink锁被拒绝。该次证据为`/tmp/modelctl-lock-20261018/result.json`，对应监管器替换前的代码。新监管器的真实自有子进程辅助验证见`/tmp/model-supervision-20261018-a2dh86ts/result.json`；它不加载权重，也不触发推理。上述实现审查阶段当时没有操作实际模型或重启主机；这些`/tmp`记录现已随后续主机重启丢失，不能当作当前仍存在的文件。用户后来授权的实际启停结果见下一节。

### 2026-10-18 后续实际启动

旧 guard 已在此前用户要求关闭服务时正常停止。本次主机已重启，两个旧记录没有`boot_id`：主 agent 依据本机同`machine_id`的systemd journal、实时时钟/单调时钟及保存的引擎启动tick唯一匹配旧boot，时间对应误差分别为0.006040/0.005336秒；保留原始记录及日志证据后补齐真实boot归属，再调用现有`reconcile --apply`归档。此为人工核查的数据恢复，不是新增自动推断接口，也没有重启机器或删除阻塞记录。证据在`.local/model/control/legacy-boot-evidence/`及`.local/startup-measurements/20261018T042203Z/`。

新版guard的首次实际加载复用了旧PLE backing，写入采样约15MiB/s，10/11分片时仍在慢速读写；638.296秒处主动停止。guard真实返回`linux_subreaper_echild`和`resources_closed=true`后，才通过既有清理工具移除最旧的未使用generation，再执行`serve --fresh-ple`。第二次加载从guard启动到`/ready`首次成功为575.016秒，随后真实文本请求在0.601秒内完成；模型保持ready。新文件写入阶段的一次采样约572MiB/s，不是整体推理或磁盘吞吐基准。

默认`serve`仍会复用配置中的PLE目录，本轮没有将缓存轮换改成自动策略。第二次尝试改变了backing路径，而且前一次已读过部分文件，不能当成严格冷/热启动对照，也不能承诺不重启就会快很多。完整计时、各次尝试和当前服务状态以[统一状态清单](../../docs/implementation-status.md)及上述私有`startup.json`为准；新guard完整预编译、ready后的停止/再次启动和压力验证仍未据此完成。

### 旧状态与guard丢失后的显式过渡

旧版本的退出码、PID消失、not_started或单独的resources_closed布尔值，均不能证明所有后代已退出。缺少新关闭凭证的记录会阻止新启动与PLE清理。旧记录没有boot_id时，新`stop`也拒绝发信号，因为PID/start tick不能排除跨重启碰撞；仍在运行的旧guard须从原启动终端或已有明确所有权的服务管理器停止，再进行下述流程。不要删除状态文件解除阻塞，也不要把旧记录手改成stopped。核对流程只针对原主机的本地运行记录；不能用其他主机或容器复制来的boot_id作为关闭证明。

```sh
# 只预览state.json和precompile-state.json的剩余条件：
python3 scripts/model/modelctl.py reconcile
# 旧记录缺少boot_id时，在已匹配的runner/engine退出后记录本次内核启动标识：
python3 scripts/model/modelctl.py reconcile --prepare
# 另行安排整机重启；此工具不停止服务、不重启机器。
# 重启后先预览，确认已观察到不同boot_id，再显式归档与核对：
python3 scripts/model/modelctl.py reconcile
python3 scripts/model/modelctl.py reconcile --apply
```

`--prepare`遇到身份仍匹配的runner或engine会拒绝；它只补观察标记，不宣布资源关闭。同一次内核启动中，`--apply`始终拒绝未闭合记录。只有记录的launch boot_id或预先持久化的观察标记与当前boot_id确实不同，才先把原记录归档至私有`control/reconciled/`目录，再发布`closure_method=host_reboot`的关闭凭证；原任务是否成功仍以归档为准。新guard记录已有launch boot_id，整机重启后可直接核对；无boot_id的旧记录即使此前已重启过，也须先prepare再安排一次重启，不能追认没有证据的历史。重启应用、退出shell或更换PID都不满足这个条件。

`precompile` 必须在模型不驻留时运行。它通过相同 guard 启动官方 `flashinfer.jit.fused_moe.gen_cutlass_fused_moe_sm120_module().build(verbose=True, need_lock=True)`，固定 `MAX_JOBS=2`，使用完全相同的 CUDA/FlashInfer 版本、flags、`FLASHINFER_WORKSPACE_BASE` 和 SM121a 缓存目录，不修改上游源码或裁剪目标。之后调用官方 `TritonGDNKernel.packed_decode/extend`，维度取自本模型配置，预热单请求 decode 和 1024-token chunked prefill；这是数值内核预热，不加载模型权重。预编译状态在 `.local/model/precompile-state.json`，结果在 `.local/model/evidence/precompiled-kernels.json`。

第一次实际启动已证明 11 分片和 mixed NVFP4/file-PLE 加载成功，但将大规模首次 MoE JIT 留到模型驻留后会叠加编译内存：2026-10-17 首次启动在最低 MemAvailable 7.96 GiB 时被 guard 正确停止，尚未 ready。对应证据目录 `20261017T110929Z`，68 个已完成对象被保留。官方 Ninja/生成源码采用内容不变不改 mtime 的写法，`MAX_JOBS` 只影响 `ninja -j`，因此同版本、同 flags、同目录会复用有效 `.o` 和 `.ninja_log`，中断的未完成目标会重做。不要并行启动另一份编译。预编译成功、无自有模型/编译进程残留、可用内存不少于 105 GiB、磁盘满足 80 GiB 预算后，才能显式执行一次 `serve`；安全阈值不变，不做失败后自动重试。

```sh
python3 scripts/model/verify_endpoint.py
python3 scripts/model/verify_endpoint.py --stream
python3 scripts/model/verify_capabilities.py tools
python3 scripts/model/verify_capabilities.py image --image .local/model/evidence/image-fixture-connections.png
python3 scripts/model/modelctl.py stop
```

`--fresh-ple` 是显式的启动选择：在同一 revision 私有目录下建立带所有权标记的新 generation，并将实际路径写回 `deployment.json`，保留原 backing 和编译缓存。此前第一次写入新稀疏 PLE 分片约 53 秒；第二次覆盖已有 backing 超过 7 分钟仍未完成，实测 NVMe 读约 33.2 MiB/s、写约 13.1 MiB/s、busy 89.2%。官方实现对同尺寸既有文件直接 mmap，再完整覆盖，且设置 MADV_RANDOM；旧页 fault-in 随机读是差异原因的推断，不是已隔离证明。第二次加载按明确指示停止，所有既有证据和文件保留。本轮额外 47.7 GiB 已检查空间，不修改官方加载器或权重。

为限制增长，最多保留 2 个由本工具创建的 generation；到达上限时先明确清理旧版本再创建新版本。默认仅预览具体候选路径，`--apply` 才执行清理；始终保护当前活跃引擎引用的目录、有未知内容的目录以及无本工具所有权标记的目录。原始 direct-root backing 仅在显式指定 `--include-legacy` 时才进入候选，本轮先保留至新服务实际验证成功后再决定。清理只涉及可重建 PLE 缓存，模型权重和内核 `.o`/`.so` 不在范围内。

```sh
python3 scripts/model/modelctl.py cleanup-ple --keep 1
python3 scripts/model/modelctl.py cleanup-ple --keep 1 --apply
# 单独审视原始 backing（默认仍只预览）：
python3 scripts/model/modelctl.py cleanup-ple --keep 1 --include-legacy
```

端点验证会触发真实推理，保存请求、原始响应、实际 usage、结束原因和耗时。工具验证实际读取本部署配置，并通过白名单工具将真实修订追加到本次证据目录内的验收记录；模型无法指定宿主路径。图片验证使用已人工查看并保留的真实工作台连接页截图，检查四个可见标签，不生成测试图片。端点检查之外，工作台现已有真实补充/停止、子任务和缓存看板流程，见[统一状态清单](../../docs/implementation-status.md)；视频和完整压力基准仍待验证，未验能力保持 unknown，不能从文本成功推导。端点工具协议通过也不能代替工作台 ToolExecutor 与真实 UI 流程验收。

历史8K配置下，2026-10-17 的 fresh-PLE 加载已真实成功：11 分片权重加载 529.51 秒，复用预编译的 MoE `.so`，服务于 12:18:14 UTC 进入 ready。至当时四项端点验证结束的快照，系统最低 MemAvailable 为 22,319,894,528 字节（20.79 GiB），系统总内存减 MemAvailable 的峰值为 108,341,014,528 字节（100.90 GiB）；这是整机 UMA 压力，包含 OS 和应用，不能作为模型独占分配量，也不能代替当前32K配置的测量。最新运行记录见 `state.json` 和 `resources.jsonl`。

上述历史8K配置的真实文本、SSE、工具读写闭环、截图阅读均已通过，证据索引为 `.local/model/evidence/s0-summary.json`，`deployment.json` 将支持状态和证据状态分别记录，并保留检查时间、修订和原始结果路径。SSE 首内容 0.131 秒，完整收到 usage 和 `[DONE]`；工具实际读取部署后写入固定修订；截图正确识别四个可见标签，响应报告 1395 image_tokens。这些是当时的小样本协议验证，不是通用吞吐基准。TileLang 在 QSA 首次编译时报告静态 `Data race` warning，完整保留在成功启动的 `server.log`，未被禁用或掩盖；它未阻止 ready 和上述真实检查，不能仅据此宣称所有上下文下数值正确。

当前 safetensors 头部统计：tensor 132,639,846,394 字节，PLE 51,200,246,018 字节（约 47.68 GiB），MTP 2,698,026,496 字节。文件 PLE 仍会使用页缓存；CPU pinned offload 不会增加统一内存。部署暂定 static fraction 0.78、PLE RSS 4 GiB，预留 OS/应用约 10 GiB；加载峰值必须以资源日志实测为准。额外磁盘预算约 83 GiB，其中 PLE 约 47.7 GiB、环境/编译缓存暂留 35 GiB。

上游依据：[发布源码](https://github.com/sgl-project/sglang/tree/v0.5.21)、[file-backed PLE PR](https://github.com/sgl-project/sglang/pull/37068)、[NVIDIA 混合精度支持 PR](https://github.com/sgl-project/sglang/pull/38121)。本地 NVIDIA README 只声明 vLLM/B200/B300；SGLang/GB10 是否可用以此次真实环境证据为准。
