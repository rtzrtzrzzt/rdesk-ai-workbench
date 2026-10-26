# AI 设置发现与配置接口

详细操作手册见[AI工作台说明书](../ai-guide.md)。任何工作区的内置AI均可通过`discover_tools(names=["read_workbench_guide"])`加载后分段读取，不要求工作区包含工作台源码；工具目录和设置目录也返回该说明入口。本文接口以实时schema为准。

1.0.0 当前实现（2026-10-26）：设置发现、读取、预览、受控保存与回执均已接通，跨对话范围、说明入口及接口易用性增量已完成本机真实验收；数据库当前为039。19类设置及85个工具的具体覆盖/限制见[状态第22节](../implementation-status.md#22-2026-10-26-跨对话读取与-ai-操作说明)。下段2026-10-19为原阶段证据。

2026-10-19 增量实现，037/038已应用。真实浏览器已验证账户同步及AI保存、固定本地Qwen自动命名与手改优先、本机FLUX等待动效及安全预览替换。证据与限制见[实现状态第17节](../implementation-status.md#17-2026-10-19-工具补齐设置接口与交互改进)。

## 发现、读取与保存

模型初始工具包含 `discover_tools`。需要配置工作台时，先通过它加载 `configuration` 模块，再依次使用：

| 工具 | 返回与用途 |
| --- | --- |
| `discover_settings` | 按用途搜索全部设置分类；返回作用域、来源、生效时机、可用动作和专用操作方法；可请求完整schema |
| `read_settings` | 当前值、版本、继承信息及保存schema；集合设置可以指定真实 `resource_id` 读取单项 |
| `preview_settings` | 对候选参数和当前版本做只读校验，返回修改前后值；不会连接提供方、安装组件或启动服务 |
| `configure_settings` | 经过当前操作审核，调用已有模块的版本校验与保存服务，返回持久回执 |
| `settings_receipt` | 按返回的 `request_id` 查询提交状态，供断线或结果未知时核对 |

发现目录是说明及合同，不保证所有已配置能力当前健康。工具目录也会区分可用、已配置、条件待检查和不可用，并给出进一步检查入口；不能把“能发现工具”当作“已经执行成功”。

命令并发属于整机应用配置。`read_settings(section="application")`公开`command_max_active_per_run`和`command_max_active_total`的有效值；用`list_commands`可读取实时占位和剩余名额。默认2/4，可在启动环境或私有启动配置的`environment`中分别设置`WORKBENCH_COMMAND_MAX_ACTIVE_PER_RUN`、`WORKBENCH_COMMAND_MAX_ACTIVE_TOTAL`（字符串整数1..32，每任务不超过整机），统一应用于API和Worker，重启应用后生效。在线用户偏好不会覆盖整机限制；模型推理并发仍保持独立限制。

每次修改以最新 `read_settings` 返回的合同为准。`section` 指定分类，`scope` 选择目录支持的范围；省略时采用该分类的第一个范围。`action` 默认为 `save`，具体数据放在 `values`。不要把读取结果整份回写：展示字段、继承字段和隐藏命令摘要不属于保存参数。

例如主题配置读取返回当前版本后，预览和保存使用：

```json
{
  "section": "experience",
  "scope": "user",
  "action": "save",
  "values": {
    "expected_version": 0,
    "theme": "dark",
    "input_mode": "queue"
  }
}
```

示例中的版本仅展示结构，实际调用必须使用刚读到的版本。保存保留所有未打算改变的字段。AI工具使用Operation ID作为提交请求标识；HTTP调用者必须提供自己的UUID `request_id`。同标识重放返回原回执，不再次执行模块操作；参数不同则拒绝。`applied` 才表示已保存，`rejected` 表示提交前已拒绝，`outcome_unknown` 要先读回执并核对实际配置，不能改用新请求标识盲目重发。

## 分类及边界

| section | 范围 | 在线动作与生效时机 |
| --- | --- | --- |
| `experience` | 用户 | 主题和默认队列/引导；浏览器同步生效 |
| `conversation_history` | 用户 | 只读读取其它对话范围和版本；用户在通用设置控制disabled/workspace/user，AI不能自行扩大权限，子Agent限任务树 |
| `runtime` | 用户 | 任务默认模型、思考、审核、命令预算、上下文、子任务与沙箱；新任务生效 |
| `workspace_runtime` | 工作区 | 每字段覆盖和继承；`options` 中 `null` 表示继承，新任务生效 |
| `models` | 用户 | 创建/更新连接、行为预设和能力声明；不自动加载或重启模型 |
| `search` | 用户/工作区 | 保存连接、引擎和搜索默认参数；下次调用生效，保存时沿用真实引擎目录检查 |
| `mcp` | 工作区 | 保存连接、显式发现；权限撤销实时生效，调用仍绑定发现快照 |
| `computer` | 用户 | 电脑总开关；关闭即时撤销会话与租约，开启不恢复旧会话 |
| `automation_policy` | 工作区 | 浏览器/桌面范围和有效期；不能替代实际操作审核 |
| `extensions` | 用户/工作区 | 安装、更新、启停、移除、导入MCP候选；新任务冻结版本，停用实时撤权 |
| `media` | 用户/工作区 | 保存生成连接、默认模型和参数；新生成任务生效 |
| `global_rules` | 用户 | 版本化全局AGENTS正文；仅在用户明确要求时修改，下次模型调用采用 |
| `developer_tools` | 用户/工作区 | 保存受信LSP命令与初始化配置；工作区整项覆盖用户设置，下次工具调用采用 |
| `file_processing` | 应用/任务 | 读取真实处理器能力和作业合同；通过 `process_file` 提交，组件准备使用原有本机流程 |
| `credentials` | 用户 | 提供保险库与连接凭据专用入口；普通AI配置工具不读取、提交或清除密钥正文 |
| `maintenance` | 应用 | 提供备份、恢复、清理等专用维护方法；不通过普通保存执行 |
| `application` | 应用 | 只读白名单中的实际启动参数；通过原启动配置调整，需重启生效 |
| `launcher` | 应用 | 提供本机启动配置方法；不读取或导出可能含口令的私有profile |
| `model_runtime` | 应用 | 提供本地模型加载与资源保护方法；在线配置接口不重载模型 |

专用边界会在目录中标记 `operator_only`，没有普通保存动作；`conversation_history`同样只能由用户改变范围。完整目录覆盖这些设置的发现与操作方法，不代表可绕过维护条件在线改动一切。

配置写入仅限主任务；子任务可按继承权限读取。保存仍遵守用户原有授权，不允许借配置更换本轮固定主模型、启动服务或修改数据位置。本机验收继续使用 `Qwen3.8-Flash-Next-NVFP4`、131072上下文、MTP及单推理许可。

接口沿用现有凭据存储，仅显示引用和已配置状态。MCP参数与LSP命令参数可能包含历史内联凭据，读取和预览隐藏其正文；编辑其他字段时省略这些参数会保留原值。新提交也拒绝已知凭据键和内联秘密参数；如需调整密钥，使用专用用户入口。正常可信命令仍须经过配置及实际执行审核，隐藏展示不构成执行授权。

LSP保存合同现在显式描述`language`、`command`、`settings`、`initialization_options`。已有同language条目可省略command保留隐藏原值；新条目必须提供实际受信命令，不能以null或展示摘要代替。该配置当前没有独立GUI表单，使用配置工具/HTTP接口。保险库发现入口为`/api/v1/vault`，状态为`/api/v1/vault/status`；AI普通配置仍不接受秘密正文。

## HTTP与离线入口

HTTP沿用当前认证、CSRF及工作区归属检查：

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/v1/settings/catalog?q=…&schemas=true` | 设置目录 |
| `POST /api/v1/workspaces/{id}/settings/read` | 读取 |
| `POST /api/v1/workspaces/{id}/settings/preview` | 只读预览 |
| `POST /api/v1/workspaces/{id}/settings/apply` | 保存，正文额外携带 `request_id` |
| `GET /api/v1/settings/requests/{request_id}` | 原提交回执 |
| `GET /api/v1/settings/experience` | 当前账户主题和输入偏好 |
| `POST /api/v1/settings/experience` | 原界面保存偏好，带版本及请求标识 |
| `GET /api/v1/settings/conversation-history` | 当前账户的其它对话读取范围 |
| `POST /api/v1/settings/conversation-history` | 用户界面保存范围，带expected_version/request_id；不是AI自行授予权限的入口 |

服务停止时，可在已准备的项目环境离线读取目录和schema，不加载私有profile、不连接数据库：

```sh
.venv/bin/python -m workbench.entrypoints.cli settings-catalog --query runtime --schemas
```

跨异步检查或独立事务发布时，配置适配器在实际保存前重新检查任务取消和Worker所有权。外部准备、文件发布和回执不是跨系统原子事务；保存后清理失败仍可能返回结果未知，须按实际配置核对。

## 账户同步与迁移

主题与默认队列/引导存入 `configuration_states`，不再仅由当前浏览器决定。已登录浏览器约每4秒同步一次；主题可即时应用，模式在当前发送、引导或未确认提交结束后采用。更改只在保存成功后成为账户值。

首次迁移仅在账户尚未初始化时导入该浏览器已有偏好；已有账户值优先。发生并发版本冲突时重新读取，只保留用户当前编辑的字段。未知保存使用原请求ID核对，不用新请求重复提交。037增加自动命名状态，038增加上述配置表；本轮获用户授权后已完成完整数据库备份、验证及迁移，真实ORM对照无差异。其他实例部署仍须遵循原升级流程。

## 自动命名与生成等待反馈

新建普通对话先采用首条输入的短标题，首个成功主任务完成后再由同一固定本地模型生成简洁名称。命名是低优先级、无工具的维护任务，关闭显式思考，共用原推理许可；不会为命名加载小模型或调用云端。手动标题、已有对话、分支和子任务标题保持其原有优先级。生成失败保留短标题，有界尝试，不能阻塞聊天。

图像生成卡片显示明显等待动画、真实阶段、耗时及可用的实际采样步数；支持系统减少动效设置。排队、资源等待、采样和收尾分别表达，旧阶段100%不沿用到新阶段。完成的安全预览真正加载后才替换占位；取消、失败和未知提交停止循环动画。不显示虚构百分比，也不提供本轮已取消需求的逐步成图预览。
