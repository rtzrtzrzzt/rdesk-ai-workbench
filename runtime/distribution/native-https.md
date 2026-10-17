# 原生 HTTPS 与局域网访问

默认只监听本机回环 HTTP。先在回环模式初始化唯一所有者，再显式配置 LAN；普通 `start` 不自动开放 LAN、不生成证书或改变系统/浏览器信任。

准备与实际访问主机名/IP相符、客户端信任的本机证书和未加密私钥。两者必须是可读普通文件，路径不能经过符号链接或重解析点；Unix私钥需要当前用户独占的0600或0400权限。启动前校验证书/私钥能否配对，但这不代替浏览器对 SAN、有效期、CA链和信任的检查。

在同一受保护的启动环境配置以下非口令设置，再执行 `./start.sh start`（Windows 对应 `start.ps1`）：

```sh
export WORKBENCH_HOST=0.0.0.0
export WORKBENCH_PORT=8765
export WORKBENCH_LAN_ENABLED=true
export WORKBENCH_SECURE_COOKIES=true
export WORKBENCH_SSL_CERTFILE=/absolute/path/server-cert.pem
export WORKBENCH_SSL_KEYFILE=/absolute/path/server-key.pem
export WORKBENCH_PUBLIC_ORIGINS='["https://workstation.example:8765"]'
./start.sh start
```

例中的域名和端口应替换成当前安装的实际值；不要复用其他实例正在监听的端口。`PUBLIC_ORIGINS` 是精确 origin 的 JSON 数组，协议和端口必须与实际监听一致，不接受通配符或逗号字符串。需要多个入口时逐个列出证书覆盖的来源。只有显式启用 LAN 后才允许非回环 bind；LAN必须同时配置HTTPS证书、私钥、Secure Cookie和来源，不会降级到明文。

本版本由原生 Uvicorn 直接终止TLS，`proxy_headers=False`。Host检查包含实际端口；带 Origin 的写操作必须与请求的实际 origin 一致，不能靠 `X-Forwarded-Host/Proto/For` 获得信任。会话 Cookie 包含 Secure、HttpOnly、SameSite=strict；CSRF检查、来源失败限制、会话闲置超时和所有者权限继续生效。不要用反向代理头代替本版本的直连TLS配置。

验收时在真实浏览器打开实际HTTPS来源，确认无证书错误，使用现有owner登录并完成所需操作，再验证离开后重新登录/旧会话撤销。应从实际授权的另一台LAN设备重复访问，确认网络、防火墙、DNS与证书信任均可用；单机用LAN IP连回自己不能代替物理设备验收。

2026-10-18 的独立 API 辅助检查覆盖回环与明确配置的来源：TLS1.3、health/login200、Secure/HttpOnly/SameSite=strict Cookie；错误 Origin403、错误 Host400，伪造转发 Host 仍400，转发 Proto 不改变实际 TLS 策略。早期检查使用的 `198.18.0.1` 属于 Mihomo TUN，不能当成物理局域网地址证据。

主 agent 随后使用真实 WiFi 接口地址 `192.168.1.9` 完成单机浏览器流程。本机 HTTPX 显式信任验收证书，核实匹配 SAN 和 TLS1.3；真实 Chrome 仅对该验收证书使用 SPKI pin，完成 owner 登录并核对 Secure/HttpOnly/SameSite=strict Cookie。证据为本机 `.local/recovery-https-acceptance/physical-lan-browser-evidence.json`。没有全局关闭证书错误或修改系统信任，也未启动会消费该实例待处理 Run 的 Worker。

这证明该配置下实际 WiFi-IP 的单机 HTTPS 登录路径，不代表证书已获得系统/其他设备的常规信任。另一台物理 LAN 设备、正式证书部署和完整 Launcher HTTPS 生命周期仍须相应真实验收；不能将此记录标为跨设备 LAN 全流程通过。
