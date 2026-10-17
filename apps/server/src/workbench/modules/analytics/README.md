# 真实用量与可见硬件，只读接口

此模块不写表、不创建样本、不调用模型、不查远端价格。仅从现有 UsageRecord、Attempt、Run、Step、Operation 和归属实体读取事实；没有新增 ORM、迁移或依赖。HTTP/鉴权/最终 UI 验收由主入口负责。

## 调用契约

- `service.summary(db, user_id, workspace_id=None, window='24h', model_connection_id=None) -> dto.AnalyticsSummary`
- `service.calls(db, user_id, workspace_id=None, window='24h', model_connection_id=None, limit=50, cursor=None) -> dto.AnalyticsCalls`
- `service.hardware() -> dto.HardwareSnapshot`

`window` 仅 24h/7d/30d；明细 limit 1–100。服务不会 commit/rollback，也不会改变 Session 事务隔离级别；no_autoflush 防止只读查询隐式发布调用方待处理写入。异步 HTTP 入口应在线程调用这些同步服务（hardware 的已有 nvidia-smi 读取最多等待两秒），不在事件循环阻塞数据库或子进程。

用户范围由 Run.user_id + Workspace.user_id 共同约束；显式 workspace 复用既有归属/未归档检查，显式 model_connection_id 复用模型归属检查，错误参数返回404。全用户历史含其拥有的已归档工作区记录；显式选已归档工作区遵循现有服务拒绝访问约定。模型详情只连接当前用户拥有的配置。UsageRecord 必须同时匹配 attempt/run/user/workspace/model，异常归属记录不参与计量，不通过它暴露他人数据。

明细游标是版本化 base64 JSON，不是登录授权。携带用户+过滤条件摘要、固定窗口上界与 `(created_at,id)`，每次仍重新校验归属并在 SQL 应用过滤。游标有效期24小时，切换窗口/工作区/模型须回第一页。按 Attempt 创建时间和 ID 倒序定位，不使用 offset；网络重取不会生成新调用。游标固定请求集合的时间上界，状态和已晚到计量仍是读取时的真实值，不宣称冻结全库快照。汇总数值与时序由同一 SQL 分组集合计算，Run/Operation 状态另一次读取，未声称所有类别跨查询原子快照。

## 精确计量口径

分母是 `Step.kind='model'` 的实际 Attempt，包括 created/running/succeeded/failed/cancelled 和其他真实状态；不依赖 UsageRecord 存在，不把重试合并。以 Attempt.created_at 落在 `[start_at,end_at)` 的请求为窗口队列。Run 和 Operation 按各自 created_at 独立计数；因此较早 Run 的新尝试可能不对应本窗口新 Run。没有 Operation 完成时间字段，不虚构工具时延。

`usage_records` 仅表示匹配记录存在，可能还是 started/未知用量。`known_usage_attempts` 表示五个标准 token 字段中至少一个已知，不等于全部用量已知，也不直接复用执行器中只说明输入可知的 `usage_known` 标记。各 TokenMetric 独立给出：

- `known_tokens`：该字段已观测非负整数之和；没有样本为 null，实际已报告零的样本保留0。
- `known_attempts` / `eligible_attempts`：此字段可知样本数 / 全部模型尝试数。
- `coverage_ratio`：上述两者之比；无请求时 null，有请求但全未知时0。

接收标准化字段，不对 raw 提供方对象再猜口径。整数范围0..2^63−1，时间范围0..2^53−1 ms；错误类型、布尔、负数、非整数 token、超范围字段均未知，不转成0。普通输入已包括标准化的缓存子项，不将 input+cache 再加；reasoning 若已含在 output 内也不再次相加。

缓存共同观测集合要求 input/read 都已知且 read<=input。`observed_input_tokens`、`observed_read_tokens` 是该集合之和；无集合为 null。`token_hit_ratio=sum(read)/sum(input)`，分母0为null，禁止无权平均逐请求比例。`request_hit_ratio=read>0 的请求数/共同可知请求数`，共同已知而全部 read=0 时为0。`coverage_ratio=共同可知请求数/所有尝试数`；`inconsistent_attempts` 给出 read>input 的真实异常数，这些不参与命中计算。缓存写独立统计，不算命中。

