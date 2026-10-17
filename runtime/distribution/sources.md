# 原生源码发行机制主来源

核对日期2026-10-18。没有新增第三方依赖；打包/验证/stage使用Python标准库，安装复用已有uv/npm/Ubuntu原生助手。

- [CycloneDX1.6官方JSON schema](https://github.com/CycloneDX/specification/blob/1.6/schema/bom-1.6.schema.json)：SBOM格式、component类型、license、properties与dependency结构；仅按证据标记bundled与locked，不能将SBOM当成平台安装或无漏洞证据。
- [uv locking/syncing](https://docs.astral.sh/uv/concepts/projects/sync/)：`--locked`对不匹配锁报错；sync是锁定依赖的安装，不应在普通start里运行；dev默认组行为原样保留。
- [npm ci官方说明](https://docs.npmjs.com/cli/v11/commands/npm-ci/)：按现有package-lock安装、配置必须与锁兼容；本项目只在明确前端构建时调用，不因随包静态产物要求终端用户安装Node。
- [Python3.12 zipfile](https://docs.python.org/3.12/library/zipfile.html)：ZIP时间/metadata/流读取；本工具另外限制成员类型、路径、大小、重复及真实manifest，不把extractall默认行为作为完整安全策略。
- [PostgreSQL原生下载](https://www.postgresql.org/download/)、[Ubuntu](https://www.postgresql.org/download/linux/ubuntu/)、[Windows](https://www.postgresql.org/download/windows/)、[macOS](https://www.postgresql.org/download/macosx/)：跨平台原生组件路径。项目私有apt下载仅复用Ubuntu24.04 ARM64既有助手，不执行sudo/全局安装或其他服务控制。
- [PostgreSQL备份](https://www.postgresql.org/docs/16/backup.html)：DB逻辑备份不自动使外部MD/artifact一致，升级依赖现有产品静止窗口+配套真实备份，不提供in-place自动回滚。
- [Reproducible Builds SOURCE_DATE_EPOCH](https://reproducible-builds.org/docs/source-date-epoch/)：规范化构建时间的约定；本工具另外记录Python/zlib版本，明确只对相同输入与工具链承诺字节可复现。

包的实际第三方许可来自当前锁与现有许可证材料，不猜未安装平台包的全内容；完整项目Apache LICENSE/NOTICE/THIRD_PARTY_NOTICES随包保存。校验摘要不替代正式发行签名、操作系统公证或用户的发布审批。
