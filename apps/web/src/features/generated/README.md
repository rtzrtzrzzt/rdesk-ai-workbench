# 受限可信表单与只读界面

这里实现的是工作台自己的 `workbench-ui/1` 快照目录 `workbench-core/1`，不是完整 A2UI renderer。SchemaForm 已通过 Cards/RunInteractions 接入持久 Question；TrustedSurface 由 RunSurfaces 接入正式 GET /runs/{id}/surfaces 与 GET /ui/protocol，在任务中显示后端不可变产物。后端另有有界 A2UI 快照适配器，转换后复用此渲染链路。已取得的真实 Chrome 验收范围见末节，没有用业务示例数据代替真实来源。

## SchemaForm

入口：`../interaction/SchemaForm.tsx`。

- `schema: Record<string, unknown>`。
- `onSubmit(value: SchemaFormValue): Promise<void>`，`SchemaFormValue = { action: 'accept' | 'decline' | 'cancel'; fields: Record<string, unknown> }`。
- `disabled?: boolean`，`acceptedValue?: SchemaFormValue`。已持久接受的记录应由宿主传回 `acceptedValue`；组件只在回调成功完成后显示“已收到”。

只接受顶层 object 与明确 properties，最多 20 字段。字段需明确 type：string/integer/number/boolean；标量 enum；或 `array` 配 `items: { type, enum }` 多选。枚举最多 100 项。支持 required、minLength/maxLength、minimum/maximum、数值 exclusiveMinimum/exclusiveMaximum、minItems/maxItems。多选输出不重复，integer 须在 JS 安全整数范围。字符串长度按 Unicode 码点计算。

未知关键字、深层对象、任意 `$ref`、组合 schema、format、readOnly:true、writeOnly:true、password，以及明显凭据字段名均停止整个表单交互并说明原因。凭据检测覆盖常见 snake/camel/大写命名和中文密码/密钥词；不会把拒绝的 schema/default 回显。宿主不能把此表单作为凭据入口。`title`、`description`、`default`、`examples` 是已知注解；仅 default 在通过同一字段校验后进入草稿，examples 不生成答案。`$schema`/`$id` 只作标识，不下载或解析引用。additionalProperties 只接受布尔值，本表单始终仅提交声明字段。

未回答 boolean 使用独立空选项，false 是实际回答。没有默认值时不自动选 enum/boolean。拒绝、取消发送空 fields，不强制填写 required。独立 SchemaForm 的未知 schema 降级视图不触发提交；宿主持久 Question 适配器 RunQuestionCard 已提供明确拒绝/取消（空 fields），不会改成普通文本收集秘密。

提交中的按钮和字段锁定。网络/非明确错误后保留原动作和字段，只能“核对本次提交”；父层必须保留同一 request_id、Question ID/version，并在持久响应后完成 Promise。明确 HTTP 4xx 可修改草稿后重新提交，由父层分配新的命令身份。不要在回调中先 resolve 再异步保存。宿主应以 Question ID/version 为组件 key，并按实际 answered/expired 状态更新 disabled/acceptedValue。组件不持久存储答案。

## TrustedSurface

入口：`TrustedSurface.tsx`，公共类型：`types.ts`。

`surface: unknown` 会重新经过运行时白名单校验。`onFileLink?: (request: FileLinkRequest) => void | Promise<void>` 由宿主提供，仅接收 artifact_id、label、可选来源 version、surface_artifact_id/run_id。宿主自行核验工作区、权限、真实版本，并转到受管文件预览或下载；模型不能提供 URL，也不能借该回调启动业务写操作。

文档字段：protocol、catalog_version、title、root_id、components、sources，以及可选 artifact_id/run_id。组件字段仅 id/type/props/children；props 与本轮任务约定一致，Text/Heading/Card/Column/Row/List/Table/Chart/Details/FileLink。无 HTML、JS、导入、任意样式、URL、事件处理器或 action 配置。

结构须唯一 ID、有效引用、单父节点、无环、所有节点从 root 可达，根计深度 1、最大 8，总节点 1–64。结构错误降级整个快照；props/未知类型错误仅降级该节点子树，保留其他分支。每节点 React 错误边界与有预算的只读 JSON 预览进一步限制局部错误影响。

数据预算：来源至多 64 个、ID 唯一、version 必须是现有 artifact 的小写 SHA-256；单文本至多 100000 字符；List 至多 1000 项；Table 1–50 列/至多 1000 行且矩形；Chart 至多 2000 个点，仅有限数值或 null。超限明确降级，不截断图表或表格的数据。JSON 降级预览有独立长度、深度和条目预算，并明确标出截断。

Table/Chart 要求 sources 非空。前端验证来源形状，**不能核实数值是否来自真实产物**；后端必须核验 artifact ID、版本、可读权限和数据血缘。来源列表按原 ID/version 展示，不把通过前端校验标为业务真实性已验证。

