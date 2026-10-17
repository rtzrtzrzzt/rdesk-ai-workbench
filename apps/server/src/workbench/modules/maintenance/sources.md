# 官方依据与组件来源

核对日期2026-10-17；沿用项目既有依赖和Ubuntu默认源下载的PostgreSQL16.15原生工具，不新增库/系统包或网络备份目标。

- [pg_dump PostgreSQL16](https://www.postgresql.org/docs/16/app-pgdump.html)：一个数据库的逻辑快照、custom归档，不包含集群角色密码，不自动保护外部文件。
- [pg_restore PostgreSQL16](https://www.postgresql.org/docs/16/app-pgrestore.html)：从custom归档恢复，标准输入/--single-transaction/--no-owner/--no-privileges；源凭据行的排除是本模块scratch净化职责，不是--no-owner自动完成。
- [Advisory Locks](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS)：应用协调，session锁跨事务，shared/exclusive冲突；不把它称为文件系统快照。
- [pg_locks](https://www.postgresql.org/docs/16/view-pg-locks.html)：真实backend/lock/mode/granted校验。
- [PostgreSQL许可证](https://www.postgresql.org/about/licence/)：沿用本地组件说明与Ubuntu包版权信息，不重新分发二进制。

文件/MD真实性和发布约定依据现有files/storage.py、files/service.py、memory/files.py、memory/domain.py。模型权重只读既有models/.verification/*/verification.json中的repo/revision与校验引用，不重复审计132GB模型。凭据清除字段来自当前ORM/实际information_schema核对，遇未审核表或新增敏感列拒绝完整备份；后续schema扩展必须继续明确该范围。
