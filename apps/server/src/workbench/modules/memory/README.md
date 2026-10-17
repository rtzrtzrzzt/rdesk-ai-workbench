# Markdown 记忆与 AGENTS 规则

本模块只提供 ORM、Pydantic DTO、文件与业务服务。HTTP、表注册、迁移、工具权限和 PromptCompiler 由上层接入。没有新增依赖，没有后台模型调用，也不以文档或静态检查表示真实 UI 已验收。

## 接口

所有服务以 `(db: Session, user_id: str, workspace_id: str, ...)` 开头，重新确认当前用户、工作区归属。`scope` 为 `user | workspace`；用户记忆的响应 `workspace_id=null`，但读取它时仍必须处于该用户可访问的真实工作区。

- `list_documents(..., scope) -> dto.DocumentList`
- `read_document(..., document_id) -> dto.Document`
- `create_document(..., command: dto.MemoryCreate) -> dto.Document`
- `update_document(..., document_id, command: dto.MemoryUpdate) -> dto.Document`
- `forget_document(..., document_id, command: dto.MemoryForget) -> dto.ForgetResult`
- `search(..., query, scope) -> dto.SearchResult`
- `get_global_rules(...) -> dto.GlobalRules`
- `update_global_rules(..., command: dto.GlobalRulesUpdate) -> dto.GlobalRules`
- `load_rules(..., relative_paths: list[str]) -> dto.RulesSnapshot`
- `list_proposals(..., scope) -> list[dto.Proposal]`
- `create_proposal(..., command: dto.ProposalCreate, source_actor='user') -> dto.Proposal`
- `decide_proposal(..., proposal_id, command: dto.ProposalDecision) -> dto.Proposal`
- `recover_writes(..., scope) -> list[dto.WriteNotice]`：维护入口，只核对真实文件，不自动补发文件写入。
- `list_revisions(..., document_id) -> dto.MemoryRevisionList`
- `read_revision(..., document_id, version) -> dto.MemoryRevisionView`
- `compare_revision(..., document_id, version) -> dto.MemoryRevisionComparison`
- `restore_document(..., document_id, command: dto.MemoryRestore) -> dto.Document`：仅用户明确恢复，不作为 Agent 自动写入能力。

读服务使用调用方 Session，不自行提交。写服务只从调用方 Session 获取 bind，在专用 connection/Session 中提交自己的短事务；不会提交调用方的事务。调用方应先保存已鉴权的 user_id 并结束认证读事务，再调用写服务。若其他实体尚未提交，新连接不保证能看到它们，因此不得将记忆写入与另一个未提交业务写混在一起。

所有写命令有 UUID `request_id`；journal 对 `(user_id, request_id)` 唯一，保存请求摘要。同 ID 不同命令/内容返回 409；已确认同命令返回已登记资源的当前安全视图，不再次写文件。后续遗忘后重放旧创建/更新，不返回旧正文，不恢复文档。冲突、未应用、待核对的旧请求不会被悄悄重新发布。

DTO 见 `dto.py`。文档公开 `version` 是真实 UTF-8 文件的 SHA256；规则不存在时为 `missing`，与 ORM 内部整数 revision 不同。create 要求最新 `expected_scope_generation`；update/forget/global rules 要求 `expected_version`。提案审批的 `expected_version` 是提案的整数 revision，另按其原始文件 hash 和基础文档 hash 核验内容。

## 存储与检索

数据只在 `get_settings().data_dir` 的 `users/<id>/` 或 `workspaces/<id>/` 内：

```text
users/<user_id>/AGENTS.md
users/<user_id>/memory/MEMORY.md
users/<user_id>/memory/topics/<document_id>.md
workspaces/<workspace_id>/memory/MEMORY.md
workspaces/<workspace_id>/memory/topics/<document_id>.md
<同一 memory 根>/proposals/<proposal_id>.md
<同一 memory 根>/.writes/<journal_id>/{基础、候选、检测到的外部版本}.md
<同一 memory 根>/.revisions/<document_id>/<scope_generation>/<SHA256>.md
```

