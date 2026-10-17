# 已有本机 PostgreSQL 的交互配置

主 CLI 和原生启动脚本已接入 `configure-database`。从已准备的项目或版本目录，在本机终端运行：

```sh
./start.sh configure-database
```

Windows 使用 `.\start.ps1 configure-database`。底层入口为 `workbench.entrypoints.database_setup.configure() -> None`，也可用项目 Python 直接执行 `-m workbench.entrypoints.database_setup`。这是已有数据库的离线交互配置向导，不是 HTTP 设置接口、数据库安装器或数据库迁移。

不提供密码参数、环境口令或 stdin 管道方案；不要把 DSN 或口令粘贴到命令行。必要时只设置不含秘密的 `WORKBENCH_DATA_DIR` 选择标准私有数据根。Linux/macOS/Windows 共用 Settings 和 vault.private，Windows/macOS 仍等待真实设备验证；Python 语法可运行不等于三平台安装和 ACL 已验收。

## 首次设置流程

1. 必须是 stdin TTY，且不存在 `WORKBENCH_DATABASE_URL`（包括显式空值）或 Settings.database_url 优先覆盖。向导不会输出或试图复制这个旧覆盖。
2. 目标为 Settings.data_dir/database.json，只允许创建新文件。已有文件、目录、断链或链接占位都拒绝，不提供覆盖、清空或删除流程。数据目录路径祖先不能是 symlink/Windows reparse point；已存在数据根必须为当前账户私有。缺失目录逐层使用现有原生私有目录 helper 创建。
3. 在与主 CLI 相同的 instance_lock 内读取相同 launcher 状态。任何非空记录（含已退出、未知组件、未确认身份）都拒绝；不会擅自清理记录或停止进程。损坏/未知文件类型也拒绝。锁会贯穿交互、连接验证、最终确认和发布，避免合作的 launcher 同时启动。
4. 输入主机（默认127.0.0.1）、端口（默认5432）、应用专用数据库名、应用专用角色。只接受数值回环 IP 或 localhost，后者明确固定为127.0.0.1，不通过 DNS 选择其他网络；IPv6 输入 `::1`。不接受 Unix socket 路径、任意域名、多地址列表、私网或公网地址。
5. getpass 读取非空口令，不 strip，拒绝 NUL/超长；无法关闭回显时拒绝而不回退到可见输入。使用 psycopg 真实连接，connect_timeout=5；默认只读事务、statement_timeout=5000、lock_timeout=5000。只执行固定 SELECT 校验 PostgreSQL 版本、实际库/角色、服务 IP/端口及只读状态，然后 rollback/关闭。
6. 展示真实版本、无口令的目标摘要和保存路径，只有输入“保存”才继续。再次检查 override、目标和 launcher 状态，用现有 vault.private.write_json(exclusive=True) 原子发布 `{database_url: ...}`。POSIX 文件0600，Windows依赖当前用户专用原生ACL，不将 chmod 宣称为 Windows ACL。

所有 PG* 连接环境变量在该离线进程连接检查期间临时移除、finally 原样恢复；不将用户密码放进子进程 argv、环境或临时文件。URI 各字段正确转义，并带固定数值 hostaddr 防止以后被 libpq 的 PGHOSTADDR 改换目标。此版本明确只做本机 TCP，sslmode/gssencmode 均 disable，不读取客户端证书/密钥，也不提供 LAN 明文数据库接入。未来非回环/TLS数据库配置需单独设计与验证，不能扩大本函数目的地。

## 范围和失败边界

不创建或迁移库、不改变角色/密码/权限、不启动 PostgreSQL/API/Worker、不创建应用所有者、不读取业务正文、不生成样本数据。不会写 managed_postgres，因此 BYO 服务生命周期仍归原生服务管理者；当前依赖私有集群管理员的受管备份功能不因“连接通过”自动支持 BYO 实例。SELECT 成功只证明本次连接及身份，不证明迁移权限、应用兼容性或完整产品验收。

非TTY/环境覆盖/已有配置在状态创建前拒绝。取得锁需要创建私有数据/runtime目录和 launcher.lock，因此用户取消或连接失败后可留下这些安全空状态，database.json 不发布。私有 runtime 父目录已经限制其他账户访问；复用现有 launcher 的锁文件模式，仍拒绝异常归属、链接、重解析点和多硬链接。文件路径检查与同一锁约束合作程序，不声称阻挡宿主同用户恶意并发改文件。

连接/驱动/设置异常转换为固定中文，不输出 DSN、密码、原始 psycopg 报错或 traceback。若发布已开始时中断/文件同步结果未知，明确提示“保存结果未确认，请核对受保护的 database.json”，不声称一定未写入，也不删除或自动覆盖它。URI口令仅存在进程内存和最终私有配置；Python不可变字符串不保证安全擦除内存。现有配置需用户按产品维护流程核对，不能借此向导更换运行实例数据库。

TTY 是本机进程的交互要求，不是证明操作者物理坐在机器前的身份机制；具有 OS 账号权限的终端受 OS 自身授权约束。本函数不接受远程 HTTP 配置请求。

## 已有证据与待验收

真实 Ubuntu24.04 ARM64 + 项目新私有 PostgreSQL 55433（ai角色）仅用于只读 SELECT；没有更改 `.local/installation-acceptance/data/database.json`。通过真实 PTY观察终端ECHO关闭、口令未出现在输出，成功写新的0600配置；另外核验非TTY、已有配置、未知launcher记录、非回环、真实错误口令、最终取消、显式DSN覆盖及symlink数据目录拒绝。临时分支数据清理，不启动新配置实例。准确成功文件路径由本轮辅助结果提供给主agent，不在文档记录凭据或DSN。

主 agent 后续已通过原生 `start.sh configure-database` 真实 PTY 验收：对现有私有 PG55433 执行只读连接验证，口令不回显，新的0600配置成功保存，原配置保持不变；证据为本机 `.local/cli-product-acceptance/wizard-evidence.json`。这证明本机终端产品入口，不代表完整 Web 安装向导或使用新配置启动应用也已通过。Windows/macOS 终端、NTFS ACL、目录与已有原生 PostgreSQL 连接流程仍等待对应设备验收。

## 官方主来源

- [Psycopg Connection.connect](https://www.psycopg.org/psycopg3/docs/api/connections.html)：使用项目已有 psycopg，关键字 libpq 参数与连接上下文。
- [PostgreSQL16 libpq连接参数](https://www.postgresql.org/docs/16/libpq-connect.html)：host/hostaddr、connect_timeout、options 和 sslmode。向导只存显式数值本机目的地。
- [Python3.12 getpass](https://docs.python.org/3.12/library/getpass.html)：口令回显控制与 GetPassWarning。拒绝警告回退。

本轮无新增依赖、系统安装、证书生成、系统服务/数据库权限修改。来源查阅日期：2026-10-18 Asia/Shanghai。
