# 有界 A2UI 快照适配器

`a2ui.py` 是无 I/O 的声明式适配器，固定依据 A2UI **v0.9.1 当前生产规范**及官方修订 `45633b97636daf89bafb43fa7db23e3cd916461d`。它将一次提交的有序消息快照映射为既有 `workbench-ui/1` 草稿；不是完整官方流 renderer，不接管 LangGraph、Question、权限或持久化。没有新增依赖。

## 公共接口与主入口接线

```python
from workbench.modules.generated.a2ui import A2UIInput, A2UIResult, adapt

command = A2UIInput(
    messages=ordered_messages,
    surface_id=selected_surface_id,
    title=display_title,
    sources=artifact_references,
    bindings=artifact_json_bindings,
)
result: A2UIResult = adapt(command)
```

`messages` 接受实际 JSON 对象：一个信封、信封列表，或官方严格 `{ "messages": [...] }` wrapper。不接受 JSON 字符串、JSONL 文本、任意包装键或客户端函数。一次仅接受 `surface_id` 指定的一个 surface；不从多 surface 消息中悄悄丢弃其他内容。

输入 `sources` 使用现有 `ArtifactReference`，即 `artifact_id` 与 `expected_version`。`bindings` 与既有 `generated.service.bind_data` 相同：Table 为 `artifact_id`、可选 `pointer`、与 columns 一一对应的 `fields` 指针数组；Chart 为 `artifact_id`、可选 `pointer`、`label_pointer`、`value_pointer`。只有 Table/Chart 接受数据绑定。源 artifact ID 不重复，绑定不得指向未列明来源或未定义组件。

输出保留 `adapter_version`、规范版本/固定修订、输入 `wire_version`、`surface_id`、原始 `catalog_id`、目标目录，以及 `revision`、`generation`、`message_count`。**revision 是本次完整有序快照内的本 surface 消息计数；generation 是显式 create 的计数。两者是适配器局部重放元信息，不是官方 wire 字段、全局版本或数据库 CAS。** 主入口必须持久化实际消息、来源、结果元信息，绑定 Run/Operation，独立进行版本与归属校验；模块不持有可恢复内存会话。

三种状态：

- `ready`：结构与受限映射完整，`draft` 为现有 `SurfaceDraft`；`source_validation='host_required'`。主入口仍须将草稿交给现有 `generated.service.create`，执行当前权限、来源版本、传输授权、JSON 内容绑定及受管 artifact 发布。适配器从未读取 artifact，ready 不表示数据来源已核验或已发布。
- `incomplete`：缺 root、子节点、尚未接入 root 的节点、所需文本数据路径或表图来源绑定。返回 diagnostics 和缺失组件 ID，`draft=None`，不能发布占位表格或补造数据。
- `deleted`：快照以合法 delete 结束，`draft=None`。只报告状态，不隐式删除既有业务 artifact，也不恢复历史 UI。

非法/不受支持的输入抛项目 `AppError`，稳定 code 前缀 `a2ui_`、HTTP 422；错误说明不回显消息正文。Pydantic DTO 输入错误仍遵循宿主已有校验映射。本模块不新增 HTTP/工具入口；由主入口注册 `present_a2ui` 并仅发布 ready 草稿。

## 信封与生命周期

每条严格为 `{version, 一个消息类型}`，版本只接受固定官方 v0.9.1 schema 声明的 `v0.9` 或 `v0.9.1`；同次快照不混用。处理语义统一依固定 v0.9.1 规范，不宣称复刻所有历史 v0.9 renderer 行为。

| 消息 | 接受字段与行为 |
| --- | --- |
| createSurface | 必需 surfaceId、catalogId；可省略 sendDataModel 或显式 false。theme、true 的 sendDataModel 均明确拒绝。创建前无活动 surface；重复创建活动 surface 失败。 |
| updateComponents | 必需 surfaceId、非空 components。按 ID 新增或整体替换该组件定义；不删除其他定义，不合并旧 props。单批 ID 重复拒绝；跨批更新同 ID 合法。 |
| updateDataModel | 必需 surfaceId；path 缺省或 `/` 为整个模型。value 存在时替换/创建；省略 value 才是删除，null 是实际值。支持对象键 upsert/删除、JSON Pointer `~0/~1` 转义；不会把 `/` 当空键。 |
| deleteSurface | 必需 surfaceId；清除本次重放的组件/数据。其后 update/delete 失败，只有显式 create 才能建立新代数。 |

更新/删除前必须在本次历史中有对应 create。没有持久历史时不能跳过 create 并凭内存猜状态。不同 surface、未知目录/版本/字段均拒绝。删除后重新 create 复用 ID 是 v0.9.1 明确允许的行为；重建后旧组件和数据全部失效。

组件树以官方要求的 ID `root` 为根；引用可以先于定义出现。尚未定义的引用/未接入节点保持 incomplete，循环、多父、反向引用 root、重复子 ID、超限明确拒绝。完整树必须所有节点从 root 可达。不从失效引用推断默认内容。

