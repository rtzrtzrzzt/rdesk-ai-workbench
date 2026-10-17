# A2UI 有限适配的官方来源

核对时间：2026-10-17。通过官方 GitHub commits/main API 只读取得固定修订 **45633b97636daf89bafb43fa7db23e3cd916461d**，随后读取该修订的 schema 与规范正文。未安装 renderer/SDK、未获取任何业务数据到外部、未引入商业 runtime；本模块依据公开协议独立实现受限映射。

| 主来源 | 已用于确认的事实 |
| --- | --- |
| [当前生产协议页](https://a2ui.org/specification/v0.9.1-a2ui/) | v0.9.1 为 current production，v0.9 是 previous stable；不是 v1.0 Candidate。 |
| [固定修订 server_to_client.json](https://github.com/a2ui-project/a2ui/blob/45633b97636daf89bafb43fa7db23e3cd916461d/specification/v0_9_1/json/server_to_client.json) | 四种互斥信封、version 枚举 v0.9/v0.9.1、所需字段、additionalProperties=false、root ID 及 create 前置条件。 |
| [消息列表](https://github.com/a2ui-project/a2ui/blob/45633b97636daf89bafb43fa7db23e3cd916461d/specification/v0_9_1/json/server_to_client_list.json)、[官方 wrapper](https://github.com/a2ui-project/a2ui/blob/45633b97636daf89bafb43fa7db23e3cd916461d/specification/v0_9_1/json/server_to_client_list_wrapper.json) | 一次接受 list 或严格 messages 对象；不从非标准包装猜测业务内容。 |
| [固定修订 common_types.json](https://github.com/a2ui-project/a2ui/blob/45633b97636daf89bafb43fa7db23e3cd916461d/specification/v0_9_1/json/common_types.json) | 扁平 ComponentCommon、静态 ChildList 与模板区别、DataBinding/FunctionCall/Action 不同；本子集拒绝函数、模板、动作和其他未映射字段。 |
| [固定修订 Basic Catalog](https://github.com/a2ui-project/a2ui/blob/45633b97636daf89bafb43fa7db23e3cd916461d/specification/v0_9_1/catalogs/basic/catalog.json) | Text.text 与 variant、Column/Row/List.children、List.direction、Card.child 的准确字段；Table、Chart、FileLink 不属于该 Basic Catalog。 |
| [固定修订协议正文](https://github.com/a2ui-project/a2ui/blob/45633b97636daf89bafb43fa7db23e3cd916461d/specification/v0_9_1/docs/a2ui_protocol.md) | 按 ID 更新组件、先引用后定义、路径 upsert/replace、缺 value 表示删除、`/` 表示整个数据模型、数组 undefined 洞，以及 create/delete 生命周期。 |
| [v0.9→v0.9.1 evolution guide](https://github.com/a2ui-project/a2ui/blob/45633b97636daf89bafb43fa7db23e3cd916461d/specification/v0_9_1/docs/evolution_guide.md) | v0.9.1 仅要求当前活动 surface ID 唯一，delete 后显式 create 可复用 ID；MIME 为 application/a2ui+json；新版 schema 允许两种 version 标签。 |
| [上游许可证](https://github.com/a2ui-project/a2ui/blob/45633b97636daf89bafb43fa7db23e3cd916461d/LICENSE) | Apache-2.0；本次没有复制官方运行时或引入其依赖包。 |

## 已观察到的主源标识差异

v0_9_1 目录内的 schema `$id`、Basic Catalog 的 `catalogId` 仍使用 `/specification/v0_9/…`；同修订 v0.9.1 协议正文示例则使用 `/specification/v0_9_1/catalogs/basic/catalog.json`。因此适配器仅明确允许这两个逐字已见的 Basic ID，不尝试自动推断任意版本路径、不网络解析 catalogId；输出保留原始输入 ID 供审阅。此差异没有被改写成“官方 schema 全已更新到 v0_9_1”。

本项目 `workbench-core/1` 是**自有目录 ID**，其中 items List、Table、Chart、FileLink 字段沿用既有可信界面能力的受限映射，不能称作官方 Basic 标准组件。标准 Basic List 的 children 布局另行映射为 Row/Column。具体范围与不支持项见 [a2ui_README.md](a2ui_README.md)。

该来源核对不是完整 renderer 一致性认证。宿主现有来源核验/发布、权限和业务版本仍是最终事实；后端没有真实 JSON 来源时，本模块不制造业务行或数值来“补齐”规范示例。
