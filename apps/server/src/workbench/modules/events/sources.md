# AG-UI 官方主来源

核对2026-10-17。官方仓库HEAD通过只读`git ls-remote`确认为 **89cedd3fd549feeee41e50e1d7c6d373e549241a**；同修订package.json的`@ag-ui/core`版本 **1.0.3**。本模块是无依赖的工作台只读投影，不安装该SDK，不复制其运行时。

- [固定修订机器可读AG-UI 1.0 schema](https://github.com/ag-ui-protocol/ag-ui/blob/89cedd3fd549feeee41e50e1d7c6d373e549241a/docs/spec/1.0/schema.json)：以JSON字段、required、BaseEvent.metadata为准；没有自造顶层event ID。
- [官方事件概念](https://docs.ag-ui.com/concepts/events)与[固定修订说明](https://github.com/ag-ui-protocol/ag-ui/blob/89cedd3fd549feeee41e50e1d7c6d373e549241a/docs/concepts/events.mdx)：生命周期与工具参数流/结果分开，不把执行进度当参数边界。
- [固定修订Python事件导出](https://github.com/ag-ui-protocol/ag-ui/blob/89cedd3fd549feeee41e50e1d7c6d373e549241a/sdks/python/ag_ui/core/events.py)、[TypeScript包版本](https://github.com/ag-ui-protocol/ag-ui/blob/89cedd3fd549feeee41e50e1d7c6d373e549241a/sdks/typescript/packages/core/package.json)：确认当前主源而非旧版示例/第三方摘要。
- [官方仓库MIT许可证](https://github.com/ag-ui-protocol/ag-ui/blob/89cedd3fd549feeee41e50e1d7c6d373e549241a/LICENSE)。本次只依据协议字段独立实现适配，不引入CopilotKit产品或其生产接入许可前提。

内部已批准依据：`docs/technical/files-computer-ui.md`9.3与`runtime-and-data.md`9.3；实际事件来源为`modules/runs/service.py`、`models/execution.py`、`tools/service.py`和现有API SSE。来源说明不把缺失threadId/工具结果消息ID补成事实，也不声称此事件子集满足整个AG-UI运行时。
