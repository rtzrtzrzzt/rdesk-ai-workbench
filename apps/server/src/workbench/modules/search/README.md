# 搜索与公开网页读取

此模块提供 SearXNG JSON 适配、用户/工作区连接配置与轻量静态 HTML/纯文本读取，不启动模型，也不自动换用付费 API。根 HTTP、工具注册及迁移由调用方接入。

## 接口与事务

唯一 ORM 符号 `domain.SearchConnection`，表 `search_connections`。`user_id` 归属检查和工作区访问检查不可省略；工作区连接覆盖用户连接（即使工作区连接已禁用也不回退绕过）。`get_connection(..., scope=None)` 默认有效连接，显式 `user` / `workspace` 读取对应配置或 null。

函数都接受 `(db: Session, user_id: str, workspace_id: str, ...)`，返回 `dto.py` 中稳定 Pydantic DTO：

- `get_connection(..., scope: Scope | None = None) -> SearchConnectionView | None`
- `save_connection(..., command: SearchConnectionSave) -> SearchConnectionView`
- `get_capability(...) -> SearchCapability`
- `async check_connection(...) -> SearchCapability`
- `async search(..., command: SearchQuery) -> SearchResult`
- `async fetch_page(..., command: FetchPage) -> WebPage`

调用方负责 commit/rollback；保存、检查、成功搜索会 flush。异步接口要求调用方 Session 没有待提交写入：先在工作线程取得脱离 ORM 的 DTO 快照并 rollback 释放认证/配置读事务，再进行网络 I/O；回程在工作线程重新检查当前有效配置 id/version/enabled 及归属。最终对 user/workspace 两个 scope 取得短事务锁，防止用户连接被新的工作区覆盖或授权在检查与提交之间被修改。网络期间不持有 DB 事务。Session 不被并行线程使用，取消会等当前 DB 线程退出再传播。配置写入通过短事务 PostgreSQL advisory lock 串行化同一 scope 的首次创建与更新；新建 `expected_version=null`，更新必须传实际整数 version。网络检查采用配置版本条件更新，旧检查不覆盖新配置。检查错误作为 capability 返回，调用方应 commit 保留真实诊断；搜索/读取失败抛 `AppError`。没有自动后台探测。

连接 `enabled=false` 或未配置时，搜索、网页读取及检查都不会发出网络请求。读取也受同一有效连接和归属授权。模型参数不接收端点；端点仅由用户配置入口提供。`query_connection` 是使用已授权不可变配置快照的底层适配接口，不应直接暴露给模型以接收任意连接对象。

## 能力与结果语义

`not_configured` 表示无有效配置；禁用、未检查、检查过期和真实失败均为 `unavailable`，带可诊断 error。只有真实 JSON 搜索成功后五分钟内才为 `ready`。检查向用户指定服务发送固定查询 `SearXNG`，会接触其上游引擎；仅 `/config` 响应不代表搜索就绪。空结果且引擎报告失败时不能确认可用；正常空结果允许成功。

搜索第一页，最多 20 条且不能超过连接上限；引擎、语言、地区、超时仅由连接配置决定。地区编码通过官方语言代码（如 `zh-CN`）表达，不发送虚构 region 参数。查询拒绝可覆盖引擎/语言的 `!`、`:` 前缀 token；域名筛选严格应用于实际返回 URL，可能少于请求数。部分引擎失败保留成功来源并给出 warnings。来源包含 URL/title/snippet/引擎/fetched_at；搜索摘要是未可信外部内容，不是已抓取全文。fetched_at 指工作台获得结果时间，不是网页发布日期。

单次搜索有总超时（3–60 秒用户配置）、2 MiB 原始响应上限和约 40,000 字符结构内容预算。每页读取总超时不超过 30 秒、2 MiB 响应、50,000 字符正文、5 次重定向。外层取消传播 `CancelledError` 并关闭 HTTP 连接；没有隐式重试。要求 identity 内容编码，服务忽略时明确拒绝，避免解压缩突破预算。

## 网页读取边界

公开 reader 仅接受 HTTP/HTTPS 标准端口、无 userinfo/fragment 的 URL。每跳解析所有 DNS 地址，任何非公网、保留、环回、私有、链路本地、共享地址、组播、IPv6 隧道映射地址均拒绝。解析后以选定数值 IP 拨号，保留原 Host 与 httpcore 官方 `sni_hostname` TLS 名称，阻止检查后再次解析导致的 DNS rebinding。每次重定向重新完整检查；拒绝 HTTPS 降级、不转发认证/持久 cookie；不用环境代理（trust_env=False）。这是本 HTTP 入口保护，不是 OS 出网隔离，也不约束 SearXNG 上游引擎、任意命令或其他工具。

本机网络目前把多个公网域名解析到 198.18.0.x/2001:2::x 保留测试网段，reader 会如实拒绝，不把这些地址自动加入公网白名单。没有偷偷切换公共 DNS/代理。需要操作系统提供真实公网解析后才能抓取这些域名。

HTMLParser 不执行 JavaScript，不请求子资源，不展开外部实体；清除 script/style 等并保留简单段落换行。正文为空时明确报告可能需要 JavaScript。页面正文和搜索摘要均是不可信证据，调用方必须按来源上下文处理，不能当系统指令。本 reader 没有引入 Crawl4AI，也不依赖浏览器；项目独立 browser 模块已有 Playwright 路线及自己的授权、代理和出网限制，不能将 reader 的访问许可自动扩展给浏览器。

## 当前辅助检查证据（2026-10-17）

- 真实本机 SearXNG 搜索 `SearXNG search API documentation`，返回 docs.searxng.org 与官方 GitHub 等真实 URL/摘要/时间，部分引擎失败警告未丢弃。
- 实际拒绝 127.0.0.1、::1、localhost DNS、169.254.169.254 和整数形式环回地址。
- 实际读取 `https://1.1.1.1/cdn-cgi/trace` 返回 HTTP 200 text/plain；只记录字节数，不保存该诊断页正文。
- 实际数值 IP 拨号同时使用 `Host: one.one.one.one` 和 `sni_hostname=one.one.one.one`，TLS 校验通过；改用 invalid.invalid SNI 时实际被 CERTIFICATE_VERIFY_FAILED 拒绝；取消实际请求时 CancelledError 传播。
- 多个普通公网域名因本机保留地址 DNS 被拒绝；此限制尚未由真实公网域名抓取验收。重定向拒绝逻辑已实现，未伪造一条公网转私网响应来宣称实际网络覆盖。
- ruff 静态检查通过。以上均为辅助检查，不能代替主 agent 的真实产品 UI 验收。
