# 本地一致备份与隔离恢复

此模块使用现有 SQLAlchemy/psycopg/pwdlib、stdlib 和本地官方 PostgreSQL16 工具；备份/恢复服务不更改源数据库或角色权限，不启动模型/Worker。HTTP、全局静止 gate、停止与维护界面已由主入口接入，真实隔离恢复范围见末节；BYO 数据库不因此自动获得私有集群管理员能力。

**当前schema040仍保留的限制：**`_facts`按`information_schema.columns`检查敏感列名，既有净化allowlist尚未包含`media_connections.encrypted_api_key`与`media_tasks.encrypted_credential`。因此只要数据库存在当前媒体表，本模块就会返回`backup_secret_schema_unsupported`，即使这些列全为空；不会跳过检查生成完整产品备份。下面2026-10-17–18的一致备份与隔离恢复是当时schema的历史证据，不能代表1.0.0全部扩展/媒体/协作数据已通过此产品迁移流程。后续阶段使用的完整PG运维归档与本模块净化备份属于不同流程；当前限制见[实现状态](../../../../../../docs/implementation-status.md)。

## 服务与DTO

同步调用，HTTP应放入工作线程，Session不可与其他线程并发使用：

- `create_backup(db,user_id,command:BackupCreate,boundary:QuiescenceBoundary,*,abort_check)->BackupView`
- `list_backups(db,user_id)->list[BackupView]`
- `inspect_backup(db,user_id,backup_id)->BackupManifest`
- `restore_backup(db,user_id,command:RestoreCreate,*,abort_check)->RestoreView`

BackupCreate：request_id(UUID)、label(最多120字符)、timeout_seconds(60–86400，默认3600)。RestoreCreate：request_id、backup_id、restored_owner_password(SecretStr，12–1024，不strip)、timeout_seconds。口令只写新恢复库原唯一owner，不保存至manifest、响应或日志。所有操作要求当前owner身份，源库Session不提交、不自动flush。数据库DSN不出现在API DTO中，仅恢复返回connection_file本机路径。

备份ID取request_id；完成请求相同参数重取同结果，异参数409。不完整/失败/中止请求保留manifest，不复用旧边界直接重跑，须明确新request_id。恢复ID也取request_id；完成请求重读同隔离实例（核对同backup及实际新口令验证器），拒绝覆写已有目录。

## 必须是真实静止边界

QuiescenceBoundary内部字段：boundary_id、acquired_at、source_database、data_dir、backend_pid、SQLAlchemy connection、gate_check无参回调。不能从HTTP JSON创建这个对象。assert_held先执行回调，再查询同connection的pg_backend_pid/current_database/pg_locks，要求对应backend PID在对应库持有key82139705的ExclusiveLock。

主入口的API写入与agent/file/media三类Worker持同key shared gate；备份通过try exclusive取得边界，busy时失败，不排队堵住停止。create_backup验证对象绑定的源库/数据目录，且每个MiB、每个文件、PG子进程100ms轮询时重复assert_held+abort_check。abort_check返回truthy或抛异常会停止；统一gate marker也可立即使边界失效。连接及gate生命周期由调用方拥有，维护服务不释放、提交或关闭它。

此外拒绝运行/created Attempt、活跃Job租约、运行中Operation、已dispatch无回执交换、未确认closed的MCP retained transport、未收敛MD journal、非available artifact与publishing上传。原生automation session必须在closed状态且持真实owned_resources_closed（browser）或owned_inputs_released且无pending_native_call（desktop）回执；creating/active/stopping/outcome_unknown或仅到期而无关闭证据均拒绝，active action与未释放writer lease也拒绝。已静止且传输真实关闭的历史unknown/needs_reconciliation可以进入备份，source ids/status列入manifest.reconciliation_required，绝不改成成功。queued任务可以被静止，恢复后held。此判断不替代gate：只有所有应用写入入口已参加同一gate、外部effect已安全停点时才可开始。gate不冻结外部编辑器或提供OS快照；观察到复制期间/完成前文件改变会失败，但不承诺控制不参与gate的所有外部写入。

## 默认秘密范围与PG权限

本适配支持现有项目私有 PostgreSQL16：开发 legacy 布局使用127.0.0.1:55432；标准应用数据根通过 `database.json` 的 managed_postgres 描述核对对应集群、工具路径和持久端口，不固定为55432。应用角色ai非superuser且无CREATEDB，私有集群管理员workbench_admin承担隔离库维护。只从匹配的原生helper读取受保护pgpass/环境，不变更源角色/源库。外部/BYO PG没有这份受管证明时明确unsupported，不能由连接成功推导备份权限。

