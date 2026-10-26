# 扩展包、生成能力与模型预设

本文保留2026-10-19增量设计及其实现约束。1.0.0 已实现插件/Skill生命周期、思考预设、统一生成任务与独立生成页；本机FLUX及对话内生成已真实验收，外部媒体提供方仍缺真实服务/凭据验收。沿用模块化单体、PostgreSQL和唯一LangGraph agent运行时；不建立公共市场、第二套文件库或另一套agent SDK。当前操作见[AI说明书](../ai-guide.md)，具体证据及限制见[实现状态](../implementation-status.md)第16至18节，最新接口范围见[第22节](../implementation-status.md#22-2026-10-26-跨对话读取与-ai-操作说明)。

## 1. 所有权与接入边界

| 模块 | 负责 | 复用的入口 |
| --- | --- | --- |
| extensions | 插件/Skill格式、受管不可变版本、安装来源、作用域、启停、资源读取 | Run配置快照、Context清单、ToolExecutor、MCP Host |
| models | 原生思考参数适配、工作台五档及用户指令模板 | ModelRevision、ProviderConfig、现有模型输入编译 |
| media | 图像/视频/音频/3D能力连接、提交/查询/取消、媒体任务与模型驻留 | Run/Job租约、Operation效果账本、Vault、Artifact |
| 资源准入 | 本机重推理串行许可、当前可用内存、分阶段增量预算及真实回收 | 全实例model_permit、所有权令牌、本机原生进程归属 |

入口只调用应用服务；第三方Python不import进API/agent Worker。Skill和插件声明不能增加工具、路径或凭据权限。媒体Worker是普通持久任务执行者，不是第二个agent编排运行时。

## 2. 插件与Skill

支持独立SKILL.md、Agent Plugins 1.0根plugin.json、Codex兼容.codex-plugin/plugin.json；后者的私有连接器、hooks等须列为兼容性诊断，不能宣称通用支持。读取规范在本机按已支持版本实现，不在加载包时执行远程schema或代码。Portable根清单优先；宿主专用overlay仅显示兼容性诊断，不执行私有组件或默默合并两套身份。

目录、ZIP、Git都先形成候选快照，再解析和原子发布。目录不持续引用可变源；Git保存实际commit，不用分支/tag作为不可变身份。拒绝越界、符号链接、重复归一化路径及设备文件，有明确文件数/大小上限；安装不执行脚本、hooks或依赖安装器。只读资源和脚本保留原包布局。

安装记录持有归属、源、当前revision、scope及乐观锁；revision持有内容标识、格式、规范化manifest、Skill索引、MCP声明及诊断。全局启用可由工作区覆盖。实际创建Run时冻结版本（排队尚未创建Run时不冻结），子任务继承父任务集合；更新只影响新Run。停用/卸载是实时撤权，历史版本不能恢复权限，已发生效果按原账本核对。卸载不删用户源目录，历史引用的受管revision保留。

Context仅加入Skill名称、描述和稳定标识；正文/引用按需通过专用只读工具读取。数据目录仍受保护，不放开read_file访问整个私有根。脚本需要时可将固定版本导出到授权工作区，随后使用原命令执行与审批机制。包内MCP声明先转换为可审查的宿主连接候选，由用户明确配置目标与凭据后沿用现有MCP发现/执行；无法转换的字段逐项显示。

全局和项目AGENTS复用memory.load_rules，通过专用只读工具重读；不凭文件名授予任意主机路径访问。路径范围仍由工作区登记和已安装资源决定。

## 3. 思考预设与子任务

模型连接提供三种选择：模型原生、工作台五档、我的模板。五档为低/中/高/极高/Max；自定义模板允许编辑通用指令及各档指令。可通用的是指令内容，底层角色/结束/工具/图像序列化仍使用匹配模型的格式；远程API不能由宿主替换其服务器内部Jinja模板。

原生参数只由协议/引擎适配器或明确的连接声明产生，未知模型默认省略。Qwen当前三个原生值保持；工作台和自定义档位转换成系统指令，不向不支持的接口发送max。当前Qwen使用模板中性档位避免自动xhigh指令与自定义低档冲突。关闭思考是独立能力，不能把“请简短思考”冒充真正关闭。普通模板预设不会执行Jinja或Python。

主agent可在spawn_agent指定单个子任务的思考选择；显式无效值返回真实可用选项供主模型修正。省略时同模型继承，换模型使用该连接默认，不误传另一模型参数；关思考同样正确继承。每Run冻结模型版本、预设正文和选择，后续修改不改变已排队/运行请求。模型切换按各连接保留偏好，失效选择回到默认并清楚提示。

## 4. 统一媒体能力

模型面向稳定generate_image等工具；用户面向独立能力连接与默认提供方。内置适配器包括本地FLUX3、OpenAI Images及版本化workbench-media/1异步服务。视频/音频/3D使用同一任务生命周期和服务合同，具体模型/协议由适配器负责，不宣称填写任意模型名即可兼容。图像参考始终使用归属/版本明确的Artifact，不接受模型提供的任意主机路径。

独立前端提供生图、视频、音频、3D入口，未接入明确显示“没有模型接入”；用户直接生成与主模型调用共用服务。提交前冻结提供方配置和输入版本，先持久登记submitting再发请求。已确认provider_job_id后只轮询/查询/取消同一任务；提交超时且无法取得身份记outcome_unknown，禁止盲目重发和重复计费。用户关闭连接实时阻止新提交，旧远端任务仍须核对和清理。HTTP客户端无自动提交重试；远端输出地址、重定向、字节预算和本地保护网段按提供方及授权验证，凭据只在实际请求时由Vault解密。

生成文件先写受管暂存，再原子发布为Artifact，保存来源Run、provider_job_id、模型revision、seed/尺寸/实际精度、参考产物和内容版本。恢复时先核对已发布产物，不能重新生图代替丢失的回执。前端复用文件预览/下载；模型可请求实际原生图像输入重新观察，视频帧、转写和3D预览分别说明可见范围。未知/未配置的外部服务显示未验证，不以辅助协议检查冒充真实生成。

Agent生成工具的提交回执仅表示创建了后台任务，不包含尚不存在的文件ID。本批工具完成后沿既有LangGraph join节点等待，结果完成后按真实Artifact列表继续；引导和停止可中断等待，不占模型生成许可。独立生成界面仍即时返回任务，刷新后读取相同记录。主对话共用真实阶段、进度和产物卡片，不把提交成功显示成图片已完成。

历史列表按创建时间/ID稳定分页，支持最多200条一次的批删除。先原子验证所有归属，再锁定root、Run、Job和模块状态；活动任务复用原取消链，待终态且所有资源实际关闭才设置deleted_at。维护逐任务按相同锁序完成删除，避免批任务之间形成反向锁环。只移除可见历史，Artifact、主对话引用、费用/效果及幂等回执保留；旧游标仍可用于加载更早记录。已删记录不能重新核对恢复；删除中的未知远端提交可登记真实提供方ID用于查询和停止，不能恢复生成。

## 5. 本地FLUX3与统一内存

固定BFL reference bfc8889a4497f846416f253a3972a1941ac698b9，权重revision 337337621b8526e120f2275d250242f4a6bedd75。独立headless原生环境，不修改现有Qwen/SGLang环境。复用官方原生模块，避免Models构造器隐式加载额外Safety/PUP权重。独立生成页默认复用冻结的本地对话模型按官方指南准备caption（编辑实际提供安全参考图），可明确关闭；主agent同样可读取指南；Qwen3-VL-4B是必需编码器，不能以主模型替代。

内存紧张时按编码→参考编码（若有）→NVFP4采样→VAE解码分阶段独立进程，保存中间小张量，确认退出后再进入下一阶段。TE直接device_map加载，避免CPU权重副本；CPU offload不作为统一内存扩容。保持NVFP4融合路径，不静默反量化、换权重或改云。起始低分辨率只用于核实可执行性，实际尺寸/步数随产物保存。

当前Spark使用safetensors的pread后端和同步分片加载，避免统一内存上mmap文件页与CUDA权重同时抬高峰值；只在一次性阶段进程适配上游加载入口，保留原生数学。首次NVFP4内核编译在无权重的独立阶段完成，限制单编译任务，按FlashInfer/Torch/设备版本复用受管缓存；不能在权重已经驻留时再叠加大型CUDA编译峰值。

内核准备通过现有Driver API读取实际架构，显式固定本次编译目标，沿用原缓存键与模块入口；它只报告主机编译/加载成功，跳过没有GPU作业的CUDA同步，不把编译完成当作GPU推理验收。实际GPU阶段继续执行同步和峰值测量。系统MemAvailable是准入依据，不能视为CUDA一定能分配的额度；[CUDA惰性加载](https://docs.nvidia.com/cuda/archive/13.0.0/cuda-c-programming-guide/index.html#lazy-loading)仍可能在实际使用模块时申请内存，现有余量保护保持。

对话活动通过可空`media_run_id`指向真实生成子任务：只对当前页可见的`generate_*`操作，按原请求ID规则读取已存在的MediaTask/Run，并校验用户、任务类型与父Run；工具结果尚未写回也可从已提交子任务恢复关联。沿用原分支前缀与操作分页，不按时间猜配或把根任务树全部混入主对话。前端在原ToolActivity内复用媒体卡片，依据媒体自身终态与真实资源关闭决定轮询；主/子对话复用同一时间线，末尾汇总移除，停止回执同时更新单任务及生成历史缓存。

所有本地重推理共用现有全实例执行许可。取得许可后，依据实时MemAvailable和单阶段增量峰值进行原子准入；未分配承诺与已驻留占用分开，活跃阶段未确认关闭时不释放预留或启动下一阶段。主Qwen驻留内存已体现在MemAvailable，不再重复扣除权重大小。原生子进程另有allocator额度和内存余量监测；仅回收自身，保留主模型guard余量。内存不足明确等待或失败，不擅自卸载主模型。

首版媒体采用按需加载、阶段退出即卸载的驻留策略，减少共享内存竞争；暖驻留必须有本机峰值证据后再启用，不能凭权重体积承诺。Windows/macOS和其他GPU未实测时保持待验证。

## 6. 调查依据

- [Agent Skills规范](https://agentskills.io/specification)
- [Agent Plugins 1.0](https://agent-plugins.org/specification)
- [OpenAI插件格式与兼容](https://developers.openai.com/plugins/build/plugins)
- [FLUX3固定源码](https://github.com/black-forest-labs/flux-3-image-klein/tree/bfc8889a4497f846416f253a3972a1941ac698b9)
- [FLUX3权重](https://huggingface.co/black-forest-labs/flux-3-image-klein/tree/337337621b8526e120f2275d250242f4a6bedd75)
- [FlashInfer SM120/121 FP4实现](https://github.com/flashinfer-ai/flashinfer/blob/8a49f9a242695fed9173cd5b19da45c9bd316503/flashinfer/gemm/gemm_base.py#L1020)
- [NVIDIA CUDA驱动兼容](https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html)

这些是格式/源码支持依据，不能代替当前Spark的真实生成与界面验收。

接口合同见[媒体HTTP](media-http.md)，本机环境见[运行说明](../../runtime/media/README.md)。
