# Native detached command runner

REQ07 的持久日志执行机制；`start_command` 与 `exec_command` 已统一使用此持久命令驱动，后者由 `service.execute_bounded` 等待同一受管记录和回执，不再走另一套临时执行器。Linux/Spark 原生辅助验证通过，产品授权、数据库生命周期和真实 UI 验收由宿主负责。无新增依赖，使用现有 psutil 7.2.2 与 Python 3.12 标准库。导入不读写文件、不启动进程。

## 宿主接口

`native.capability()` 返回 `status: available|unsupported`、`supported: bool`、`reason`、`platform`、`driver`、`os_sandbox: false`。调用时只读查询本机 `PR_GET_CHILD_SUBREAPER` 与自身 pidfd；不改变宿主 subreaper 状态。真正 `PR_SET_CHILD_SUBREAPER` 仅在新 supervisor 内执行且先于 command dispatch。缺少任一内核能力则拒绝 dispatch。

`native.launch(absolute_plan_path, acknowledgement_timeout=10)` 返回不可变 `LaunchResult(status, supervisor_identity, state, reason)`。status 为 `acknowledged|outcome_unknown`。必须先提交私有 launch intent，再 spawn supervisor；完整 native state 及 child 身份（或实际终态）落盘后才 ack。重复 launch 对同一 intent 只读取，绝不重发原 command；不同 plan 拒绝。缺回执/启动中宿主崩溃留下 intent 时保持 unknown，不自动清除 intent 或重试。

`native.read_state(output_dir)` 验证私有 state，另附只读的 `supervisor_observation: match|gone|unknown` 及 `effective_status`。若 supervisor 不再可证明存活且没有 closed 回执，effective_status 为 outcome_unknown。不得仅凭 native_process.matches=false 宣称资源关闭。

`native.stop(output_dir, expected_identity=None, timeout=15)` 返回 `StopResult(status: confirmed|unknown, owned_resources_closed: bool, state, reason)`。先写 nonce 绑定的 stop marker，再通过现有 strict native_process.terminate_owned 向相同 supervisor 请求 SIGTERM；不 force kill supervisor。只有私有终态资源闭合回执且 supervisor 自身已退出，才 confirmed。宿主应传 admission 时保存的完整 expected_identity，unknown 时保留 DB 记录并交 janitor 对账。missing/malformed state 不重发 command，也不推测后代已关闭。

`native.read_log(output_dir, stream='stdout', offset=0, limit=65536)` 只接受固定 stdout/stderr 流，不接受文件路径；limit 1..65536 是原始字节预算。返回 text、requested_offset、start_offset、next_offset、total_bytes、eof、utf8_boundary_adjusted、boundary_pending、decode_replacements。offset/next_offset 均为原始字节偏移。offset 落在 UTF-8 continuation 内时跳过这些字节并显式报告 start_offset；分页末尾不完整码点保留到下一页。limit 过小可能 boundary_pending 且 next_offset 不前进，调用者增至至少 4 字节即可；二进制/无效 UTF-8 可返回替换字符并标识。正常完整码点 U+FFFD 不被误报为解码错误。

这些为同步、可阻塞接口；API/Worker 应放在线程/专用任务中调用，并在 Run 终止前先 request stop，再以真实 closed 确认释放租约。模块不连 DB、不提供工具权限、不初始化账号。

## Trusted plan

宿主先创建 0700 私有目录 `.../commands/{canonical command UUID}`，再以0600私有原子文件写 plan.json，传绝对路径。所有路径的 ancestor 不得是 symlink；不创建/修改无关父目录权限。plan 仅接受：

```json
{
  "schema": 1,
  "command_id": "宿主生成的canonical UUID",
  "nonce": "每次意图生成的canonical UUID",
  "argv": ["经过宿主授权的实际可执行文件", "参数"],
  "cwd": "存在的绝对目录",
  "sandbox_profile": "native_scoped",
  "protected_roots": ["宿主提供的受保护绝对目录"],
  "timeout_seconds": 180,
  "max_output_bytes": 33554432,
  "output_dir": "末级basename等于command_id的私有绝对目录"
}
```

