# 受管浏览器 companion

此模块复用已安装 Playwright 1.64.0 与 `/usr/bin/google-chrome`，不新增依赖、不接管日常浏览器、不引入第二个 Agent 运行时。主应用负责身份、用户网络授权、审批、Operation 账本、工作区租约与 artifact 发布。网页内容、DOM、截图均是外部不可信输入，不能授予权限。

## 启动与边界

底层 standalone 服务默认未启用。工作台产品层自2026-10-19起采用默认开启的用户级电脑总开关；开启后由host注入`allow_public_network=true`，可访问公网HTTP(S)目标，无需逐个填域名。关闭即时撤销所有工作区的旧会话，重新开启不复活。AI创建、观察及动作仍经独立本地审核；模型不能用工具参数扩大权限，截图不外传。以下独立辅助入口仍需明确启用：

```sh
.venv/bin/python -m workbench.modules.browser.entrypoint \
  --enable --data-dir .local/acceptance/data \
  --trusted-local-origin http://127.0.0.1:8766
```

`trusted_local_origins` 产品默认空列表；上例仅是此前真实验收页面的明确授权，不是内置 localhost 许可。会话 `local_origins` 必须是管理员配置的精确 origin 子集；旧精确授权模式的 `allowed_origins` 同样由host注入，范围是完整scheme/host/port。全局公网模式也是从持久用户设置产生，IPC与创建参数必须一致，禁止把模型提供的`enabled=true`或origin当成用户批准。底层双方未启用时不能创建会话。

产品使用主 launcher 的 automation companion，通过现有私有 transport 绑定端点：Linux/macOS 为0600 UDS，Windows为127.0.0.1随机端口加高熵token与当前用户原生ACL。客户端核验endpoint元数据权限、真实PID/create_time/executable/instance nonce，每次私有请求认证，不接受任意远程endpoint。Windows传输已有实现但本轮无实机验证，不能据此声称Windows浏览器能力已验收。模块 standalone main 仍仅POSIX，预绑定0600 socket传fd，避免Uvicorn便利路径chmod0666；它不生成主launcher身份metadata。已有或未知归属socket不覆盖；异常退出只在严格证明原进程已退出且record/inode仍匹配时清理传输，不据此声称业务会话已关闭。Linux已实测；macOS浏览器组合等待真实设备验证。没有公开CDP入口。

每会话独立自有 Chrome/profile/context，单一受管页面；不导入 storage_state、用户 cookie、日常 profile 或应用凭据。Chrome使用显式最小环境，不继承服务的密钥、代理或数据库环境；profile在私有项目目录。Chrome SingletonSocket 受Linux Unix路径长度限制，故本会话仅用随机0700 `/tmp/workbench-browser-*` 临时目录并在停止后清理；不会写全局HOME。保留 Chrome sandbox，不以失败为由静默禁用。profile退出时删除不等于SSD安全擦除，页面本身的JavaScript正常执行。

## 精确接口

异步 `BrowserClient(data_dir)` 可供 HTTP 后端与 ToolExecutor 共用：

- `capability() -> BrowserCapability`
- `create(owner:BrowserOwner, command:BrowserCreate) -> BrowserSession`
- `observe(owner,session_id,command:BrowserObserve|None=None) -> BrowserObservation`
- `action(owner,command:BrowserAction) -> BrowserActionResult`
- `download(owner,session_id,command:BrowserDownload) -> BrowserDownloaded`
- `stop(owner,session_id) -> BrowserStop`

`BrowserOwner(user_id,workspace_id,run_id=None)` 必须由可信调用方注入，UUID；同会话每次完全匹配身份。companion不访问DB，主入口须先校验这些实体归属/当前有效授权；Agent参数不能自报这些值。独立进程内 `BrowserService` 提供同名方法（observe需显式command）。导入不会启动进程。

`BrowserCreate`：request_id(UUID)、enabled=false、allowed_origins=[]、local_origins=[]、allow_public_network=false、timeout_seconds=20(1..60)、ttl_seconds=900(30..3600)。`allow_public_network`由host按真实用户设置注入，不是模型可自授的网络许可。最多4个活动会话（可信启动配置上限8），每进程最多256条会话历史。重传同create请求返回同会话；参数/身份不同409，不重新启动浏览器。

