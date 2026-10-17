# 私有原生 Companion HTTP 传输

本模块只处理IPC监听、私有发现、进程身份与端点文件生命周期。自动化授权、用户/工作区归属、写租约、停止/接管、Operation及业务资源关闭仍由主automation服务负责。没有数据库、进程扫描/信号发送、桌面输入或浏览器动作；不能从端点不存在推断浏览器/桌面资源已关闭。

## 工厂与接线

```python
from workbench.modules.automation.transport import (
    bind_endpoint, client_endpoint, inspect_endpoint, release_dead_endpoint,
)

owned = bind_endpoint(data_dir, "browser", instance_id)
# ASGI所有路由（包括status/stop）都必须先验证：
allowed = owned.authorize(request.headers)
# 传已绑定socket，不能使用uvicorn的uds=便利参数：
await server.serve(sockets=[owned.listener])
# serve返回后finally调用；只清理本实例未被替换的端点：
owned.close()
```

`name`仅browser/desktop，companion进程命令行必须已有唯一、完全相同的`--instance-id value`或`--instance-id=value`，nonce16..256 ASCII。此nonce是公开进程身份，不是HTTP token。工厂使用当前进程psutil公开API读取真实PID/create_time/exe并重验nonce；既有native_process.capture只允许捕获直属子进程，**未错误地用于捕获self**。保存的process字典与原native_process.matches格式兼容，跨进程客户端复用matches；同进程客户端另行核对self真实字段。

`OwnedEndpoint`提供listener/socket、record/dict（**含秘密，只用于私有内部持久化，禁止返回API或写日志**）、authorize(headers)->bool、幂等close()->None。对象repr隐藏record/监听器/私有原始字节。EndpointError继承httpx.TransportError，只有固定safe code/message，不携带token、请求或原始元数据。

- Linux/macOS：`data_dir/name/companion.sock`，预绑定socket后0600，再listen；私有父目录0700。传sockets=[listener]保持权限，避免uvicorn uds路径自行chmod666。Linux路径最多107字节/macOS103字节，超限明确失败，不搬到不明共享目录。
- Windows：AF_INET仅绑定`127.0.0.1:0`，绑定前SO_EXCLUSIVEADDRUSE，不启用SO_REUSEADDR。使用实际随机端口，不能传入任意host/port；同样将已绑定socket交给server。
- 两个平台均用secrets生成48随机字节/64字符URL-safe token，**只**存在当前账户私有`data_dir/name/endpoint.json`与内存，不进入argv/env/日志。所有请求要求`X-Workbench-Companion-Token`，hmac.compare_digest比较；Host必须与固定目标完全一致。任何Origin（包括空值/null）、Sec-Fetch-*、重复头均拒绝。缺失token的status/stop也拒绝。服务端必须安装上述authorize中间件，本模块不暗中改ASGI入口。
- data_dir必须已经存在且是当前账户私有目录。其所有祖先不得为symlink/Windows reparse point；本模块不静默chmod/修复已有用户数据。runtime子目录通过vault.private.private_directory创建：Windows复用原生owner-only DACL，文件继承后再次核实ACL与持久ACL文件系统；**没有Windows chmod等价假设或不安全回退**。

```python
endpoint = client_endpoint(data_dir, "browser")
async with httpx.AsyncClient(
    transport=endpoint.transport, base_url=endpoint.base_url,
    headers=endpoint.headers, trust_env=False, follow_redirects=False,
    timeout=70,
) as client:
    response = await client.get("/status")  # 实际业务路由由调用方提供
```

`ClientEndpoint`不可变数据类字段为transport（AsyncHTTPTransport子类）、base_url、headers（含token，repr隐藏）。只从固定私有元数据读取地址，无URL参数、模型覆盖或其他服务fallback。transport重试0、trust_env=False、keepalive0；**每个请求**在线程中重查metadata字节/文件dev+inode、socket权限/身份、exe/create_time/唯一nonce及当前进程身份。拒绝绝对URL改host/port/scheme、userinfo、Host或授权头覆盖，即使调用方误传模型URL也不发送。客户端仍应显式follow_redirects=False/timeout；这不是动作自动重试层，超时后沿原Operation语义核对，不重放点击/输入。

## 状态与异常退出

`inspect_endpoint(data_dir,name)->dict`只读且不返回token：status为bound/unavailable/unknown/missing，并在有效记录时包含name/instance_id/transport/process与固定说明。bound仅证明进程及端点文件身份，**不是HTTP就绪/用户会话健康/业务成功**。unavailable表示受私有记录约束的PID已不存在，资源状态仍需核对。PID已复用、僵尸、权限不可读、exe/nonce/create_time不符或socket/record替换均unknown；不能仅以进程名或PID采取动作。

bind永不覆盖已存在metadata或socket，包括旧/死/损坏记录。正常close先关闭本次自有listener；只有metadata原字节及dev/inode仍完全相同、socket原dev/inode和权限仍匹配才unlink。变化/无法确认则保留文件，重复close安全，不凭socket不在内存当关闭证据。

主生命周期在明确确认原executor退出后，可显式调用：

```python
result = release_dead_endpoint(data_dir, "browser", expected_instance_id)
```

只接受私有/结构有效/同平台记录、匹配expected_instance_id、确认对应PID已不存在，并在unlink前后复查同一metadata及socket dev/inode。存活/复用/僵尸/未知状态保留；返回released/missing/unknown及固定说明。**只释放传输文件，不杀进程、不关闭子Chrome、不修改业务DB、不宣布任何native会话或外部effect完成**。必须由主runtime生命周期锁串行调用，不能开放为模型命令。若metadata不存在但还有旧socket，不擅自删除无归属socket。

## 边界与本机证据

安全边界是当前OS账户：Unix0600/0700或Windows当前用户DACL保护发现文件；本机HTTP还检查token/Host/浏览器来源。相同UID/Windows用户的恶意进程可以访问同用户秘密和资源，本模块不声称抵御它；管理员/root也不在隔离边界内。这里没有OS沙箱/TLS、同用户防篡改或任意恶意进程隔离承诺。loopback不是公开网络服务，不能加代理/端口转发变成远程接口。进程/文件核对不能消除OS用户边界内的所有TOCTOU，因此异常结果保留unknown并由治理层核对。

2026-10-17实际Linux Python3.12、httpx0.28.1、psutil7.2.2、uvicorn0.54.0，自有最小诊断子进程验证通过：真实UDS/HTTP身份+token请求、无token403、Origin/Host/URL覆盖拒绝、metadata644拒绝、socket600、重复绑定拒绝、存活实例不得清理、POST停止后自有record/socket清理；另一个本轮子进程真实kill/回收后，错误instance保留、正确instance显式release成功；未知record保留。诊断只观测IPC事实，不伪造浏览器或桌面结果，临时目录均清理，无用户服务/模型/DB重启。

Windows原生loopback/ACL与macOS UDS路径均代码已实现，**等待真实设备验证**。主 Launcher、客户端与 automation 服务已接入该私有传输；2026-10-18 主 agent 另已通过真实浏览器显式 README 下载与停止/资源闭合流程，见 browser 模块说明。这不将本次 IPC 机制检查变成产品验收，也不代表 Windows/macOS 或实际桌面输入通过；桌面输入仍等待诊断和真实操作结论。