`known_input_token_coverage_ratio=共同集合输入量/所有已知输入量`，只描述已知 token 的覆盖，不代表输入未知请求的真实总量。没有足够分母时null。

`first_visible_ms` 直接来自实际运行时单调时钟的首段可见文本时间，不冒充首协议事件或引擎TTFT。P50/P95 采用 PostgreSQL percentile_cont 连续线性插值，含 sample_count 和覆盖率；一个样本也如实展示该样本分位数，界面须结合样本数判断，不生成平滑值。

每个请求输出速率 `output_tokens_total * 1000 / duration_ms`，要求 output已知且duration>0；实际output=0时速率0。该时钟覆盖请求、推理、流处理及持久化阶段开销，不是纯解码速度，且可能包含隐藏推理。summary 以逐请求速率做分位数，`weighted_tokens_per_second=sum(output)*1000/sum(duration)` 则使用同一可计算集合，独立标记，不能称其为无权请求平均。duration=0不参与速率。

24h 按 UTC 小时，7d/30d 按 UTC 日。只返回有实际模型尝试的桶，不补空桶、不造趋势；边缘桶保留完整桶边界但只纳入窗口内请求。没有请求时 series/items 为空，attempts=0，所有 sum/比例/分位数null。

当前没有价格修订/计费类别/提供方账单实体，因此 cost.status=unknown，amount/currency=null。没有把本地推理“无API账单”推断成总成本0，也不安装新计费系统。

明细只返回追溯ID、当前连接标签、状态、白名单指标、固定诊断文字与错误code；不选择或返回请求正文、消息、密钥、provider raw、opaque协议。连接名/模型标识为当前配置标签，不重建不存在的历史版本。

## 硬件语义

仅使用既有系统工具与stdlib，不安装 psutil。CPU 的真实1/5/15分钟load不是利用率百分比，未测时间差时 utilization_percent=null。内存直接读取 `/proc/meminfo`，used=MemTotal−MemAvailable，明确内核可回收语义。磁盘是数据目录所在文件系统容量，不是项目文件大小；未创建目录时读取最近存在祖先的实际文件系统，不创建状态。

GPU 使用已有 nvidia-smi 的固定 CSV 查询，超时2秒；失败/缺少设备为 unavailable，单项N/A为null。GB10标记unified且system_memory_overlap=true；其他设备内存语义保持unknown。禁止把统一GPU内存和RAM相加，也不汇总RSS当真实UMA总占用。输出 scope=visible_to_server，不虚称容器配额或全物理主机独占资源。

## 本阶段辅助证据

2026-10-17 在真实独立验收库 `SET TRANSACTION READ ONLY` 执行三个窗口汇总和明细：当时真实1 Run、0模型Attempt/UsageRecord，各窗口0请求、空series/items、token/覆盖率/分位数null、费用unknown。未创建样本。这份历史记录只证明空数据与未知值处理，不改写成后续非空数据的证据。

真实硬件读到 Linux/aarch64、20CPU、NVIDIA GB10，系统内存约130.66GB；GPU返回实际利用率/温度，内存计数器N/A保持null且UMA重叠明确。读数随采样变化，不存为静态展示数据。模块ruff辅助检查已通过。

2026-10-18 主 agent 在真实产品看板查看实际模型用量（本机 `.local/installation-acceptance/pending-and-preservation-ui-evidence.json`），随后启用 SGLang 缓存扩展并取得非空缓存样本：known attempts 大于0，显示比率按真实 cache-read/input token 计算，旧缺少计量字段的记录仍为 unknown（`memory-forget-cache-ui-evidence.json`）。这些证明所选非空数据及缺失值路径，不等于所有筛选/分页/分位数、外部账单或通用性能基准已验收。