`BrowserObservation`：session_id、observation_id(UUID字符串)、generation、captured_at、url/title/text、truncated、elements[{ref,tag,role,name,input_type,href}]、screenshot_path/width/height(nullable)、blocked_requests、scope=main_frame。纯文本最多32000字符，最多200个可见交互元素，名称最多240字符。ref是当前观察的受管ElementHandle；`page` 是导航/页滚动/页键盘目标。不提供任意selector、坐标或调用者JS；内部只有固定DOM读取与检查脚本。iframe没有假造定位引用，不声称完整无障碍树。

`BrowserAction`：request_id、session_id、last_observation(UUID)、target_ref、kind=navigate|click|type|scroll|key；仅允许对应url/text/key/delta_y参数。type最多16000字符，只填普通字段；password/file input明确unsupported，凭据必须由后续独立可信引用/审批机制处理。key限Enter/Tab/Escape/ArrowUp/ArrowDown/Home/End/Space，不开放系统快捷键或任意按键串；滚动±3000。Chrome 自动下载和上传保持关闭，文件 input 不接受任意本地路径。显式静态链接下载见下文，凭据继承和上传未因此开放。

DOM MutationObserver闭包、主frame导航代数、URL及元素连接性共同校验旧观察。DOM变化/导航/动作/停止使旧ref失效，必须重新observe；页面变化过快时观察直接返回冲突，不把不一致快照当成可操作证据。每会话动作锁立即拒绝并发写，不排队旧动作。每会话最多128个动作结果，request_id同payload重传只取缓存；异payload409。停止后清除页面结果缓存。companion重启不复活session；持久幂等与结果核对归主Operation账本。

`BrowserActionResult.execution_status` 为executed/not_executed/outcome_unknown；`business_outcome` 永远unverified，附started_at/finished_at、error_code/message、可空的新observation与requires_observe。executed仅证明Playwright动作返回，不代表表单、购买、发帖或其他网站业务成功。HTTP200也是协议接收证据。任何动作调用后的timeout/中断/无法取得完整证据均unknown，绝不自动点击重放；传输丢失的客户端AppError带outcome_unknown=true。主执行器应将这些事实写入Operation/Attempt，并要求回执核对。

stop绕过动作锁，先拒绝新输入并取消本会话动作，关闭其代理、context和自有browser；不按进程名杀Chrome。`BrowserStop` 返回status=stopped|stopping、interrupted_action、owned_resources_closed、external_effects_reverted=false。只有实际browser断开才声称资源关闭；超时保留stopping并由reaper继续确认，不删除活profile或声称外部效果撤销。TTL到期同样拒绝旧动作。

## 网络控制及其实际边界

每会话独立随机回环 HTTP/CONNECT proxy，Chrome明确指定且无DIRECT fallback，取消隐式loopback bypass。代理每次解析目标，拒绝任一非公网答案并直接连接选定的数字IP；HTTPS仍由Chrome用原域名执行TLS证书验证，代理不解密。每个HTTP请求/CONNECT、页面route/重定向/子资源再次检查网络范围。旧精确授权模式限标准端口与既有origin；全局公网模式允许公开HTTP(S)目标及非标准端口，仍拒绝loopback/private/linklocal/metadata/保留/过渡地址。本机仅精确授权origin，且其 `/api`（包含规范化/编码路径）一律拒绝，不能碰工作台自己的API。

关闭Service Worker、WebSocket、弹窗、Chrome自动下载、浏览器扩展、QUIC及非代理WebRTC UDP；固定init脚本移除WebRTC构造入口。HTTP明文代理拒绝chunked请求/Upgrade、单请求body最多2MiB、关闭复用以避免换目标；每会话网络总量128MiB、并发连接24、连接生命周期120秒。参数均以库调用传递，无shell拼接。浏览器与代理不读取应用vault或工具环境，页面没有后端工具bridge。

这是受管Chrome的应用层限制，不能等同OS网络/文件/进程隔离或承诺抵御浏览器漏洞、所有未来协议。TLS隧道看不到路径，路径策略仍依赖Playwright route；未获授权的程序不应使用本代理。目标页面CPU/内存没有OS硬配额，时间/并发/输入输出预算用于限制负载和发现异常。当前环境某些DNS将官方域名映射到198.18.0.x/2001:2测试网段，会如实拒绝；不切DNS、不放宽私网规则掩盖失败。

## 截图与发布

`BrowserObserve(screenshot=True,output_dir=...)` 只接受可信后端给定的、已经存在的 `data_dir/workspaces/<owner.workspace_id>/staging/<run_id>/<attempt_id>`；run_id若绑定则必须匹配，不允许符号链接。只写随机新PNG(0600、viewport1280×900、最多8MiB)，不覆盖原文件、不创建DB记录。`screenshot_path` 交回当前worker，由主文件服务以现有journal/artifact协议发布；前端不得把本机路径当下载URL，模型也不能自报output_dir。截图默认保留页面光标，避免Playwright隐藏光标时自身DOM修改污染版本校验。

