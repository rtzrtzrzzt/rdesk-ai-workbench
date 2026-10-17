# 官方来源与依赖

核对日期2026-10-17，实际已安装Python Playwright1.64.0，Google Chrome155.0.8059.84；没有新增包、下载浏览器或重型套件。Playwright是项目已有dev依赖，运行companion时必须明确准备此可选组件；主API导入client/dto不强制启动Playwright。

- [Playwright Python browser contexts](https://playwright.dev/python/docs/browser-contexts)：独立上下文与存储，不复制日常登录态。
- [BrowserType API](https://playwright.dev/python/docs/api/class-browsertype)：launch_persistent_context、executable_path、env、proxy、chromium_sandbox；指定系统Chrome的兼容性须以本机实测为准。
- [BrowserContext API](https://playwright.dev/python/docs/api/class-browsercontext)：route、service_workers=block、route_web_socket、add_init_script、关闭资源。
- [Playwright network](https://playwright.dev/python/docs/network)及[Service Workers](https://playwright.dev/python/docs/service-workers)：被Service Worker处理的请求不能只依靠普通route，因此禁用Service Worker。
- [Chromium proxy support](https://chromium.googlesource.com/chromium/src/+/HEAD/net/docs/proxy.md)：HTTP proxy侧解析、HTTPS CONNECT保留端到端TLS、DIRECT fallback及 `<-loopback>` 隐式bypass减法规则。它不是OS隔离承诺。
- [Playwright ElementHandle](https://playwright.dev/python/docs/api/class-elementhandle)：受管引用绑定实际元素；页面重建后需要重新获取。
- [Playwright page screenshots](https://playwright.dev/python/docs/api/class-page#page-screenshot)：viewport截图、PNG、caret策略；本模块不执行调用者evaluate。
- [Python asyncio Server](https://docs.python.org/3.12/library/asyncio-eventloop.html#asyncio.Server.wait_closed)：Python3.12等待活动客户端关闭，因此先关闭owned tunnels再wait_closed。
- [Playwright Python LICENSE](https://github.com/microsoft/playwright-python/blob/main/LICENSE)：Apache License2.0。复用本机已有Chrome，不重新分发浏览器，浏览器自身条款与Playwright许可证分别适用。

具体API签名同时核对当前已安装1.64.0 Python包；没有将滚动文档推断为未来版本保证。UDS0600预绑定是根据本机Uvicorn当前Config.bind_socket真实实现作出的必要修正，不把默认uds配置当成私有认证机制。


REQ10/U30 显式静态下载核对（2026-10-18），复用现有 HTTPX0.28.1/httpcore1.0.9，无新增依赖：

- [HTTPX proxy](https://www.python-httpx.org/advanced/proxies/)：固定 HTTP proxy 可代理 HTTPS，HTTPS 通过 CONNECT 后仍做端到端 TLS；同时核对当前安装 httpcore `_async/http_proxy.py` 原目标 hostname 的 TLS/SNI。
- [HTTPX streaming](https://www.python-httpx.org/quickstart/#streaming-responses)和[async API](https://www.python-httpx.org/async/#streaming-responses)：原始流用 `aiter_raw`，不以自动解压后的 `aiter_bytes` 冒充原件；stream context 负责连接关闭。
- [HTTPX environment](https://www.python-httpx.org/environment_variables/)：`trust_env=False` 关闭环境代理和环境证书配置，避免工具环境偷偷改变下载目的地/凭据行为。
- [Playwright downloads](https://playwright.dev/python/docs/downloads)：浏览器 download 生命周期/取消与独立 HTTP 原件下载不是一回事；本模块继续取消 Chrome 自动下载，不用模拟 browser download 事件宣称成功。
- [Python email Message](https://docs.python.org/3.12/library/email.compat32-message.html#email.message.Message.get_filename)：仅解析候选显示文件名，再做控制字符/路径符号/平台保留名称清洗，不信任它构造输出路径。

网络策略和私有目录校验复用项目现有 `NetworkPolicy`、`SessionProxy`、`vault.private`；准确行为同时针对已安装源码核对，未声称 HTTPX 代理等同操作系统隔离。
