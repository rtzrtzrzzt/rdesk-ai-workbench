# 工作台前端

应用版本 0.1.0。React / TypeScript / Vite，依赖由 npm 与 package-lock.json 锁定。前端开发遵循 package.json 的 Node 范围 `^20.19.0 || >=22.12.0`；源码启动器的显式构建要求见[原生启动说明](../../runtime/installation/README.md)。已带 dist 的日常运行不需要 Node。

```sh
npm ci
npm run dev
npm run build
npm run format
npm run format:check
```

开发入口为 `http://127.0.0.1:5173`，`/api` 代理到 `http://127.0.0.1:8765`。发布使用 `dist/` 静态产物，并由同源服务处理 `/api/v1`。前端不直接访问模型端口。

API 类型、请求与 SSE 适配集中在 `src/api/`。后端导出的 `openapi.json` 是 DTO 的来源；更新后运行 `npm run api:generate`，生成 `src/api/schema.d.ts`。手写 `types.ts` 只提供命名别名，HTTP/SSE 客户端使用生成类型。生成文件不由 Prettier 修改。认证使用 HttpOnly 会话 Cookie，写请求携带服务器返回的 CSRF token，凭据不写入浏览器持久存储。请求绑定认证代次，session 查询接受 AbortSignal，旧会话响应不能改写新会话 CSRF。外观与选中的对象 ID 保存在当前浏览器；消息草稿仅保留于当前页面内存。

当前接入初始化/登录、工作区、会话、模型连接、任务、问题/授权、记忆与规则、文件上传/提取、搜索连接/网页读取、MCP、凭据保险库、浏览器/桌面自动化、备份维护、数据看板及系统诊断。记忆编辑使用预期版本，冲突时保留本地草稿；忘记操作不会删除原始对话或备份。文件上传与内容处理分开，处理器和模型原生能力分开呈现。搜索连接分别保存用户级与工作区级版本，仅显式查询或读取时访问网络，网页正文按纯文本显示。可选能力按实际组件、平台和授权状态显示，入口接通不等于当前能力可用或已通过真实验收。后台任务列表显示所选会话的真实任务。

日常界面以对话、文件、记忆为主，其余入口按需展开。一个输入框提供“补充当前任务”和“下一条消息”，两份草稿独立；设置分组在收起和切页时保留已挂载草稿。未确认发送有独立核对条，不会随普通通知关闭而消失。退出账号会清理当前页面的任务、草稿、待确认操作和流状态。

未显式选模型时，创建任务不提交 `model_connection_id` 覆盖，由后端继承工作区或用户默认，并保存 `model_selection_source`。预检使用的 `expected_model={connection_id,version}` 随请求提交，默认连接或版本变化会要求重新核对，不能将已确认附件发送到另一个目标；不可用模型不会自动换成列表第一条。`useRunStream` 从 `/runs/{id}/conversation-snapshot` 取得同一 REPEATABLE READ 事务中的消息、任务与游标后接续事件，终态再读权威正文；旧订阅结果由 dispose/AbortSignal 隔离。

网络失败后保留原请求 ID 并先查回执。MCP、Vault 和自动化面板还提供关闭待确认请求的入口：`POST /requests/{id}/close` 与受理写入共用幂等锁；在途返回 409，已有有效回执或自动化会话返回 `recorded` 并继续核对，无已受理记录时持久封存。只有 `sealed` 后才解除对应本地待确认状态，迟到同 ID 写入返回 410。已有过期记录也可返回 `sealed`；它不证明过去的副作用未发生，也不取消或撤销既有效果。单独的 GET 404 不是未执行证明，秘密正文不为恢复而写入浏览器存储。

文件任务由服务端列表发现，并合并当前浏览器已确认的任务 ID；新浏览器也能查看服务端现有任务。上传恢复仅在当前浏览器保存工作区 ID、上传/任务 ID、文件元信息及待确认请求标识，不保存文件字节。刷新后需要重新选择原文件才能继续上传。记忆正文与编辑草稿不会写入浏览器持久存储。

数据看板仅展示真实模型尝试、已知计量覆盖和服务端硬件快照。缓存读取按共同已知 token 口径加权，缓存写入单列；首段可见延迟与请求平均输出速率附样本数，未知费用不归零。UMA 的系统/GPU 内存分别展示，不相加。ECharts 以按需模块懒加载，仅对真实时间桶画图，空数据不造图。

`npm run build` 验证类型与生产构建。2026-10-18 主 agent 已在实际 Chrome/原生服务完成以下真实流程，记录位于本机 `.local/installation-acceptance/`，不随源码包分发：

- `simplified-ui-evidence.json`：单输入框、两份独立草稿、设置折叠/切页保留、Escape 焦点返回、390px 无横向溢出。
- `audit-chat-ui-evidence.json`：工作区默认继承来源正确、无显式模型覆盖且带连接/版本预检；断网后沿原 ID 核对、补充输入进入下一次实际 attempt、刷新正文一致。
- `generated-memory-ui-evidence.json`：两种可信界面使用真实会话 JSON 的 4 行数据，FileLink 下载与原件字节一致；实际 AG-UI CUSTOM 终态与唯一事件 ID；模型记忆候选经批准写入 Markdown。
- `pending-and-preservation-ui-evidence.json`：Vault 断网后刷新、封存并拒绝迟到同 ID，保险库仍未初始化；清理后 ASR 107B 结果和原记忆正文保留，页面展示真实用量。
- `memory-forget-cache-ui-evidence.json`：真实模型读取候选记忆及两层历史引用，等待问题期间从 UI 忘记，回答后任务继续完成；实际持久片段中的直接/嵌套引用被过滤，新历史没有旧依赖。看板实际启用 SGLang 缓存扩展，known attempts 大于0，显示真实读取/输入 token 比率，旧缺字段记录仍为 unknown。

这些记录不代表全部验收场景通过。X11 已在新建自有 Chrome 的进程级英文输入环境验收实际 ASCII 写入、人工接管及撤权；原 Chrome/libpinyin 环境仍有吞键问题，中文和其他应用不能据此视为通过。界面提示执行后重新观察，不自动重发。Windows/macOS 无实机验证。OCR 缺可用安全更新组件，自由 HTML/JS 执行不可用，电脑截图禁止传给远程模型。外部 provider 的真实调用与完整 A2UI/AG-UI 协议兼容仍有各自验证边界；所选记忆引用与缓存样本通过也不等于任意历史格式或性能基准全部通过。