## 实际辅助检查（2026-10-17）

Ubuntu24.04 ARM64，已安装Playwright1.64.0，实际启动Chrome155.0.8059.84，sandbox=true。仅用现有acceptance真实归属，独立空登录profile访问8766真实SPA，页面显示“尚未连接工作台/Failed to fetch”是自身API禁令的真实结果，不声称已登录或业务可用。真实页面的消息textarea完成click、输入本仓库已批准文档标题、Tab和scroll，没有点发送或创建业务对象。旧观察not_executed/browser_observation_stale；自身API导航not_executed/browser_target_forbidden。

真实 `https://1.1.1.1/cdn-cgi/trace` 读取通过，正文含真实trace字段；同一动作request_id返回缓存，不重发。真实PNG29177字节、1280×900、文件头正确，限现有succeeded Run/Attempt staging；辅助截图检查后仅删除本轮输出。停止实测返回stopped/owned_resources_closed=true（一次0.080秒），活动会话0。关闭顺序曾实际暴露Python3.12 server.wait_closed等待活动连接的问题，已修为先关闭自有隧道再等待server。静态检查通过。

这些是辅助验证，不替代主agent最终真实UI验收；未制造网站回执、付费请求或登录数据。真实外部提交超时/权限审批/持久Operation集成/需Cookie认证的下载/上传/凭据填入/Windows/macOS组合仍需对应实现或真实环境验收，不应据本记录宣称全部浏览器业务通过。


## 显式受管下载（REQ10 / U30）

`BrowserSession.downloads_enabled=false` 专指 Chrome 自动下载继续关闭，`accept_downloads=False` 和 page download cancel 保持生效。新增 `explicit_download_supported=true` 表示实现独立显式静态链接下载，并不表示可读取需网页登录、POST、Blob、data URL 或 JavaScript 生成的下载。

`BrowserDownload` 严格字段是 `request_id:UUID, last_observation:UUID, target_ref:e<digits>, output_dir:str`。目标必须是当前观察已有的真实 HTML `a.href`；没有 URL、任意 selector、JS 或请求 headers 参数。观察内部按 ref 保存当时真实 href，`BrowserElement.href` 仅公开 HTTP(S)、无 userinfo 且当前会话网络策略通过的规范目标（去 fragment），其余为 null。主应用可按持久 observation/ref 冻结审批显示；实际执行仍重新核对观察、DOM revision、导航世代、连接性与完整 href。href、DOM 或导航变化即拒绝，不能猜测替代链接。

下载复用本会话 `SessionProxy.address`，仅连接其随机 127.0.0.1 代理；每一跳再次做原 `NetworkPolicy` 验证，代理对新连接重新解析并检查全部地址答案、拨号数字 IP。HTTPS URL 保留原始域名，由 HTTPX 在 CONNECT 隧道中做 TLS/SNI/证书验证。重定向只手动接受 301/302/303/307/308，最多 3 跳，每跳仍须满足用户授权的会话网络范围（精确origin或全局公网模式），不能跳到未授权内网地址或自身 API。没有 DIRECT、环境代理或更换 DNS 的回退。

HTTPX 使用已有 0.28.1，`trust_env=False`、transport retries=0、follow_redirects=False，每跳清空临时 Cookie jar；不读取浏览器 Cookie、storage state、Bearer、应用凭据或 Referer，也不继承响应 Set-Cookie。请求 `Accept-Encoding: identity`，压缩响应直接拒绝；只流式读 `aiter_raw` 原始内容编码字节，不透明解压。只接受完整 HTTP 200，Content-Length 和实际累计字节均限 16 MiB，总 async 时限 30 秒（单连接 10 秒）。30 秒是异步网络/动作边界，不是 OS 对磁盘故障的硬中断保证。

`output_dir` 只能由可信宿主注入，沿截图同一 `_output_path` 校验：已存在的 `data_dir/workspaces/<owner.workspace_id>/staging/<run-or-session UUID>/<attempt-or-request UUID>`，绑定 run_id 时必须匹配，拒绝相对路径、跨工作区、符号链接和其他位置；下载另复用现有 private_directory 校验目录所有者/0600 类私有权限或 Windows ACL。当前模块没有宿主 `allowed_output_roots` 对象：主应用仍需按本次持久 Operation 的精确输出根再次验证来源路径、hash 和大小，不能把 model 提供的目录传入。