新写文件权限为 0600。拒绝绝对私有存储键、路径穿越与私有存储中的符号链接。项目文件一律只读，并复用既有 `tools.filesystem.authorized_path/read_text` 的工作区与受保护目录规则。

主题正文和提案正文仅在 MD 文件保存。DB 保存归属、storage path、title/来源等索引元数据、文件 hash、版本、journal 与遗忘屏障，不保存可作为唯一原件的正文。MD 首行是固定 schema 1 JSON 元信息注释，随后空行和原样 Markdown 正文；不解析可执行 YAML 标签，也不新增 YAML 库。文档 ID 是当前粒度的稳定 entry ID；本版按整份文档编辑/遗忘，不声称具有段落级遗忘。

`MEMORY.md` 是可重建的链接索引，包含活动文档 ID 与相对主题链接。全局 AGENTS 不会混入其中。用户直接编辑主题正文后，读接口返回文件当前真实 hash；旧预期版本不会覆盖它。若元信息被破坏，返回冲突和可读原文，检索不会使用它。若 `MEMORY.md` 被外部修改，后续受管写入停下，保留现有索引，不能以“索引可重建”为由覆盖用户文本。

检索按已授权 scope 与活动元数据筛选，先读取本范围 MEMORY 索引，再按标题和正文中文子串/casefold 字面匹配读取文件；不使用英文分词器或向量库，不扩展为跨范围遍历。结果包含文档 ID、文件路径、当前 hash、来源引用与 MD 行范围，最多 50 个命中、每文档 5 个片段；扫描受 16 MiB/5 秒预算约束，截断显式返回。标题命中定位到元信息第 1 行；正文从第 3 行开始。journal、提案和遗忘文件不作为搜索事实源。

## 发布、冲突与恢复

受管写入按稳定顺序取得 request 和 scope 的 PostgreSQL session advisory lock；scope 锁同时保护主题文件和其索引。锁跨 journal 提交、文件发布、DB 收尾持有，finally 显式释放；清理失败时使池连接失效，不把悬挂锁归还连接池。锁等待上限 5 秒。没有将跨提交即释放的 xact lock 当成该锁。

发布顺序：在私有目录用独占创建保存基础和候选快照 → 提交 prepared journal 与必要的元数据意图 → 发布前重读真实 hash → 目标同目录临时文件 flush/fsync → `os.replace` / 删除 → 尽可能同步目录 → DB 标记 published 并收尾 committed。多文件（主题+索引）并不构成一个原子事务，数据库与文件系统也没有共同提交。

外部编辑器不参加锁。提交前检测到修改时停止覆盖，保留基础、候选及能读取的外部版本；检查到替换之间仍有竞争窗口，不能保证捕获所有外部同时写入。需要严格保护原路径时，应只提交提案并由用户安排写回；本模块不能宣称 journal 捕获过每次外部修改。

发布中断、DB 提交未确认或混合文件状态返回明确 `outcome_unknown`；列表 `pending_writes` 提供 journal ID 与阶段。维护对账只做三类判断：文件均为已登记新 hash 时补齐元数据；均为旧 hash 时标记未应用（遗忘屏障例外，仍待清理）；混合/外部内容保持未知或冲突。不凭 mtime 猜测，不盲目重新执行替换。外部改过的索引需要人工核对；特别是遗忘已经落下屏障但索引清理被阻止时，保留未知清理状态，不宣称物理删除已完成。

## 候选与遗忘

agent/subagent 只应通过 `create_proposal` 提交候选，runtime/tool 接入必须仅向它们暴露读/搜/提案能力，不暴露用户写入或审批入口。`source_actor` 是受信调用方提供的来源标记，不是提案可自行提升权限的字段。提案创建不会写入永久主题；仅用户确认的 decision 才进入统一发布流程。候选文件变动、基础文件变动、版本变化或 scope generation 改变都拒绝旧审批。拒绝候选不创建记忆。

