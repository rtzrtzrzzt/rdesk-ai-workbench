# 本地单所有者账号增强（REQ19）

本模块复用现有 Cookie、服务端可撤销会话和 `pwdlib[argon2]`，不扩展多用户，不提供 HTTP 重置密码，也不改变 vault 解锁口令。HTTP 总入口的同源、CSRF、维护写 gate、Cookie 配置和可信 peer 来源仍由上层负责。

## 表与接口

迁移已包含 `AuthSession.last_active_at`（nullable、无自动回填）、`LoginThrottle / login_throttles`、`AccountCommandReceipt / account_command_receipts`。`AccountCommandReceipt.session_id` 使用 `ON DELETE SET NULL`，清除会话时不妨碍保留安全审计。表注册及原生实例迁移已接入统一启动流程，不由各 HTTP 请求自行建表。

保留 `login(db, username, password, *, peer: str | None = None) -> (User, token)`。入口必须传真实连接的 `request.client.host`，不得从 X-Forwarded-For 等请求头取得 peer；未传时统一进入 `unknown` 来源键，不绕过限速。按 peer 原始字符串与用户名的 JSON 二元组 SHA256 作为唯一键，不存原始 IP 或输入用户名。该摘要只减少明文暴露，不声称对可枚举的地址/用户名实现匿名化。

配置 helper 只读取 `getattr(settings, name, default)`，本模块不修改 Config：

| 字段 | 默认值 | 允许值 |
| --- | --- | --- |
| `login_max_failures` | 10 | 1..1000 |
| `login_window_seconds` | 900 | 1..86400 |
| `session_idle_seconds` | 1800 | 1..86400 |

`login` 是唯一自行提交的身份服务：它拒绝调用方尚未提交的 ORM new/dirty/deleted 状态，结束调用方纯读事务，然后在专用 Session 中执行“插入缺失计数行 → 行锁 → 核对固定窗口 → Argon2 验证 → 更新计数或建立会话”。失败也先提交计数，再抛出 401；调用方随后 rollback 不会清掉计数。成功清零与新会话在同一事务提交，之后用调用方 `db.get(...populate_existing=True)` 返回其所属身份视图。旧 HTTP 路由再次 `db.commit()` 不影响该已完成登录事务。**不得把 login 嵌入包含其他写入的 UnitOfWork，或预先持有会阻塞登录的外部连接行锁。**

同一键在固定 900 秒内允许记录 10 个失败，第 11 次起返回 `login_rate_limited`（429、`details.retry_after_seconds`）；受阻尝试不推迟窗口起点。窗口结束后重置，不永久锁账号；成功登录也重置本键。成功与失败通过行锁串行化，不用进程内计数。不同来源仍有独立键，此机制不等同分布式攻击防护。每次完成后另用短事务至多清理 100 个过期键并跳过被锁行，避免清理引入相反锁顺序；不宣称固定最大表行数。凭据事务锁等待最多 5 秒，清理最多 1 秒，提交不明或数据库故障不能当作已确认 401/成功。

不存在用户名仍验证进程私有 dummy Argon2 hash，损坏/不可用 hash 也走同类工作并返回通用 `invalid_credentials`。不承诺整条数据库/HTTP 路径恒定时间。有效旧 hash 通过现有 `verify_and_update` 在成功事务中升级参数。原始密码、token 不进入日志、命令回执或错误详情。

其余服务保留调用方事务边界，成功后调用方必须明确 `db.commit()`，错误时 rollback：

- `authenticate(db, token) -> (User, AuthSession)`：仅检查，不刷新活动。
- `security_status(db, token) -> AccountSecurity`。
- `record_activity(db, token) -> None`：受 CSRF 保护的显式活动写入。
- `change_password(db, token, command: PasswordChange) -> PasswordChangeReceipt`。
- `recover_owner_password(db, new_password, *, services_stopped: bool) -> AccountRecoveryReceipt`：只供原生 CLI。

## 会话与 HTTP 契约

新会话同时记录创建/最近活动时间；绝对到期沿用 `session_hours`，活动刷新不延长绝对到期。空闲判断使用 `last_active_at or created_at`，默认 30 分钟。老行 null 以原创建时间作保守基线，可能立即要求重新登录，不因升级获赠活动时间。过期和吊销均不能由 activity 请求复活。此模块不自行删除旧行，认证查询直接排除它们。

`entrypoints.routes.account.router` 提供：

| 方法与路径 | 请求 | 响应 |
| --- | --- | --- |
| GET `/api/v1/auth/security` | 无 | `AccountSecurity{idle_timeout_seconds, session_expires_at, last_active_at}` |
| POST `/api/v1/auth/activity` | `AccountActivity{}` | 204 |
| POST `/api/v1/auth/password` | `PasswordChange{current_password,new_password,request_id}` | `PasswordChangeReceipt{request_id,password_changed:true,current_session_retained:true,other_sessions_revoked,changed_at}` |