示例 UUID 为接口占位说明，不能直接作为业务记录；实际辅助验证均用当次随机 command UUID。timeout 1..3600；max_output_bytes 默认32MiB，stdout+stderr总上限64MiB（可配置更小）。argv 1..256 个非 NUL 字符串，每项最多32768字符；plan 最多256KiB。cwd 只是执行位置，并非 OS 文件访问隔离。没有自动 shell；确需 shell 必须由 host 显式授权 argv 中的 shell。supervisor 自身环境仅含固定 PATH、LANG、LC_ALL；实际命令由 SandboxProvider 准备环境。native_scoped 将 HOME/TMPDIR/TEMP/TMP 映射到所选工作区，不继承宿主 HOME、Python path、DB/key/token/proxy 环境；这些变量并非不存在，也不构成 OS 隔离。linux_bwrap 仅在能力可用时采用其独立 `/tmp`/HOME，不能静默回退。plan 只保存 profile 和受控路径，FD 由真正 command runner 内 prepare_command 后直接继承和关闭。

## Native receipt 与状态

state.json 私有原子替换+fsync，首次状态在 child 启动前落盘；stdout.log/stderr.log O_EXCL0600，并在终态回执前 fsync。

- status：starting、running、stopping、completed、failed、cancelled、timed_out、output_limit、outcome_unknown。
- command_id、nonce、schema=1；started_at / finished_at 为 UTC ISO8601，运行时 finished_at=null。
- supervisor_identity：现有 strict native_process 格式 version/platform/pid/create_time/executable/nonce/nonce_flag。
- child_identity：实际 lineage 身份或 null；kind=linux-owned-lineage、pid/create_time/executable/uid、parent_pid/parent_create_time、owner_nonce、captured_at。快速退出 zombie 的 executable 可为 null；内核 pidfd/birth/归属仍真实，不能编造 executable。
- exit_code：实际 waitpid 退出码，signal 为负；尚未知为 null。成功须实际 root exit=0 且资源闭合。
- stdout_bytes、stderr_bytes：实际成功写入日志字节，终态与实际文件大小一致；运行中快照最多约250ms滞后。*_observed_bytes 是管道真正读到的字节，不声称知道尚未读取/写出的字节。
- truncated：达到总 byte budget 后开始停止并丢弃超额管道内容；即使恰好写满预算也立即停止，保守标识 true。
- owned_resources_closed：只能在本轮内核 waitpid 无剩余子进程、所有持有的 pidfd 已结束且无身份检查不确定后置 true。它证明本轮原生资源回收，不撤销命令已经产生的外部效果。
- reason：可读真实原因；error 为 null 或 {code: COMMAND_FAILED|COMMAND_TIMED_OUT|COMMAND_OUTPUT_LIMIT|COMMAND_OUTCOME_UNKNOWN, message}。
- owned_identities、reaped：有界 native 观察附录，不是可执行恢复材料；最新最多512项及 identity_receipts_omitted。未抓到身份但由内核 waitpid 回收的快速 orphan 只保留真实退出 receipt，不伪造身份/信号。

Supervisor 先成为 Linux child subreaper，再创建唯一 command tree。只从自身/已证明 parent birth 的直接子关系发现后代，取得 pidfd 前后核对 birth 与 parent；执行中合法 exec 不改变 pidfd 所指生命周期。所有信号仅用 pidfd_send_signal；不按进程名字、进程组或单独 PID 杀进程。shell 退出留下的后代会被本轮 subreaper 接管；root 正常退出后仍回收其后台后代。停止先 SIGTERM，1s 后对仍被本轮 pidfd 指向的进程 SIGKILL；最长约10s后仍不能闭合则 outcome_unknown。

私有目录/0600对同一 OS 账户提供进程间协作边界，不声称防御同账户主动篡改或恶意 native command；本模块不是 OS sandbox。宿主仍必须做权限/审批、cwd/command policy、并发限制和输出脱敏。

## 崩溃与平台边界

Supervisor SIGKILL/断电、manifest不全、身份变化、回执写失败都不能推断外部命令没发生。新 host 可重读原生回执，但不能在 supervisor 消失后重新创造已丢失的 pidfd/后代归属，更不能从头重发。无真实 closed 回执时持续 unknown；即便运维另行清理资源，也不能把缺失的 runner 事实自动改为成功。