只以独占创建写 `browser-download-<requestUUID>.candidate`（0600；Windows继承现有私有目录 ACL），不覆盖已存在目标。显示 `filename` 从响应/URL 清洗得到，不参与路径构造。写完重新从实际文件计算 SHA256、核对累计字节/文件身份/大小/链接数并 fsync；HTTP response、client 和文件 descriptor 都关闭后才返回 `BrowserDownloaded`：

`request_id, session_id, generation, last_observation, status='downloaded', candidate_path, sha256, bytes, mime, mime_source, filename, source_url, final_url, started_at, finished_at, cookies_inherited=false, business_outcome='unverified'`。

`mime_source=response_header|fallback` 区分服务器声明与默认 application/octet-stream，不能用它冒充内容魔数验证。`source_url` 是被观察的原始 HTTP 目标，`final_url` 是重定向后的实际目标。downloaded 只表示该原件真实字节已完整保存，尚未成为可下载 artifact，更不证明网站业务目标成功；主文件服务负责不可变发布、真实类型探测、归属和用户下载。

失败、超限、stale、timeout、cancel 都清理本轮仍可确认身份的候选。路径已换成未知归属或清理无法确认时保留它并返回 `browser_download_cleanup_unknown/outcome_unknown`，不删除未知对象或谎称清理完成。每会话仍单写，stop 不排队，取消下载任务、关代理，等待下载 client/文件 finally 清理后再确认 owned_resources_closed；未完成则保持 stopping。

native 小缓存与动作共用 128 个请求预算，同 UUID 异参数 409，失败请求也不会同 ID 重新发网络请求。成功重传先核候选实际 hash/bytes；候选被主应用发布、移走或改变则报告 `browser_download_candidate_unavailable`，不重新下载。缓存不跨 companion 重启，at-most-once 和传输丢失后的持久结果核对仍由主 Operation 账本保证。GET 也可能消耗远站一次性链接，超时后不自动重拉。

私有路由为 `POST /sessions/{session_id}/download`，body `{owner,command}`，与现有 companion 身份认证相同。主 launcher 的 automation companion 使用既有 identity-checked private transport（Linux/macOS UDS，Windows随机私有 loopback+token）；本模块没有开放公网下载端点。旧 standalone `browser.entrypoint.main` 仍是 POSIX 低层入口，不生成主 launcher 所需的 endpoint metadata；产品使用主 launcher，不能用它绕过持久 grant。

### 本轮实际辅助检查

Ubuntu24.04 ARM64，真实 Playwright1.64.0 / Chrome155.0.8059.84，`chromium_sandbox=True`。本轮私有 `/tmp` 目录、0600+token UDS companion、随机精确 loopback HTTP origin；只读现有归属目录 ID，未写 PG、正式/验收用户文件或现有服务。实际输入为 `docs/README.md`、`files-computer-ui.md` 和此前已从官方源下载的 20298210 字节 LibreOffice common deb；目录 listing 来自真实临时文件列表，HTTP redirect/gzip/慢流用于明确协议边界检查，不是伪造业务成功。

5 组辅助检查通过：真实 BrowserClient 下载与文件 hash/bytes/0600；缓存/已移走候选不重拉；3 跳重定向上限/未授权目标拒绝/无浏览器或 redirect Cookie 继承；真实大包 16MiB 限额和 gzip 拒绝后候选为空；re-observe 与真实 DOM mutation 拒绝旧 ref；慢速真实 MD 下载 stop 后候选为空、代理任务为零、浏览器断开、owned_resources_closed=true。初次停止辅助脚本把目录导航误当下载起点，已修起点并通过，不将测试失误说成产品修复或业务结果。测试进程/Chrome/服务和私有临时目录均清理，这些机制检查与后续产品验收分开记录。

2026-10-18 主 agent 已通过实际工作台 UI 完成显式下载：授权的自有 loopback 服务只提供真实项目 README，浏览器实际观察后选择明确链接，下载发布的原件与源文件字节完全一致；结束后 session 为 stopped，`owned_resources_closed=true`。证据为本机 `.local/installation-acceptance/browser-download-ui-evidence.json`。没有制造网站提交回执，也没有将下载成功称为远端业务操作成功。

HTTPX 下载是受限应用代码，不是新的 OS 网络沙箱；本轮 Chrome 的 OS sandbox 真实保留。需 Cookie 的认证下载、非标准动态下载、Windows/macOS 实机组合仍没有本轮效果验收，不以静态成功冒充它们。