PG链路：源库由应用角色执行官方pg_dump custom stdout，管道接本轮随机`workbench_backup_<uuid>`的pg_restore stdin；不把原始含凭据dump落盘。只在scratch清理auth_sessions、users.password_hash、model_connections与model_revisions.encrypted_api_key及模型配置里的显式api_key字段，禁用remote/search并使能力验证过期，清除旧会话化请求响应、保留最小去重记录。新增MCP密文字段被明确纳入schema审阅与清空校验，MCPConnection默认disabled、execution_authorized=false、encrypted_secrets为空；stdio启动argv及env/bearer引用清除（任意argv可能内嵌凭据，不能靠flag名称猜测），command/cwd/已验证格式的endpoint等非秘密结构仍保留。MCPSnapshot失效且服务器/协商元信息排除。ToolExchange保留source ids、实际content/structured_content/raw业务结果与unknown事实，requestState/continuation/inputResponses、InputRequired回调请求、SDK session/transport/callback与_meta材料排除，保留不可恢复的来源轮次摘要。automation_policy的browser/desktop capture/control全部disabled、remote_model_revisions为空、精确window id/PID与grant失效；闭合native历史结果保留真实文字/几何/受管artifact引用，移除native_id/runtime_nonce、session/lease/ref及旧host路径/secret。writer lease解除session绑定、revoked=true、epoch递增并过期，不把历史lease带到新host。原始Question/表单答案作为业务历史保留；恢复时MCP pending Question失效，原awaiting_input交换/operation标记为需要核对的unknown，原状态保存在reconciliation.backup，不续发。最终pg_dump来自净化后的可见行，不靠删除vault.key或保留密文来声称排除凭据。

只有本轮CREATE成功、随机前缀正确、database COMMENT ownership marker与owner仍匹配的库才允许DROP。不会按前缀批量删除，不杀源库连接。pg_dump/pg_restore参数为列表，密码不出现在命令行；诊断进入未命名受保护临时fd，不回显原始SQL/业务行。取消时只终止本轮子进程并清理其scratch。系统崩溃可能留下有ownership marker的随机scratch，不能用“看到前缀就删除”处理；普通finally清理不承诺SSD/WAL安全擦除。

声明范围是“系统管理凭据与会话排除；用户正文保留，可能含用户主动粘贴的秘密”。它不是任意内容自动脱敏器。最终备份未加密，以本机目录0700/文件0600保护，不将口令与备份同存伪装成加密备份。

## 文件范围、MD与完成标记

只按事实引用复制受管artifact、上传块、用户AGENTS、用户/工作区MD主题/索引/候选及MD journal历史文件。拒绝路径越界、符号链接、硬链接、不完整原件；artifact必须与不可变长度/hash匹配。运行staging、整个vault目录（含加密master envelope与临时capability）、OS Keyring、旧vault.key、session-signing.key、runtime、.env、日志、重建缓存、数据库物理data目录、外部workspace原文件、权重和PLE不复制。

MD为主数据：复制后用现有memory.files.decode验证document_id/schema/scope/source_refs，世代不能超前；只对active且无待核对/冲突/遗忘屏障的文档，在scratch重建title/source_refs/content_hash与scope.index_hash。源MD、源DB缓存、forgotten状态、tombstone和scope generation都不改。manifest分别记录recorded_version与actual_version；头部身份改变、非法元信息或复制过程中变化仍拒绝完整。MD历史可能包含已忘记旧正文，但旧候选不能绕过generation/tombstone恢复成active。

最终dump在上述scratch重建后产生。manifest记录schema/checkpoint包和迁移版本、graph版本、PG/client版本、扩展/表、workspace映射、MD与遗忘屏障、实际文件长度/hash、模型连接/修订与既有权重revision/校验证据引用。132734506847字节模型不复制、不重新全量哈希；PLE/编译缓存未纳入，换机需按原revision在受支持后端另行准备，不能承诺离线立即加载。

写入`maintenance/backups/.incomplete-UUID`，全部引用校验与scratch清理完成后才写manifest/COMPLETE摘要并rename到UUID目录。失败/停止清除本轮复制内容并留下failed/aborted manifest，不替换先前完整目录。复制逐块检查取消，没有整目录shutil.copytree。只对本轮失败暂存做rmtree清理。

## 恢复安全边界