当前持久命令驱动仅 Linux 开放；Windows/macOS 的 `start_command` 和统一后的 `exec_command` 均明确 unsupported，不再保留旧 bounded exec_command 绕过路径。NativeDriver 是窄替换边界；Windows 需可验证独占 Job Object 及可靠后代退出收据，macOS 需可靠 fork/daemon 后代归属与清理证明后才能开放，不能用进程名/killpg 或“只杀根进程”替代。

## 实际辅助证据（2026-10-18，未替代 UI 验收）

`/tmp/workbench-command-20261018/results.json` 保存真实 plans、native identities、状态、分页和回执；`final-points.json` 保存最后受影响点复核。所有命令读实际项目 docs/README.md，未构造业务任务/日志，无模型生成、无 DB 记录、未停止既有服务。

- 文档 stdout 7242B / stderr247B，completed exit0/closed；UTF-8中间偏移分页、新 Python host 重读、同 plan 重复launch不重发。
- 1s timeout：timed_out/exit-15/closed；shell+Python descendant显式停止：cancelled/closed，两份native身份。
- shell即时退出留下后台Python：根exit0，至少两份实际waitpid receipt，completed/closed。
- 总预算10000B：实际日志恰好10000B，output_limit/truncated/closed。
- 早期独立runner辅助记录中，child env keys 仅 LANG/LC_ALL/PATH；没有继承DB/key/proxy/宿主HOME。当前实际命令已改经 SandboxProvider，native_scoped 会额外将 HOME/TMPDIR/TEMP/TMP 指到工作区；该早期环境记录不表示这些变量现在仍不存在。
- 实际 SIGKILL 自有 supervisor 后 effective_status=outcome_unknown、closed=false，stop仍unknown；harness另以事先获取且birth核对的pidfd清理自有child，未伪造runner关闭回执。
- 长命令实际自行wait的120个cat docs子进程，共868920B真实日志，supervisor观察最多10个FD，closed=true（descendant-handles.json）；已结束pidfd及时释放，避免长任务累积句柄。
- `/bin/true` 快速退出亦 completed/exit0/closed；最后docs日志7242B计数/分页复核通过。supervisor显式恢复SIGCHLD默认处理，实测宿主继承SIG_IGN时仍取得真实exit0/closed（sigchld-point.json）。
- 首次测试误用不存在的根README，真实 FileNotFoundError stderr537B、failed exit1/closed，证据保留；不是转写成成功。
- 首次私有写采用现有 helper 的短暂hardlink时 nlink=2。模块只对此确切私有过渡有界重试100ms，其余权限/链接/大小异常仍拒绝；未修改公共helper。

2026-10-18 主 agent 另在真实 Chrome/本地模型流程完成持久命令产品验收：一次真实 README 读取命令自然结束，724100B日志分页、刷新与下载字节一致；另一项经精确批准启动后，由 UI 独立停止，状态 cancelled/closed，36205B日志下载一致。证据为本机 `.local/installation-acceptance/command-ui-evidence.json` 与 `command-stop-ui-evidence.json`。后者还在独立恢复实例通过完整日志读回，未自动重放命令。这些字节数是当次真实输入的记录，不随当前 README 修改而重算，也不代表所有命令或平台均已验收。Windows/macOS 持久命令驱动仍明确 unsupported。

## 官方依据

- [Linux PR_SET_CHILD_SUBREAPER](https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html)：orphan adoption及wait语义。
- [Linux pidfd_open](https://man7.org/linux/man-pages/man2/pidfd_open.2.html) 与 [pidfd_send_signal](https://man7.org/linux/man-pages/man2/pidfd_send_signal.2.html)：生命周期句柄信号避免PID reuse。
- [Python3.12 os/waitpid/pidfd_open](https://docs.python.org/3.12/library/os.html)、[signal.pidfd_send_signal](https://docs.python.org/3.12/library/signal.html#signal.pidfd_send_signal)、[subprocess](https://docs.python.org/3.12/library/subprocess.html)。
- 现有项目 `infrastructure/native_process.py` strict capture/terminate_owned 与 `infrastructure/vault/private.py` 私有原子文件，复用而未修改。
