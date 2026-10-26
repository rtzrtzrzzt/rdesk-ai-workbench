# 本机结构化诊断

REQ-19默认脱敏诊断、REQ-22磁盘占用说明的独立只读模块。依据 docs/requirements.md:216、240 及 technical/deployment-and-validation.md §5.5、§8.1、§10.1。它不是一致性备份、日志查看器、清理执行器或完整SBOM；不替代主要入口认证、资源授权、保留策略和用户确认。无新依赖；导入无IO。

## 主入口接线

```python
from workbench.modules.diagnostics import service

# 入口先认证当前owner，并关闭认证用Session/事务，再传其Engine。
value = service.snapshot(engine, authenticated_user_id)
archive_bytes = service.export(engine, authenticated_user_id)
```

同步接口同时接受SQLAlchemy Session或Engine。传Session时只取get_bind，不flush/commit/rollback或复用其在途事务；新开独立连接，建立REPEATABLE READ、READ ONLY事务并核实transaction_read_only=on，先按canonical user UUID与users.owner_slot=1再次校验。未知owner/非法UUID返回403；DB不可用或不能验证owner时503，**不会跳过认证返回本机诊断**。无DB的首次安装/修复入口应由另一个受控bootstrap认证边界负责，本模块不自行开放。

本模块关闭自有数据库连接后才做目录计量和ZIP。传入的Session如果尚有事务，仍由调用者负责关闭；不能因本模块另开连接就宣称调用方事务已释放。API应在线程执行这些同步函数，不在事件循环直接阻塞。模块不生成Artifact、不提供HTTP/CLI、不自动上传或落盘诊断包。

每条诊断SQL statement_timeout=1500ms、lock_timeout=250ms，缺失可选查询以savepoint回滚后标unknown/partial；不改变调用方Session设置。连接checkout和底层connect遵循宿主Engine预算，非本模块声明的全请求硬时限。统计为只读观察，不加行锁/调度锁、不改任务状态、租约或heartbeat。

返回dto.DiagnosticsSnapshot，schema_version=1：

- runtime：实际app/Python版本、平台族、架构桶和固定13个已安装distribution metadata版本。缺安装或不符合版本格式时unknown/unavailable。没有hostname、OS用户名、IP、CPU序列号、MAC、进程标识或原始uname；这不是硬件唯一指纹。
- database：实际PostgreSQL版本、alembic_version（最多16个合法revision）、当前逻辑库pg_database_size、只读事务证明。只报告读到的revision，不声称与最新migration兼容或自动迁移。
- queues：固定agent/files两队列，最新heartbeat、距采样时间秒数、30秒内heartbeat行数，以及owner jobs的固定状态桶计数。当前产品另有media Worker，但本诊断schema尚未将media纳入该固定队列投影，不能据此快照判定三类Worker全部健康。不存在heartbeat即unknown，不将历史心跳当进程探活；未来时间超前5秒则clock_skew。未知queue不导出，未知status并入other；不导出worker/run/workspace IDs。
- recent_errors：24h内**记录创建时间**满足范围的runs.error/attempts.error，分别按created_at倒序最多200条，只在SQL投影error.code前80字符。不是故障发生时间轴；长任务创建超过24h后才发生的错误可能不在本快照，DTO window_basis=record_created_at明确此覆盖。仅固定代码白名单保留，其他并为other；不读error.message/reason、stack、Run.input、消息、日志或operation arguments。run/attempt是两个来源计数，不能相加当独立故障数；达到200即标采样边界，不伪称全量精确统计。
- storage：只stat固定受管目录的表观字节与文件计数、data_dir所在卷fstatvfs实际total/free/available。不存在/不可访问目录保持null/unknown，不把未测当0。类别内表观字节不是实际唯一物理占用；稀疏文件、硬链接、压缩、目录元数据及文件活跃变化会造成差异，不能将类别和强行等同卷used。
- models：owner模型连接引用数、恰好一个应用runtime/model/deployment.json中已有的已验证资产体积与engine版本。模型路径、名称、仓库标识、端点、revision及其他任意原文均不返回；本轮只导出引用计数/既有体积/engine版本，不打开或哈希权重，不扫描配置的外部模型/PLE路径，PLE占用明确not_measured。

## 磁盘计量范围

只在配置data_dir下，通过逐级O_DIRECTORY/O_NOFOLLOW目录描述符打开并scandir(fd)。不读取Workspace.root_path，不扫描外部工作区，不跟随symlink，不读取截图、文档、日志/MD正文、浏览器profile内容或私有控制JSON。

