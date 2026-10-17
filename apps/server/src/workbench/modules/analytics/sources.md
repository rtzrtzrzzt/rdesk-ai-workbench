# 计量事实与官方来源

项目事实来源：`modules/usage.py`、`modules/models/execution.py`、`modules/models/providers.py`、`modules/runs/domain.py`、`modules/tools/domain.py`，以及 `docs/technical/models-and-context.md` 第8节。

提供方适配已将 OpenAI input/output 与 Anthropic uncached/cache_read/cache_write 归一化；本模块只读取标准字段，不重复解析 opaque/raw。source=provider 本身不意味着全量 token、账单或费用已知。计时来自执行器单调时钟，归档时间用UTC墙钟关联。

核对官方文档（2026-10-17）：

- [PostgreSQL16 Aggregate Functions](https://www.postgresql.org/docs/16/functions-aggregate.html)：sum 对无输入返回null，percentile_cont 连续插值；保留这些未知语义，不 coalesce token 为0。
- [PostgreSQL16 Grouping Sets](https://www.postgresql.org/docs/16/queries-table-expressions.html#QUERIES-GROUPING-SETS)：同一查询聚合总计与真实时间桶。
- [Linux /proc 文档](https://docs.kernel.org/filesystems/proc.html)：MemTotal/MemAvailable 与系统内存口径。统计仅服务可见系统视图。
- [Python os](https://docs.python.org/3/library/os.html#os.getloadavg)：load平均数与CPU可用计数；未把load写成百分比。
- [Python shutil](https://docs.python.org/3/library/shutil.html#shutil.disk_usage)：真实文件系统容量。
- [NVIDIA nvidia-smi](https://docs.nvidia.com/deploy/nvidia-smi/index.html)：选择性CSV查询；设备不支持项可为N/A；命令输出兼容性有局限，因此解析失败明确unavailable。
- [NVIDIA DGX Spark 规格](https://www.nvidia.com/en-us/products/workstations/dgx-spark/)：GB10统一系统内存。这里据实际设备名称GB10标记重叠，不把产品标称128GB替代实际/proc计数。

无新增依赖；Python stdlib、项目现有 SQLAlchemy/PostgreSQL 和主机已有 nvidia-smi。未下载或安装 NVIDIA SDK/驱动，也不改变 GPU/电源/时钟配置。所有硬件查询都是固定只读命令。