三路由依赖现有 USER 鉴权，写路由使用既有 X-CSRF-Token 验证，并在返回前 commit；总入口已接入 include、Origin 和 gate。`current_password` 非空且最多 1024 字符，`new_password` 12..1024 字符，二者 Pydantic SecretStr/writeOnly，保留原始空白，不 strip；命令拒绝未知字段。

前端只应在真正用户交互时节流调用 activity，不应给轮询、SSE、模型输出或定时器续期。后端能保证读请求不刷新，并不能从一个已授权 POST 证明有物理人类活动。SSE/长连接须由上层周期重鉴权；已建立外部连接的撤销检查边界仍由对应模块实现。

改密以固定 User → 当前 AuthSession 锁顺序重检认证及当前密码，变更 hash 并撤销其余全部未吊销会话；当前会话和 Cookie/CSRF 保留。其他会话计数是实际更新行数，可包括已绝对到期但尚未标记吊销的行。错误当前密码返回 `current_password_invalid`（409），不把当前会话误当登录过期。并发登录也锁 User，旧密码不能在改密吊销完成后建立新会话。写入锁等待最多 5 秒。

回执按 `(user_id, request_id)` 唯一，并绑定原 session。幂等冲突用已有本机会话签名密钥、带用途前缀的 HMAC-SHA256 比较命令内容；只保存 keyed 摘要，不保存明文口令、无密钥口令指纹或旧口令 hash。HMAC 值不能作为登录凭据，也不在 API 回传。相同请求/内容返回原安全回执，不再改密；同 ID 异内容 409。原回执说明过去那次改密的结果，不声称当前密码此后从未改变。凭据、会话签名密钥仍不得默认进入备份；恢复环境已撤销会话，旧会话绑定回执不能重新授权。

## 原生离线恢复

只有主 CLI 在真实核实本实例 API/Worker 的 PID、create time、executable 和 nonce 全部停止后，才能传 `services_stopped=True`；该 bool 是可信调用方契约，**不是本函数自行得到的停服证据**。入口不得向 HTTP、Agent 工具或任意浏览器命令暴露。密码由停服后的本机 getpass 读取，不用 argv/env/日志。服务确认唯一 owner，设置新 Argon2 hash、撤销全部会话、清空本单所有者实例的匿名登录失败计数，并写 `offline_password_recovery` 安全回执；caller commit 后才报告完成。无 owner 或多 owner 完整性冲突均拒绝，不创建账号。

返回 `AccountRecoveryReceipt{request_id,user_id,password_changed:true,sessions_revoked,recovered_at}`，没有口令或 token。恢复登录不自动解锁 vault、不更换其主密钥，也不自动恢复模型授权。备份/恢复上层可在恢复新环境时清除 `login_throttles`，避免继承暂时的来源失败限制。

## 依据与检查

项目依据为 `docs/technical/deployment-and-validation.md` 第 6.4 节与当前 Cookie/CSRF 服务。现有依赖实际 `pwdlib 0.3.1` 的实现与[官方 PasswordHash 参考](https://frankie567.github.io/pwdlib/reference/pwdlib/)共同核对了 `verify_and_update(password,hash)`、Argon2 推荐实现与 UnknownHashError；未新增依赖。持久键初始化复用 [PostgreSQL 16 ON CONFLICT](https://www.postgresql.org/docs/16/sql-insert.html)，串行化使用[官方行锁语义](https://www.postgresql.org/docs/16/explicit-locking.html#LOCKING-ROWS)。

已完成 9 组真实隔离 PostgreSQL 辅助检查：12 个并发同键失败精确落 10 个失败/2 个限速；请求 rollback 不抹计数与已提交成功会话；固定窗口与实际 peer 字符串隔离；认证/安全轮询不刷新而显式活动刷新；legacy null/idle/绝对到期拒绝续期；改密验证、整体 rollback/commit、当前保留/其他撤销、幂等冲突；并发旧密码仅成功一次；停服契约拒绝/本机恢复全撤销/清限速/安全审计/新登录；删除 auth_sessions 对回执 SET NULL 与 SecretStr/writeOnly schema。

上述机制检查只读验收库公开身份字段作隔离结构归属，口令为内存中随机生成并由 Argon2 实际验证的隔离凭据，没有复制真实凭据/会话。窗口/idle 边界通过明确调整隔离行时间验证，不声称等待了真实30分钟。仅当轮随机 `workbench_backup_*` 库与私有临时目录有写入，并按 ownership marker 清理；这份辅助记录没有运行 API、修改原正式/验收账号或调用模型。

后续主 agent 在原生实例的真实 Chrome 已验证修改密码后当前会话保留、另一浏览器会话撤销。离线 `start.sh recover-account` 的真实 TTY/独立 PG/HTTP 检查亦已通过，按真实执行资源关闭情况判断停服，queued/held 等持久工作流状态不单独阻止恢复；完整条件见[账号恢复说明](../../../../../runtime/distribution/account-recovery.md)。真实 WiFi-IP Chrome 登录另有独立 HTTPS 证据。Windows/macOS终端/ACL、完整30分钟空闲时序及所有浏览器撤销窗口仍不由这些有限结果自动标通过。
