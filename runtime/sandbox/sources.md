# 官方主来源与本机组件

- [bubblewrap v0.9.0 官方 README](https://github.com/containers/bubblewrap/blob/v0.9.0/README.md)：空挂载命名空间、调用者负责策略、网络命名空间、终端和 D-Bus 等边界。bubblewrap 是构建工具，不是自动具有完整策略的沙箱。
- [v0.9.0 官方参数定义](https://github.com/containers/bubblewrap/blob/v0.9.0/bwrap.xml)：本机基础参数语义；注意该上游原版不含本机 Ubuntu 回移的 bind-fd，不能凭 0.9.0 字符串假定存在。
- [v0.11.0 官方 FD 挂载实现](https://github.com/containers/bubblewrap/blob/v0.11.0/bubblewrap.c)：bind-fd/ro-bind-fd、FD 挂载后 inode/device 比较并关闭 FD。用于对照已回移能力，不声称本机安装的是 0.11.0。
- [官方许可证](https://github.com/containers/bubblewrap/blob/v0.9.0/COPYING)：LGPL-2.0-or-later。当前仅调用系统组件，不复制其二进制或修改它。
- [Ubuntu 官方安全公告 USN-7046-1](https://lists.ubuntu.com/archives/ubuntu-security-announce/2024-September/008673.html)及[官方回移发布记录](https://lists.ubuntu.com/archives/noble-changes/2024-September/041827.html)：0.9.0-1ubuntu0.1 为避免路径竞态而加入配套修复；[当前 noble-updates 官方包记录](https://packages.ubuntu.com/noble-updates/bubblewrap)为 0.9.0-1ubuntu0.3。是否支持本模块仍由本机实际 help 和试跑判断。
- 本机第一手来源：`/usr/bin/bwrap --version`、`--help`；`dpkg-query -W bubblewrap` 为 `0.9.0-1ubuntu0.3 arm64`；`/usr/share/doc/bubblewrap/changelog.Debian.gz`、`copyright`。0.9.0-1ubuntu0.1 记载回移预计进入 0.10.0 的安全功能，本机 help 实际提供 FD 参数；本机 man 仍旧，故没有只依赖 man 断言这些参数。这里只核实版本/权限/参数及本次运行结果，不声称已完成全部 CVE 审计。

访问与核验：2026-10-18 Asia/Shanghai。没有新 Python 依赖、系统包安装、Docker 或系统策略变更。批准需求依据为本项目 tools-and-recovery 第6节和 deployment-and-validation 的无静默降级条款；docs 的 REQ-08 标题实际是搜索/网页来源，未以编号错位扩展本次范围。
