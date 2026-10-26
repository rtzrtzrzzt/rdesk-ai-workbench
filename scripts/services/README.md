# 项目私有原生 SearXNG 开发服务

`python3 scripts/services/searxng.py prepare|start|ensure|resume|stop|status|doctor`

统一启动器日常调用 `resume`：仅启动已经准备完整的本机组件，不隐式 clone、安装依赖或生成新配置；已有受管进程还会核对服务响应。首次准备仍使用显式 `prepare`。

prepare 下载官方固定源码并仅在 `.runtime/search-env/venv` 安装依赖（默认 PyPI，仅二进制依赖轮子）；没有 apt/sudo、系统用户、systemd、Docker。原版源码含 AGPL 许可证。重复 prepare 对相同固定 revision 幂等，完整版本快照保存在环境目录。缺 uv/二进制轮子时明确失败，不修改系统补依赖。

start/ensure 绑定 `http://127.0.0.1:8888`，使用官方源码的本地开发入口 `venv/bin/python -m searx.webapp`；不是生产服务器。配置和随机 secret 在 `.local/search/settings.yml`（JSON 是合法 YAML），0600，不打印 secret；日志/PID/缓存均在 `.local/search`。不要把此配置或日志作为公开静态资源提供。status/doctor 只读、不安装、不启动；service_responding 仅表示 /config 可读，search_verified=false 明确不伪造搜索验收。

stop 只处理元数据 PID、精确命令行及 /proc start_ticks 全部匹配的进程；不抢占8888、不过问其他服务、不自动 SIGKILL。启动器锁防止并发重复启动，启动时保留端口冲突诊断。程序没有注册开机启动。

2026-10-17 Ubuntu24.04 ARM64、Python3.12.3 实测：版本2026.10.17+2a6fc07，revision `2a6fc07be3760543c7b6d1d1bfe62f5765d5d9d6`；所有依赖均有可用 ARM64 轮子，未编译系统库。源码33 MiB、venv116 MiB、项目uv缓存168 KiB、运行状态412 KiB（测量时）；空闲进程RSS79,924 KiB。stop/start/ensure/doctor实际通过，记录时PID177275；PID随重启变化，以status为准。

真实主题搜索已返回官方文档等来源；部分引擎失败保留警告。本机已有记录中的DNS会把部分公网域名转换为保留测试地址，严格网页reader会拒绝，不隐式使用公共DNS或代理。完整接口、实测边界和官方来源见[搜索模块](../../apps/server/src/workbench/modules/search/README.md)与[来源记录](../../apps/server/src/workbench/modules/search/sources.md)。服务启停及搜索机制证据各有范围，产品真实流程与当前限制以[实现状态](../../docs/implementation-status.md)为准。
