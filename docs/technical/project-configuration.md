# 用户目录与项目配置

用户目录继续采用系统标准位置（`application_paths().data_dir`），启动时可用 `WORKBENCH_DATA_DIR` 覆盖。本轮不创建 `~/.rdesk`、不改已有路径、不迁移用户数据。设置 → 数据与维护 → 用户目录显示服务所在机器的实际路径、全局规则与扩展位置，并复用有上限、不读文件正文、不跟随链接的分类占用观测。没有可靠的服务端桌面打开能力，因此提供复制路径，在服务所在机器上手动打开。

分类占用不是目录总量：不含外部项目原文件、模型权重、凭据及扩展包；缺失、部分扫描和未知大小分别标注。PostgreSQL 是会话及配置记录来源，数据库大小为整库观测，不保证在用户目录所在磁盘。复制目录不等于完整备份或迁移；规则、凭据、扩展及任务数据通过现有入口管理，此页不清理或迁移数据。

项目根目录的 `.rdesk/config.json` 是可携带的声明与偏好，最多 64 KiB。通过设置 → 当前工作区 → 项目配置查看状态和已校验内容，下载无凭据的空白模板后由用户自行编辑、保存；应用不会创建或覆盖项目文件。

```json
{
  "version": 1,
  "interpreter": [".venv/bin/python"],
  "commands": {"test": [".venv/bin/python", "-m", "pytest"]},
  "defaults": {"command_timeout_seconds": 120}
}
```

以上 argv 是格式示例，具体命令须由项目维护者确认。`interpreter` 和 `commands` 仅作为结构化任务上下文声明，不自动执行、不更改全局解释器。命令仍遵守现有工具权限和审核。

`defaults` 仅接受现有运行契约中的低风险字段：`command_timeout_seconds`、`checkpoint_rounds`、`model_retry_limit`、`retry_backoff_seconds`、`context_target_ratio`，数值范围与运行 DTO 一致。优先级为系统 → 账户 → 项目 → 工作区显式覆盖 → 单次任务，新任务生效。模型连接、审批、沙箱、子 Agent 权限、OS/network、provider/auth 和凭据字段不受支持，未知字段使整个文件无效。不要把秘密写入 argv。

创建任务时从同一次校验结果解析偏好与命令声明，冻结到 Run；子 Agent 继承原任务声明。编辑文件或刷新设置页只影响新任务，不会悄悄修改执行中任务的上下文或权限。外部 API 任务不加载所有者的项目声明。

规则继续使用项目 `AGENTS.md` 与用户全局 `AGENTS.md`；插件/Skill 沿用已有 Agent Plugins / Agent Skills 格式及显式安装流程，不增加另一套记忆或凭据存储。

安全读取通过固定文件名及逐级 `O_NOFOLLOW` 目录描述符，禁止 symlink、hardlink、特殊文件和越界；读取长度有上限，拒绝重复 JSON 字段、无效编码与非法类型。平台缺少安全能力时返回 `unavailable`，不会降级跟随链接。未配置为 `missing`；无效为 `invalid`；有效为 `valid`。错误不回显原始文件内容。缺失或无效配置整体不应用。

集成口：`project_configuration.service.load(root: Path)` 返回带状态的视图；`runtime_defaults(root: Path)` 返回安全偏好字典；`context_declarations(root: Path)` 返回经过校验的 argv JSON 上下文。均只读、无文件和执行副作用。
