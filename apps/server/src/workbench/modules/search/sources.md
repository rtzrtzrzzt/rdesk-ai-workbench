# 官方来源与组件边界

核对日期：2026-10-17。只使用官方项目和既有依赖，无新增应用依赖。

- [SearXNG 官方仓库](https://github.com/searxng/searxng)，固定 revision `2a6fc07be3760543c7b6d1d1bfe62f5765d5d9d6`，版本 `2026.10.17+2a6fc07`。AGPL-3.0-or-later；原源码与 LICENSE 完整保存在 `.runtime/search-env/src`，未修改上游。私有本地组件，若以后分发或提供网络服务，应保留对应源码与许可要求。
- [官方 JSON Search API](https://docs.searxng.org/dev/search_api.html)：`/search`，format=json、q、language、time_range、pageno；JSON 必须在 search.formats 启用。引擎列表参数的准确解析同时核对该 revision 的 `searx/webadapter.py:parse_generic`，地区通过语言代码表达。
- [官方 Administration API](https://docs.searxng.org/admin/api.html)：`/config` 仅用于私有进程响应检查，不当成真实查询验收。
- [官方原生安装](https://docs.searxng.org/admin/installation-searxng.html)：独立 Python 环境和源码安装；本项目不执行其中系统 apt、useradd、systemd 操作。
- [官方开发入口源码](https://github.com/searxng/searxng/blob/2a6fc07be3760543c7b6d1d1bfe62f5765d5d9d6/searx/webapp.py)：`python -m searx.webapp` 是本地开发服务器，官方明确不建议用于生产。本项目只绑定回环，不声称生产部署完成。
- [官方 limiter 说明](https://docs.searxng.org/admin/searx.limiter.html)：limiter 依赖 Valkey。本地回环开发配置关闭 limiter，不额外启动 Valkey。上游自带 SQLite 引擎缓存放项目私有 TMPDIR；应用数据库依然 PostgreSQL。
- [httpcore 官方 SNI 扩展](https://www.encode.io/httpcore/extensions/#sni_hostname)：数值 IP 拨号时保留原 TLS SNI/证书主机名。当前 httpx 0.28.1 / httpcore 1.0.9 的 transport 源码核实 extensions 原样传递；未使用私有 backend API。httpx/httpcore 为 BSD-3-Clause，沿用应用已有依赖。
- [Python ipaddress 官方说明](https://docs.python.org/3/library/ipaddress.html)：地址分类；额外拒绝保留/组播/隧道映射。
- [Python HTMLParser 官方说明](https://docs.python.org/3/library/html.parser.html)：仅非执行静态文本解析，不承担浏览器网络边界。
- [Crawl4AI 官方仓库](https://github.com/unclecode/crawl4ai)：Apache-2.0；本阶段未安装、未依赖、未调用云服务。复杂页面需要时再评估实际成本和浏览器出网边界。

SearXNG 依赖来源为其固定 revision 的 `requirements.txt` 和默认 PyPI https://pypi.org/simple。实际完整安装快照在 `.runtime/search-env/installed-requirements.txt`；第三方包安装目录的 dist-info 保留原许可证。组件元数据清单见 `.runtime/search-env/component-licenses.json`，上游未声明的字段保持 null，不推测许可证。