只接受当前服务器私有目录内该owner的完整UUID备份，不支持把任意上传SQL档直接交给pg_restore。COMPLETE和文件hash用于本地完整性，不是外部发布者数字签名。restore拒绝应用/schema/checkpoint包或迁移版本/PG主版本不兼容，不自动迁移或加载旧checkpoint。

恢复写入新`workbench_restore_<uuid>`和全新`maintenance/restores/<request_id>/data`，不存在覆盖当前实例的开关。逐文件/归档校验后pg_restore，再次撤销session、旧凭据、remote/search、Worker heartbeat。所有非terminal Run/对应Job变held、jobs.completion_status清空、lease失效、待执行批准重置pending；外部workspace根改成新目录下不存在的映射路径，历史/记忆可读但不能沿旧授权直接写项目。只给原唯一owner设置本次新口令，重新登录产生新vault/session。

恢复后MCP必须重新填写启动参数/必要凭据、发现目录与授权，历史操作/未知结果不能作为自动重放来源。恢复后验证artifact/active MD引用闭合、scope generation/tombstone一致、安全停点与禁用状态；新连接文件0600。返回`status=isolated,user_interface_verified=false`，从不自动切换配置、启动Worker或续发外部动作。这是新库/新目录逻辑隔离，仍使用原应用角色，不声称OS/角色安全沙箱。真实UI登录、历史/产物/记忆和授权安全任务验收后，用户才可选择切换；本服务永远不把辅助检查当真实恢复验收。

## 实测辅助依据（2026-10-17）

官方PG16.15真实工具链，使用现有acceptance数据：source会话1、可登录hash1、两表API密文实际0；scratch四类净化计数全0，source计数前后不变。copy含真实2个succeeded Run，未制造账号/会话/付费模型数据；非空API密文清除SQL已实现，但不编造该源原本含非空key的证据。

统一mutations.quiescence实际持锁后，完整PG+FS备份`b8fc1f89-d9e6-439d-b63b-06eeedb43e1f`成功（13文件、128067字节，实际值以manifest为准）；真实外部编辑MD的旧DB索引与实际新版本分别记录，scratch重建而源cache保留。另一次复制阶段通过真实abort marker中止，保留先前COMPLETE不变，新请求aborted。边界实际持锁验证及释放后拒绝已通过。

上述完整备份又通过实际restore_backup恢复到本次独立新库/目录：恢复2个artifact、1个MD文档、7个checkpoint；原唯一owner的新口令验证器通过，MD新cache与实际文件一致，source旧cache保持不变；sessions与enabled search均0。同一request_id重试返回同一隔离实例。恢复源中的2个Run均已succeeded，因此held_runs实际为0，未人为造非terminal数据声称验证held分支。本次辅助恢复库/目录已按本轮ownership marker清理，完整备份保留。静态ruff通过；最终真实UI登录、历史与产物恢复验收仍由主agent负责。


## CLI 进程记录与复用

`workbench stop`始终交给strict `native_process.terminate_owned`核对，不能把
`process_matches=false`当作已经退出。confirmed（含严格记录已不存在）才逐项删除并
原子发布state；legacy、身份不可读/变化、退出未确认与未知组件保留记录并非零退出，
`--force`也不绕过身份检查。只停止精确记录进程，不搜索同名进程、不杀进程组或独立
模型/数据库。旧记录需要人工核对，不根据裸PID自动补造严格身份。

processes.json使用同目录600临时文件、fsync、replace与POSIX目录fsync；runtime目录
必须当前账户独占。可读取旧的当前用户只读共享644记录以完成迁移到私有原子写，
拒绝其他账户可写、符号/硬链接、不完整/超大记录。Windows依赖原生owner-only ACL
验证而非声称chmod等价，尚无真实Windows/macOS设备验收。

`start`先检查现有严格process identity、API进程实际loopback listener、真实health、
agent/file/media三类Worker真实heartbeat与对应vault broker；五个应用组件全部健康就复用，不先ensure DB/migrate或
争夺任务quiescence。存在无法完整确认的记录时保留并报告，不覆盖/自动重启现有任务。
空state遇未核实归属的健康服务同样拒绝启动。新实例迁移先commit结束gate证明SELECT
事务，再建checkpoint并发索引；session advisory gate仍保持，避免自身旧snapshot阻塞
CREATE INDEX CONCURRENTLY。browser-companion/desktop-companion尚未分派的CLI命令
明确失败，未假装启动或实现这两类组件。


