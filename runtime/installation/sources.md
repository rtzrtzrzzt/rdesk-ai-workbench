# 原生工具链官方来源

核对日期2026-10-17；只核查本轮选中组件，不扩大为无关生态审计。

- [psutil7.2.2官方PyPI](https://pypi.org/project/psutil/7.2.2/)与[默认源JSON](https://pypi.org/pypi/psutil/json)：实际当前版本/ABI轮子/Python支持；主agent已安装并锁定。
- [psutil官方文档](https://psutil.readthedocs.io/stable/)：Process exe/cmdline/create_time/is_running以及terminate/kill的PID重用校验；同时核对本机安装7.2.2源码，未将更新分支文档当作锁定行为。
- [psutil7.2.2 BSD-3-Clause许可证](https://github.com/giampaolo/psutil/blob/release-7.2.2/LICENSE)与[官方安全策略](https://github.com/giampaolo/psutil/security)：项目有私下漏洞报告渠道；本次针对进程重用/AccessDenied做检查，不因页面未列公告宣称不存在风险。
- [uv项目与锁文件](https://docs.astral.sh/uv/guides/projects/)及[uv官方安装](https://docs.astral.sh/uv/getting-started/installation/)：sync --locked、项目venv与默认源；实际复用uv0.9.25并核对本机help参数。不自动安装或浮动更新uv。
- [npm ci](https://docs.npmjs.com/cli/v11/commands/npm-ci/)：锁定依赖安装；项目实际npm10.9.2，参数同时通过本机版本probe与既有构建约定核对，未并发重新安装。
- [Node.js官方原生下载](https://nodejs.org/en/download)与[Python官方发行](https://www.python.org/downloads/)：缺失时给原生入口，不假设三平台有相同二进制；当前没有新增下载。
- [XDG Base Directory](https://specifications.freedesktop.org/basedir/latest/)：data/config/cache/state及绝对base规则。
- [Windows Known Folders](https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid)：LocalAppData与RoamingAppData职责；仅词法helper，不声称POSIX0600等同Windows ACL。
- [Apple文件系统目录职责](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/FileSystemProgrammingGuide/FileSystemOverview/FileSystemOverview.html)：Application Support、Caches、Logs与用户Library，实际权限/签名/安装等待对应设备验证。
- [PostgreSQL官方平台下载](https://www.postgresql.org/download/)：Windows/mac原生安装来源；Linux项目已有官方Ubuntu包私有助手由独立组件说明记录，不将PG物理目录跨平台复制。

当前交付是源码准备与原生helper。签名发行包、系统服务/登录任务、首次bootstrap Web向导、升级/卸载与跨平台真实UI必须另有实现和证据，不能从这些脚本推断已交付。