固定类别为owner的最多256个canonical UUID工作区中artifacts、staging、uploads、memory、commands（命令日志及控制文件的表观总量），当前owner的users/{id}/memory，应用logs，runtime中顶层.log，browser/profiles，maintenance/backups与maintenance/restores。当前逻辑数据库大小独立来自PG，不递归postgres集群目录。模型权重、PLE、工具安装树、未引用的工作区目录及其他非列举目录不在此stat覆盖，因此快照不声称“整盘/整个data_dir占用已完整分类”。

全局最多20000条目录项、深度32、计量循环检查2s预算。底层一次文件系统调用可能自行阻塞；这是有界条目/检查间隔预算，不是对故障网络盘的硬实时承诺。达到预算或存在跳过项保留partial；活动运行中这些stat值是非原子观察，不暂停当前Root/children或要求维护静止。

Linux本轮实测支持描述符接口；平台缺少fd-scandir、dir_fd open、O_NOFOLLOW或fstatvfs时磁盘分类明确unavailable，仍可显示经owner验证的其他结构事实。Windows不能用存在路径字符串的递归扫描冒充等价安全实现；Windows/macOS还需实际环境验证。

## 默认导出与纯脱敏

ZIP仅有两个固定名称：manifest.json、snapshot.json；没有任意文件列表/额外附件参数。snapshot是本模块按闭合DTO生成的JSON，最大512KiB；ZIP最大1MiB。文件名固定、权限元数据0600，不读原始文件加入压缩。manifest声明脱敏版本structural_allowlist_v1、包含项、固定原因词典和排除项，automatic_upload=false。

sanitize.sanitize_code(value)只允许固定ERROR_CODES精确匹配，其他为other；sanitize_reason(value)只允许固定reason标识选择静态文本，**不是用正则替换一段敏感原文后保留其余内容**。所有reason_code来自代码词表，不把异常str/repr、provider文本、路径、endpoint/query、argv、环境、用户名、地址、token、DSN或host标识写入DTO/ZIP。未知错误保留来源与次数，失去原始诊断细节是默认隐私边界，不谎称原始日志已完整保留。

## 实际辅助证据（2026-10-18）

/tmp/workbench-diagnostics-20261018/result.json、snapshot.json、diagnostics.zip：读取当前acceptance真实数据库和本机data_dir，无INSERT/UPDATE/DELETE、无新用户/业务记录、无模型请求/服务启动停止。初次快照Linux arm64，实际schema b46d1ca93f85；约0.031s、stat211项；初次导出ZIP2661B/JSON4668B（DTO后续加window_basis后会有少量长度变化，旧测量不冒充新字节数）。SQL事件记录只有SET/SELECT/SAVEPOINT/RELEASE，结束checkout=0。非法UUID和真实不存在owner均403。

storage-point.json：自有隔离机制目录中放实际docs/README.md（7241B），stat计量精确7241B/1文件；外部docs目录symlink被跳过，状态partial；missing类别bytes=null。没有创建数据库artifact/workspace或伪造业务数据。

纯脱敏机制用明确非业务的对抗字符串验证原文不泄漏；没有往DB写测试secret、生成假日志或读取用户截图。后续主 agent 已在真实 Chrome 从产品入口下载诊断 ZIP，核对仅含 manifest.json 与 snapshot.json，未包含账号口令、数据库连接、记忆正文等本次检查的敏感内容。该产品下载与早期辅助脚本分开记录，不将有限字段核对称为任意用户正文的通用秘密检测。

## 依据

- [PostgreSQL16 SET TRANSACTION](https://www.postgresql.org/docs/16/sql-set-transaction.html)：READ ONLY、事务隔离边界。
- [PostgreSQL16 database size functions](https://www.postgresql.org/docs/16/functions-admin.html#FUNCTIONS-ADMIN-DBSIZE)：当前逻辑库体积来源，不等同全cluster/WAL。
- [Python3.12 os](https://docs.python.org/3.12/library/os.html)：目录fd、no-follow stat和fstatvfs接口。
- 当前项目identity.User、runs.WorkerHeartbeat/Job/Run/Attempt、Workspace与ModelConnection实际字段；files/storage.py、browser/service.py、maintenance/service.py、processes/service.py实际受管路径布局。本模块不修改这些来源。