2026-10-17当时新增schema相容范围为146468c9210f（MCP/结构化Question）与8b71c5dc2b01
（automation政策/会话/动作/writer lease及jobs.completion_status）。
automation_actions.session_id与desktop_writer_leases.session_id明确审核为FK历史source
id，而非bearer/session凭据；只加入这两项allowlist，其余新增secret类字段仍拒绝。
AutomationAction结果unknown保持unknown并列入reconciliation_required；未核实关闭的
AutomationSession不会为备份被改成closed。2026-10-17这次辅助归档没有实际automation行，
因此没有伪造原生closed会话/观察结果来宣称该分支通过；后来接通runtime不改变这份历史证据的覆盖。

## 本轮 CLI/MCP/Vault/schema 辅助证据（2026-10-17）

`/tmp/cli-maintenance-20261017-2/result.json`内CLI cases已通过：真实自有strict进程
停止/已退出记录删除；真实legacy PID在普通与force停止后均未被误停且记录保留；
state600、无残余临时文件；未接线companion命令明确失败。真正启动自有API、两Worker
与vault broker到临时55253端口/独立数据库，持真实shared gate再次start仍复用同四个
PID且state字节不变，owner数始终0。自有组件与临时库均已清理。脚本后续Artifact
fixture误写name/media_type字段而失败；已按真实display_name/mime_type修正，并仅继续
备份分支，未把该脚本整体伪报通过。

首次`/tmp/cli-maintenance-20261017/result.json`之前的实际新库setup暴露：quiescence
证明SELECT留下idle transaction，checkpoint CREATE INDEX CONCURRENTLY等待其旧
virtualxid。已最小修复为_migrate前commit证明事务（session advisory gate保留），
第二轮真实新库迁移与启动已通过。首轮只中断本轮自有检查进程并清理其标记库，未停
主实例/模型/搜索。`-3`保留新增automation session_id schema防线拒绝的中间证据；
核实为FK并加入明确策略后继续，不把未知新列一律放行。

`/tmp/cli-maintenance-20261017-4/result.json`整体通过：真实SDK读取项目Markdown生成
一份受管artifact；真实InputRequired进程关闭后保留unknown；实际PG trigger拒绝
另一真实只读调用的回执提交，验证dispatch+SQL NULL/JSON null回执均阻断备份。
解除本轮trigger后显式持久化实际收到的unknown回执，未重发调用，才获准备份。

完整备份`dd287dbb-2527-4e40-9e9d-d0e81d7320a4`与实际隔离restore成功：1个真实
Markdown artifact+database.dump；2个非终态Run held、jobs.completion_status清空；
manifest含4条待核对source引用。MCP disabled/secret密文为空、真实requestState与
continuation/lease材料移除，实际业务正文与unknown/source ids保留；vault envelope
及session-signing.key未复制；源库原1份MCP密文/2项unknown计数不变。旧auth会话和
正式账号未被操作；这里的独立机制owner仅位于本轮临时数据库，并非正式实例初始化。
所有本轮native/stdio/broker与带ownership marker的临时库已回收，完整备份留在该
私有验证目录。

`/tmp/cli-maintenance-20261017-4/post-sanitize-verification.json`仅将上述已有真实
归档恢复到一个自有临时库，核实新增capability净化后置SQL通过且原2项unknown不变，
没有重复CLI/SDK或模型调用。该归档automation行数为0，因此未声称真实closed native
history/截图的产品恢复已验收。代码lint/compile辅助检查通过；这些历史机制记录与下面
后续真实产品证据分别保留。

## 后续真实产品证据（2026-10-18）

主 agent 从标准数据根的私有 PG55433 制作实际备份，恢复到新数据库/新目录，并在独立原生实例的真实 Chrome 登录后核对记忆正文、两版历史、两条 held 任务以及完整36205B命令日志下载，字节与源实例一致；证据为本机 `.local/installation-acceptance/restore-read-evidence.json`。未结束任务没有自动续发。这证明该次隔离恢复的实际数据范围，不证明任意版本升级/回退、BYO PG 或全部 browser/desktop 历史恢复。

清理入口复用受控候选与静止边界，只处理符合条件的已登记上传临时内容及已完成文件任务 staging，不清原件、正式产物或记忆。真实空 staging 删除可以返回0文件/0字节，不能为获得正数而制造样本。后续 `pending-and-preservation-ui-evidence.json` 已在真实 UI 核对清理后 ASR107B产物和原记忆正文仍保留；对话 Markdown/JSON 与结构化诊断 ZIP 下载也已有独立真实 UI 结果。