遗忘先在 durable intent 事务中提升 scope generation、写永久 tombstone、将文档标记 forgotten 并使本范围旧候选失效，再进行文件与索引清理。即使清理失败，后续读取/搜索及旧请求重放也不提供正文；恢复不得越过 tombstone。用户重新明确创建新的记忆需使用新 generation 和新请求，不能通过旧候选或旧备份静默复活。

保留的 journal/base/candidate 是故障核对材料，不是后续检索来源；遗忘不声称撤回既往外发内容、清除历史聊天或销毁所有备份。上层 PromptCompiler 必须在下一次模型请求前重新按 scope/generation 获取有效记忆，不能持续发送运行状态里缓存的已遗忘片段。

## 版本比较与明确恢复（REQ12）

`MemoryRevision` / `memory_revisions` 仅保存已确认快照的归属、世代、摘要、路径、来源和关联 journal，不保存正文。快照沿用受限 UTF-8 MD、独占创建、0600 权限与 fsync；同一路径存在时只核验摘要，绝不覆盖。未索引的暂存或孤立文件不是历史目录来源。

本功能从启用后的真实观察和确认发布开始保留历史。**所有目录响应 `history_complete=false`**；`history_gap_reason` 明确说明未导入旧 journal 候选，不能保证记录外部编辑器的每次修改。`history_started_at` 是该文档最早实际确认快照的时间，不是其创建时间，也不是全安装历史的开始时间。只读目录/读取/比较不会创建快照或回填旧 journal。

确认点有两类：

- `origin=observed_current`：新写入暂存时，基础文件是当次实际读取且摘要匹配的真实 MD；检测到冲突或进行恢复核对时，也只能保存实际当前文件。journal 最终失败或未应用不否认这个已观察正文，但其未发布候选不能进入历史。
- `origin=managed_publish`：仅带本功能 `revision_tracking.version=1` 的 journal，且文件发布已返回并保存 `publish_confirmed` 回执，或恢复时所有目标文件确实匹配已登记新 hash，才能登记 after。已确认候选损坏时只允许用独立匹配摘要的真实目标文件核对；两者都无法核对则保留待核对结果。候选文件单独存在、prepared 状态或 mtime 都不是发布证明。

`MemoryRevisionSummary` 必填字段为 `document_id, version, scope_generation, title, origin, captured_at, journal_id, journal_status, status, restorable, unavailable_reason`。`version` 是完整真实 MD 的 SHA256。`status=unavailable` 表示快照缺失、改变或身份无效，不返回其正文。`unavailable_reason` 可为 `snapshot_unavailable / scope_generation_changed / current_document_not_active / null`。目录按实际确认时间倒序，最多 100 个不同摘要，截断用 `truncated=true`；同摘要多代保留时展示最近代的确认项。`journal_status` 是其关联写入的当前状态，不应据此把 before 正文当成该写入成功结果。

读取旧版本返回真实 `body/source_refs`。比较方向是目标旧版本到当前版本，返回 `target_version/current_version`、标题与来源引用差异及 unified `diff`；两边各最多 200000 字符/4000 行，diff 最多 200000 字符，限额触发时 `truncated=true`。快照无效或当前文档不是活动状态时不伪造比较结果。历史不参加记忆检索、自动规则加载或 PromptCompiler；界面可明确展示给用户，不应自动外发给模型。

`MemoryRestore` 严格字段为 `target_version, expected_current_version, expected_scope_generation, request_id`，两版本均为 SHA256、世代为正整数、request_id 为 UUID。恢复在既有 request/scope session advisory lock 内重读真实当前 hash、确认 active 且无 tombstone、核对当前世代与快照确认世代，再用旧正文/标题/来源生成当前世代的新 MD。它通过原 journal/CAS 发布协议执行，不重命名旧快照或伪用旧 hash。相同请求重放只返回当前安全视图；新请求携带旧 CAS 会冲突。

范围内的遗忘会提升 scope generation。因此其他仍活动文档的较早世代快照可供用户明确读取/比较，但 `restorable=false`、原因 `scope_generation_changed`；不得跨屏障恢复。被遗忘文档的所有世代历史都立即禁止读取/比较/恢复，用户需要重新明确创建新文档，不能隐式取消遗忘。