dataModel 可包含普通有界 JSON；本子集只把它用于 Text 与自有 List 的只读文字绑定。Text 可用字符串或严格 `{path: "/..."}`，List.items 可用字符串数组或同样的对象路径绑定。绑定值类型不符明确拒绝，缺值为 incomplete；不做字符串/数字强制转换。数组索引路径读写、删除造成 undefined 洞、children 模板、函数、表达式、双向输入和回调均不支持；不会将 undefined 偷换为 null。

## 固定目录子集

仅三个精确 ID 在白名单内，catalogId 从来不是下载地址：

- 官方 schema 声明：`https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json`。
- 官方 v0.9.1 文档示例：`https://a2ui.org/specification/v0_9_1/catalogs/basic/catalog.json`。
- 本项目受限自有目录：`workbench-core/1`。

前两个是已在官方主源中逐字出现的 ID，而不是按 URL 模式猜测别名；实际来源差异见 [a2ui_sources.md](a2ui_sources.md)。不接受任意目录或覆盖目录定义。

组件用官方扁平形状 `{id, component, ...字段}`，不接受原生 workbench 文档的 `type/props` 冒充 A2UI：

| component | 官方 Basic 子集 | 自有 workbench-core/1 子集 |
| --- | --- | --- |
| Text | text 字符串/受限对象路径；variant 缺省或 body；映射纯文本 | 相同；不是 HTML/Markdown 执行器 |
| Column / Row | 静态 children ID 数组 | 相同 |
| Card | 单个 child ID | 相同 |
| List | 静态 children；direction 缺省/vertical 映射 Column，horizontal 映射 Row。保留子组件 ID，不把它误当 items 列表 | items 字符串数组/受限对象路径，映射已有只读文本 List；这是本项目目录定义 |
| Table | 不支持；不是官方 Basic 组件 | columns；rows 省略或空数组。必须提供独立真实来源绑定，交宿主填行 |
| Chart | 不支持；不是官方 Basic 组件 | kind=bar/line、x_label、y_label、unit；points 省略或空数组。必须提供独立真实来源绑定，交宿主填值 |
| FileLink | 不支持；不是官方 Basic 组件 | artifact_id、label；ID 必须在本次 sources 内，只由现有受管预览/下载入口解析 |

Basic 的 justify、align、weight、accessibility 及其他 variant/组件不在此有限映射内，提供时明确拒绝而非静默忽略。Heading、Details、官方输入控件/Action 等也未映射。Table/Chart 的业务 rows/points 不能由模型字面值或 dataModel 路径填入；空数组仅是传给宿主绑定器的未填数据槽，缺真实绑定时不会产生草稿。来源绑定最终读取的 UTF-8 JSON、记录类型、列数/数值合法性仍由既有发布服务判定；该服务可拒绝此处 ready 的草稿。

没有任意 URL、脚本、HTML、style、函数、handler、openUrl、网络抓取、工具调用、用户输入或权限升级。FileLink 不接受模型提供的下载地址。既有 workbench-ui/1 与持久 Question 表单功能保留，不因本适配器变成完整 A2UI 动作协议。

## 预算与已有辅助证据

单次 JSON 最多 1 MiB、128 条消息、12000 个 JSON 值、JSON 深度20；组件最多64、树深8、静态子 ID 最多64、ID 最多256字符、文本最多100000字符；自有 List 最多1000项、Table 最多50列、来源最多16、绑定最多64、指针最多1000字符/16层。表图实际记录数沿用宿主绑定器的1000行/2000点限制。超限拒绝，不截断然后当成完整数据。

2026-10-17 辅助检查仅针对本模块：真实 `docs/requirements.md` 标题映射至既有 check_components；单信封/wrapper、增量组件与缺路径、对象 upsert/删除、delete/recreate、wire/catalog/lifecycle 拒绝、函数/动作/模板/多父/数组路径拒绝、Basic List 布局、缺表图绑定不产生草稿及禁止 dataModel 业务行均通过。一次探针起初把缺 path 的 FunctionCall 预期为 unsupported_field，实际准确拒绝为 shape；只校正探针预期后继续剩余边界，未为通过探针修改产品拒绝逻辑。

另以验收数据中真实项目文档 artifact `7b780a23-b1bf-4bca-87cc-c286a19d0e52` 的现存原件版本核对自有 FileLink→既有 check_components、输入不变和确定性重放，通过。这次协议机制辅助检查未发布业务产物、未造表图业务值、未调用 API/模型、未读写业务数据库。

后续主入口已接入适配、真实 artifact 数据绑定与持久发布。2026-10-18 主 agent 在真实 Chrome 会话中验收两种可信界面：使用实际会话 JSON 的 4 行数据绑定表格/图表，FileLink 下载与原件字节完全一致；证据为本机 `.local/installation-acceptance/generated-memory-ui-evidence.json`。这证明所选有界路径，不代表完整官方 renderer、所有增量/删除/不完整状态或任意业务动作均已验收；自由 HTML/JS 执行仍不可用。
