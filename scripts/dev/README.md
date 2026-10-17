# 原生开发数据库

此目录的 `postgres.py` 提供项目私有 PostgreSQL16 的原生准备和控制逻辑，不依赖 Docker。Launcher 已复用同一 helper；普通用户优先使用 `./start.sh --prepare-database doctor`，下列无 `--data-dir` 命令保留开发 legacy 布局。所有路径由脚本位置解析，可从任意工作目录调用。这不是三平台通用数据库安装器。

```sh
python3 scripts/dev/postgres.py prepare  # 复用原生二进制；必要时下载默认 Ubuntu 源包并局部解包
python3 scripts/dev/postgres.py ensure   # prepare + 幂等启动、真实 SQL 检查、生成应用连接文件
python3 scripts/dev/postgres.py status   # 只读检查项目进程和 SQL 连接；未运行返回 1
python3 scripts/dev/postgres.py doctor   # 只读检查版本、SQL、凭据权限、角色与 SCRAM 认证
python3 scripts/dev/postgres.py stop     # 仅停止此项目数据目录对应的 PostgreSQL
```

`start` 与 `ensure` 相同。Launcher 通过这些入口复用准备和控制逻辑，不应复制 APT 下载、初始化或凭据生成逻辑。数据库迁移由应用的单独受控迁移入口执行；脚本只创建空 `workbench` 数据库和 owner `ai`，不建应用表。

无 `--data-dir` 时，开发数据库只监听 `127.0.0.1:55432`，关闭 Unix socket。随机管理员/应用密码保存在 `.local/postgres/passwords.json`，客户端凭据位于同目录 `pgpass`；均为 `0600`。应用连接文件为 `.local/development/database.json`（`0600`），不得输出其内容。角色 `ai` 仅拥有应用数据库，不是超级用户，不能创建角色或其他数据库；`workbench_admin` 只用于该私有集群管理。上述文件不进入 Git、分享材料或诊断附件。

显式 `--data-dir <绝对应用数据根>` 使用独立布局：`postgres/` 保存集群和受保护状态，`components/postgres/` 保存私有工具，数据根 `database.json` 保存连接及严格匹配的 managed_postgres 描述。端口在首次准备时确定并持久化，以后复用，不能猜测另一个端口或接管占用者。旧 `.local/postgres` 不会自动迁移到该布局；详见[发行说明](../../runtime/distribution/README.md)。

legacy 模式的进程、数据和日志位于 `.local/postgres/`（目录 `0700`）；`postgres.log` 为 `0600`。独立数据根模式使用对应 `postgres/` 目录。控制动作使用文件锁，停止前核验同一用户、PostgreSQL 可执行程序与该数据目录。端口冲突时退出，不结束其他服务。`stop` 使用 PostgreSQL 的 fast shutdown：回滚活动事务并正常关闭集群；应先由应用停止接单和 Worker，再停止数据库。此工具不会自动升级、清空集群或修改系统服务。

自动下载仅支持 Ubuntu 24.04；其他环境须已有可复用的原生 PostgreSQL 16。脚本优先复用项目二进制或 `/usr/lib/postgresql/16/bin`，也识别 `pg_config --bindir`。下载使用主机配置的默认 APT 源和同版本 server/client 包，不运行 `sudo`、包维护脚本或全局安装；系统共享库须可用，缺失时会明确失败。当前 ARM64 主机所需直接及传递运行库均已由系统提供，未新增全局库。开发服务关闭 LLVM JIT，不要求可选 LLVM 运行库。组件来源和许可证见项目本地 `.tools/postgres/README.md` 与原包 copyright。

已执行辅助检查：Ubuntu 24.04 ARM64，PostgreSQL 16.15，重复启动、停止后重启、真实 SQL、应用最小权限、SCRAM 与文件权限。数据库检查通过不等于产品 UI 或跨平台已验收。

2026-10-18 独立标准数据根的 PG55433 已用于实际原生应用与 Chrome 验收；另一次隔离 stop/resume 检查证明 `start.sh` 可恢复已核验的受管集群并启动四个应用组件，缺必要文件或非受管配置则拒绝且不初始化。该机制证据不能外推 Windows/macOS 或任意已有 PostgreSQL 服务的管理能力。