遗忘在同一个 durable intent 事务中写 tombstone 并把该文档全部 `MemoryRevision.status` 改为 forgotten，随后通过既有文件发布计划删除摘要匹配的快照。清理文件若被外部修改，不覆盖它：屏障仍有效，清理返回 `memory_history_cleanup_conflict / outcome_unknown`，不能声称物理删除成功。旧 `.writes` 故障材料沿用既有保留语义，不是历史读取入口；本功能不声称安全擦除磁盘或撤回既往备份、聊天、已发送内容。

全局 AGENTS 历史本轮未扩展；其缺失与读取/写入契约保持既有行为。

## AGENTS 作用域

全局 AGENTS 不存在时返回 `body='' / version='missing' / exists=false`，不创建文件或虚构默认规则。`RulesSnapshot.global_rules` 始终提供这一真实编辑状态；`rules` 仅包含实际存在的来源。

加载顺序是用户全局 → 项目根 → 仅相关相对路径的父目录规则。每项返回 source_path、文件 hash、scope、applies_to、正文和 precedence；子目录规则只适用于其目录子树，多个相关路径不等于将所有子目录规则升级为全项目规则。最多 32 条相关路径、32 层目录、128 个来源、512 KiB 正文。只提供 AGENTS 来源，不从网页/记忆/工具日志的同名文字推导规则。现有明确用户指令和独立 Policy/OS 权限仍由上层控制；文件里的规则不能授予权限。

## 依据与验证状态

项目依据：`docs/technical/models-and-context.md` 第 7 节、`docs/technical/runtime-and-data.md` 第 7 节、既有 `modules/tools/filesystem.py`。

官方机制依据：[PostgreSQL 16 advisory locks](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS) 说明 session 锁需要显式释放或会话结束；[Python 3.12 os.replace](https://docs.python.org/3.12/library/os.html#os.replace) 说明单次替换的语义，不能据此声称跨文件/DB 原子性。

既有记忆创建、编辑与中文检索由主 agent 完成真实 UI 验证。本次版本扩展完成模块 Ruff/格式检查与 10 项隔离 PostgreSQL + 临时 MD 辅助检查：创建/更新的真实快照和比较、恢复新 hash/请求幂等/旧 CAS 拒绝、实际外部编辑基础版本、发布前中断候选排除、真实发布后中断核对、已发布候选损坏时只接受匹配目标、外部冲突排除候选、世代屏障、遗忘清理/旧请求不复活、快照被改时清理未知但历史访问屏蔽。

上述辅助检查正文来自实际 `docs/technical/models-and-context.md`；只读复制验收库现有 user/workspace 的公开归属作为隔离结构夹具，口令哈希置为不可认证标记，没有复制凭据/会话或运行隔离 API。随机 `workbench_backup_*` 数据库按私有 helper ownership marker 清理，临时 MD 目录也已清理；该机制检查未修改正式/验收库、调用模型或重启服务。表注册、迁移与 HTTP 已接入；以下真实产品结果与这些辅助检查分开记录。

2026-10-18 主 agent 已在真实 Chrome 创建/修改记忆并保留两版历史；备份到独立实例后正文与历史读取一致（`.local/installation-acceptance/restore-read-evidence.json`）。模型实际提出的记忆候选经用户批准写入 Markdown（`generated-memory-ui-evidence.json`）；清理后原正文仍保留（`pending-and-preservation-ui-evidence.json`）。历史列表可读不等于版本比较/回退按钮的所有流程都已验收。

`memory-forget-cache-ui-evidence.json` 记录真实遗忘流程：模型读取候选记忆及两层实际历史引用后等待用户，用户在 UI 忘记该记忆，再回答原问题继续，任务完成。对实际持久片段核对直接与嵌套引用过滤，新历史不含旧依赖。该证据覆盖所选真实引用链，不将遗忘解释成自动清除原聊天/备份，也不据此承诺所有未知旧历史格式均已验证。
