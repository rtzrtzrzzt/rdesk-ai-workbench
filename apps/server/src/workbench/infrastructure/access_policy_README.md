# 原生回环/LAN访问策略

AccessPolicy.from_values(host, port, lan_enabled, public_origins, secure_cookies, ssl_certfile, ssl_keyfile) 返回不可变策略。工厂只解析配置，无文件/网络/SSL IO；validate_tls_files() 是CLI启动前另行显式调用的只读预检，不应在OpenAPI导出/模块导入时调用。

## 接口和统一判定

- 字段：host/port/lan_enabled/scheme/public_origins/trusted_hosts/dev_origins/secure_cookies/ssl_certfile/ssl_keyfile/startup_owner_required。证书路径类型Path或None，其余origins/hosts是不可变元组。
- startup_owner_required 在LAN时True。主CLI/API必须确认已通过本机初始化owner，否则禁止LAN启动；本模块不读DB或假定已有账号。
- allows_host(host_header, request_scheme) 校验已列精确Host、实际端口和实际ASGI scheme；必须使用proxy_headers=False下的真实scheme。
- allows_origin(origin, request_scheme, host_header) 保留精确同源语义，多个public别名不互授跨别名写请求。仅无自定义origin的默认HTTP回环模式有localhost/127.0.0.1/[::1]5173开发例外；LAN及本机HTTPS无dev例外。缺Origin返回False，由主HTTP按接口决定是否要求；CSRF仍独立执行。
- cors_origins 合并明确公开/开发来源，供CORS粗粒度白名单；仍须逐请求allows_origin。trusted_hosts供TrustedHost（关闭www自动跳转），不能使用“*+凭据”。
- primary_origin 为第一个明确公开URL，启动器不能展示0.0.0.0或::作为访问地址。uvicorn_kwargs 包含host/port/证书路径，并固定proxy_headers=False，不应覆盖该边界。

地址按scheme/host/port规范化（DNS大小写、IPv6、默认端口），不查询DNS。ASCII DNS或用户明确提供的punycode可用；拒绝模糊数字/八进制/十六进制IPv4、wildcard、userinfo、控制字符、反斜线、转义主机、空/非法port。IPv6 HTTP authority必须方括号，bind地址不带方括号。origin不接受任何path（含末尾/）、query/fragment（含空?/#）。

## 启动合法性

默认仅127.0.0.1，允许同端口loopback Host。非回环bind（含0.0.0.0/::）必须显式LAN；bind只接受明确IP或localhost。LAN同时要求明确HTTPS public_origins、origin端口=listenport、本地cert/key、Secure Cookie。无效配置抛AccessPolicyError(code,message)，不默默退回明文LAN。0.0.0.0/::及其映射未指定地址仅作bind，不能作public origin。

本机HTTPS同样要求证书/密钥与Secure，仍只允许回环同端口；HTTP+Secure也作为不一致配置拒绝。本轮只支持原生直连TLS，不支持代理443→8765，不从Forwarded/X-Forwarded-*猜TLS/peer。不会打开网卡、防火墙、数据库、模型、驱动、云或隧道。

主CLI顺序：from_values → 按startup_owner_required检查真实owner → validate_tls_files → 用uvicorn_kwargs启动。首次owner仍严格校验实际peer为127.0.0.1/::1，LAN授权不放宽初始化；认证、CSRF、登录限流、HttpOnly/SameSite Strict、SSE/下载撤销仍由主程序负责。

## TLS只读预检

每级路径非symlink/Windows reparse；先核常规文件再打开，核对身份/可读性。私钥复用vault私有权限：Linux/macOS当前用户0600或只读0400；Windows原生当前用户DACL/持久ACL文件系统，拒绝chmod伪装及ACL失败回退。证书可公开可读。拒绝URL/UNC/设备路径，不展开HOME、不移动/复制用户材料；相对路径按CLI当前工作目录，部署推荐绝对路径。

SSLContext(PROTOCOL_TLS_SERVER).load_cert_chain核可解析与配对，前后复核文件/权限。加密key通过明确拒绝password回调失败，不交互提示/从环境或模型读口令。固定错误不回显路径、密钥、证书正文或OpenSSL细节，不生成/上传证书。

这不验证SAN覆盖、有效期、CA信任、浏览器信任或另一设备可达性，也不保证预检到服务器重开文件之间跨进程原子。CLI须紧接预检启动，变化须重核。主agent准备自有验收证书并完成真实Chrome/设备检查；本模块不要求用户现在提供证书。

## 已接入的 CLI 配置

同名 WORKBENCH_ 字段已接入 Config/CLI，可使用 `./start.sh start`，底层入口为 `.venv/bin/python -m workbench.entrypoints.cli start --no-browser`。配置示意：HOST=0.0.0.0，PORT=8765，LAN_ENABLED=true，PUBLIC_ORIGINS=["https://192.0.2.20:8765"]，SECURE_COOKIES=true，SSL_CERTFILE=/absolute/private/server.crt，SSL_KEYFILE=/absolute/private/server.key，各自加 WORKBENCH_ 前缀。PUBLIC_ORIGINS 使用 JSON 列表，不能用通配或逗号字符串。正式操作说明见[原生 HTTPS](../../../../../runtime/distribution/native-https.md)。

192.0.2.20是RFC5737文档保留地址，不是本机网卡探测或实际可访问证据；部署换成用户选择的真实网卡/origin和已准备证书。默认本机不用这些字段。不能覆盖或重启已运行实例；启动器仍核对进程与端口归属。

## 已做辅助检查与待验收

2026-10-17 Linux Python3.12/OpenSSL3.0.13：纯策略默认回环/LAN显式启用、TLS/Secure配套、精确Host/Origin端口/scheme、IPv6、dev仅默认HTTP回环、owner标志、坏URL/端口/未指定IP/非本地key路径拒绝通过。不存在证书路径能通过factory，确认纯解析不读文件。

仅/tmp本轮私有随机测试材料：自签配对加载成功；key600/400接受、644拒绝；symlink/FIFO拒绝；另一随机key不匹配拒绝；随机口令加密key非交互拒绝。临时材料全部清理；未读用户凭据、未留产品证书、未监听HTTP/TLS/LAN、未改防火墙或重启服务。ruff/py_compile通过。

以上是2026-10-17的配置/TLS机制辅助检查。后续原生 API 已验证 TLS1.3、Host/Origin 拒绝与安全 Cookie；主 agent 在真实 WiFi 地址192.168.1.9完成单机 Chrome owner 登录，HTTPX 验证 SAN/TLS，Chrome 仅 pin 该验收证书 SPKI。证据为本机 `.local/recovery-https-acceptance/physical-lan-browser-evidence.json`，不是系统常规信任或另一台物理设备的证明。账号会话撤销有独立产品证据；不能将其与 TLS 检查合并宣称全部 LAN 场景通过。Windows ACL、macOS 路径、正式证书和跨设备访问仍等待对应真实环境验证。
