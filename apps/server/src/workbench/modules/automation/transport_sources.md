# 原生私有IPC主来源

核对2026-10-17，仅复用已锁依赖（实际httpx0.28.1、psutil7.2.2、uvicorn0.54.0）及Python标准库，没有安装依赖。

- [Python3.12 socket](https://docs.python.org/3.12/library/socket.html)：AF_UNIX/AF_INET、bind/listen、非阻塞与socket资源生命周期。
- [Microsoft SO_EXCLUSIVEADDRUSE与SO_REUSEADDR](https://learn.microsoft.com/en-us/windows/win32/winsock/using-so-reuseaddr-and-so-exclusiveaddruse)：Windows绑定前独占选项，不能借SO_REUSEADDR覆盖已有监听。使用现代Windows分支；文档事实不替代实机验证。
- [Microsoft ACL](https://learn.microsoft.com/en-us/windows/win32/secauthz/access-control-lists)：Windows访问边界基于DACL，不用POSIX chmod伪装。具体创建/验证代码直接复用本项目`infrastructure/vault/private.py`与`windows_acl.py`；该层不另造权限实现。
- [psutil官方7.2.2 API](https://psutil.readthedocs.io/stable/index.html)：Process.exe/create_time/cmdline及用户身份；[官方PID复用说明](https://psutil.io/faq/)。本模块只自行读取当前进程身份；外部进程核对复用项目native_process.matches，不按name/PID扫描或发信号。psutil BSD3-Clause为项目已有依赖，未新增/重分发库。
- [HTTPX官方transport](https://www.python-httpx.org/advanced/transports/)、[HTTPX API](https://www.python-httpx.org/api/)：AsyncHTTPTransport、UDS、自定义handle_async_request、retries与trust_env；AsyncClient由调用方显式关闭重定向/设置timeout。本模块未引入自动重试或模型可覆盖地址。HTTPX是已有BSD3-Clause依赖。
- [Python secrets](https://docs.python.org/3.12/library/secrets.html)、[hmac.compare_digest](https://docs.python.org/3.12/library/hmac.html#hmac.compare_digest)：随机token与比较，原始值不进入说明/argv/日志。

项目规则依据：已批准原生跨平台交付、Browser/DesktopProvider的私有节点/单实例归属/未知结果语义；实际API以当前`native_process.py`及`vault/private.py`为准。本模块不把传输端点不存在解释成浏览器/桌面会话已关闭。