Chart 复用已安装的 Apache-2.0 ECharts 6.1.0，React.lazy 按需载入 Bar/Line、Grid、Tooltip 与 SVG renderer，不加载完整图表入口，不接收模型 ECharts option。tooltip 使用 richText，折线不平滑、不跨 null 连线。空点集/全部缺失不绘图，null 不当零，每张图都有可展开的可访问数据表。表格支持仅当前视图的文本筛选；Details 仅本地展开。没有业务写按钮、模型派生入口或自动提问；持久问题仍走 Question。

## A2UI 调研与可映射边界

核对日期：2026-10-17。[官方协议 v0.9.1](https://a2ui.org/specification/v0.9.1-a2ui/) 当前标记为生产版本。协议由 common_types、server_to_client 与 catalog 三类 schema 组成，含 surface 创建/组件更新/数据更新/删除，静态子 ID、绑定与函数以及动作语义。它比这里的只读快照范围大。

[官方 renderer 专项表](https://a2ui.org/reference/renderers/) 将 React v0.9.1 列为 Stable，v1.0 列为 Planned；v1.0 规范 Candidate 不能推导成 React v1.0 renderer 已稳定。[React 接入文档](https://a2ui.org/guides/client-setup/#react) 需要 `@a2ui/react` 和公共 `@a2ui/web_core` 消息处理层。此处未安装它们，也未声称已经验证它们与本项目 React 19 的实际包兼容性。

[官方 Basic Catalog 指南](https://a2ui.org/specification/v0.9.1-basic-catalog-implementation-guide/) 描述的是可扩展目录；不同目录同名组件不保证 props 相同。已实现的[后端有界适配器](../../../../server/src/workbench/modules/generated/a2ui_README.md)固定 v0.9.1 与目录 ID，将一次提交的有序消息快照转换为受控组件；主要映射如下，精确支持范围以该模块说明为准：

| A2UI 静态输入                                    | workbench-core/1     | 适配要求                                         |
| ------------------------------------------------ | -------------------- | ------------------------------------------------ |
| Text literal text                                | Text.text            | 纯文本，不解释样式或 Markdown                    |
| Column/Row 的静态 children ID 数组               | Column/Row.children  | 保留 ID，重新校验唯一、深度、可达性              |
| Card 静态单个 child                              | Card.children 的一项 | 将单 ID 正规化为数组；不搬运样式                 |
| 自有 catalog 的 Heading/Details/List/Table/Chart | 同名受限组件         | 逐字段验证；表格/图表必须补充服务端核验的来源    |
| 自有 catalog 的受管文件引用                      | FileLink             | 只保留 artifact ID，不映射 openUrl 或任意 action |

后端适配器处理有界 create/update/delete 消息序列，完成校验后交给宿主发布快照；前端不直接解释 A2UI wire 消息。只支持模块声明的受限静态组件与路径读取，表格/图表的业务值仍须从真实 artifact 绑定；FunctionCall、children template、任意主题、openUrl、业务 action 和未知目录被拒绝。**尚未实现完整 A2UI 实时流 renderer、官方动作回传或任意数据绑定**，不能将有界快照映射称为官方全协议支持。[A2UI 仓库许可证](https://github.com/a2ui-project/a2ui/blob/main/LICENSE) 为 Apache-2.0；当前仅参考公开规范，未引入商业 runtime。

## 验证与接线

TypeScript 检查 src 下所有文件；SchemaForm 与 TrustedSurface 都已进入 Vite 导入链，图表模块仍按需加载。辅助检查覆盖白名单、外引用/秘密字段拒绝、required boolean、范围校验、非法节点局部降级、无来源图表拒绝、环/深度/重复引用，以及 SSR 的纯文本转义与未选 boolean。正常 build 和这些机制检查不替代真实产品操作。

2026-10-18 主 agent 在真实 Chrome 会话中验收两种可信界面，表格/图表绑定实际会话 JSON 的 4 行数据，FileLink 下载与对应原件字节完全相同；证据为本机 `.local/installation-acceptance/generated-memory-ui-evidence.json`。持久问题与补充输入的实际提交/刷新结果另见 `audit-chat-ui-evidence.json`。这不证明任意 A2UI 生命周期、所有表单拒绝分支或完整官方 renderer 兼容；自由 HTML/JS 执行仍不可用。

正式接线 `RunSurfaces.tsx` 仅读取后端保存的界面文档，不从 Markdown 识别组件；每 4 秒读取活动任务，并在状态/seq 改变、SSE 发布/完成及重连后重新读取，终态变化也强制读取最终快照。每份产物显示 artifact_id/version，并通过同工作区受管 URL 下载 JSON 原件。来源按钮先读取该工作区 artifact 元数据，检查可选版本及界面/Run 追溯，再打开纯文本预览与受管下载；不接受模型 URL 或执行 HTML。源版本变化会明确拒绝打开替代文件。
