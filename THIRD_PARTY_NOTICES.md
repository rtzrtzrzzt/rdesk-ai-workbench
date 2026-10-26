# 第三方软件与许可证说明

基础许可核对日期：2026-10-17；后续新增来源按各节记录。本文保留对应锁定版本及已安装包的实际许可元数据，1.0.0文档同步未重新进行全量上游许可审阅，也未改变第三方版本；它不是将第三方代码重新授权为 Apache-2.0 的声明。

## 项目许可证与范围

AI工作台的原创源码、脚本、配置及随附文档采用 **Apache License 2.0**，另有明确标注的内容除外。根目录 [LICENSE](LICENSE) 完整复制自 [Apache 官方文本](https://www.apache.org/licenses/LICENSE-2.0.txt)，包括官方附录；附录的示例占位符是上游原文，不是对版权主体的声明。[NOTICE](NOTICE) 保留项目说明与已核对的 Apache ECharts 上游 NOTICE。本文件不新增或推定任何个人、公司或组织为项目版权人。

第三方依赖、复制的上游材料及其版权声明仍由其各自许可证约束；本项目的 Apache-2.0 不替代这些许可证。`models/` 下所有模型、权重及其附属上游材料明确不在本项目 Apache-2.0 授权范围内。用户导入的文件、凭据和业务数据也不因导入而取得本项目许可证。

`.venv/`、`node_modules/`、`.runtime/`、`.tools/`、`.local/`、`models/` 均为本仓库忽略的本机安装或数据目录；本次未将这些第三方安装内容打入 Git。仓库中的 `runtime/` 源码、脚本和来源说明与 `.runtime/` 安装目录不同，不能混为一谈。

## Python 锁定依赖

下表版本来自根 `uv.lock`。当前 Linux/ARM64 环境已安装的包以 `importlib.metadata` 的 `License-Expression`、`License`、分类及随包许可证核对；平台条件包未安装时查询其**相同锁定版本**的官方 PyPI 元数据，表中明确标注。每个包名链接至对应版本的 PyPI 元数据，源码与维护者链接也在该元数据中。`AND`、`OR` 保留上游表达式，不能自行解释为任选最宽松许可。

“直接”指项目运行依赖；“间接”指锁文件内的传递或平台依赖；“开发”指当前 dev 依赖链。Playwright 虽列在 dev 组，启用浏览器伴随进程时也属于实际运行所需组件，不能据此从该功能的发行材料中删去其许可。

| 包（上游元数据） | 锁定版本 | 许可 | 用途/范围 |
| --- | --- | --- | --- |
| [alembic](https://pypi.org/pypi/alembic/1.20.0/json) | 1.20.0 | MIT | 直接 |
| [annotated-doc](https://pypi.org/pypi/annotated-doc/0.0.5/json) | 0.0.5 | MIT | 间接 |
| [annotated-types](https://pypi.org/pypi/annotated-types/0.8.0/json) | 0.8.0 | MIT | 间接 |
| [anyio](https://pypi.org/pypi/anyio/4.15.1/json) | 4.15.1 | MIT | 间接 |
| [argon2-cffi](https://pypi.org/pypi/argon2-cffi/25.1.0/json) | 25.1.0 | MIT | 间接 |
| [argon2-cffi-bindings](https://pypi.org/pypi/argon2-cffi-bindings/26.1.0/json) | 26.1.0 | MIT | 间接 |
| [attrs](https://pypi.org/pypi/attrs/26.1.0/json) | 26.1.0 | MIT | 间接 |
| [certifi](https://pypi.org/pypi/certifi/2026.7.22/json) | 2026.7.22 | MPL-2.0 | 间接 |
| [cffi](https://pypi.org/pypi/cffi/2.1.1/json) | 2.1.1 | MIT-0 | 间接 |
| [charset-normalizer](https://pypi.org/pypi/charset-normalizer/3.5.2/json) | 3.5.2 | MIT | 间接 |
| [click](https://pypi.org/pypi/click/8.5.0/json) | 8.5.0 | BSD-3-Clause | 间接 |
| [colorama](https://pypi.org/pypi/colorama/0.4.6/json) | 0.4.6 | BSD（PyPI 分类；本机未安装，未细分条款） | 间接/平台；本机未安装 |
| [cryptography](https://pypi.org/pypi/cryptography/49.0.0/json) | 49.0.0 | Apache-2.0 OR BSD-3-Clause | 直接 |
| [defusedxml](https://pypi.org/pypi/defusedxml/0.7.1/json) | 0.7.1 | PSF-2.0（本地完整 LICENSE） | 直接 |
| [distro](https://pypi.org/pypi/distro/1.9.0/json) | 1.9.0 | Apache License, Version 2.0 | 间接 |
| [et-xmlfile](https://pypi.org/pypi/et-xmlfile/2.0.0/json) | 2.0.0 | MIT | 间接 |
| [fastapi](https://pypi.org/pypi/fastapi/0.143.3/json) | 0.143.3 | MIT | 直接 |
| [greenlet](https://pypi.org/pypi/greenlet/3.5.6/json) | 3.5.6 | MIT AND PSF-2.0 | 间接 |
| [h11](https://pypi.org/pypi/h11/0.16.0/json) | 0.16.0 | MIT | 间接 |
| [httpcore](https://pypi.org/pypi/httpcore/1.0.9/json) | 1.0.9 | BSD-3-Clause | 间接 |
| [httpcore2](https://pypi.org/pypi/httpcore2/2.13.1/json) | 2.13.1 | BSD-3-Clause | 间接 |
| [httpx](https://pypi.org/pypi/httpx/0.28.1/json) | 0.28.1 | BSD-3-Clause | 直接 |
| [httpx2](https://pypi.org/pypi/httpx2/2.13.1/json) | 2.13.1 | BSD-3-Clause | 间接 |
| [httpx2-jsfetch](https://pypi.org/pypi/httpx2-jsfetch/1.0/json) | 1.0 | BSD-3-Clause | 间接；本机未安装 |
| [idna](https://pypi.org/pypi/idna/3.20/json) | 3.20 | BSD-3-Clause | 间接 |
| [iniconfig](https://pypi.org/pypi/iniconfig/2.3.1/json) | 2.3.1 | MIT | 开发 |
| [jaraco-classes](https://pypi.org/pypi/jaraco-classes/3.4.0/json) | 3.4.0 | MIT | 间接 |
| [jaraco-context](https://pypi.org/pypi/jaraco-context/6.1.2/json) | 6.1.2 | MIT | 间接 |
| [jaraco-functools](https://pypi.org/pypi/jaraco-functools/4.6.0/json) | 4.6.0 | MIT | 间接 |
| [jeepney](https://pypi.org/pypi/jeepney/0.9.0/json) | 0.9.0 | MIT | 间接 |
| [jsonpatch](https://pypi.org/pypi/jsonpatch/1.35/json) | 1.35 | BSD-3-Clause | 间接 |
| [jsonpointer](https://pypi.org/pypi/jsonpointer/3.2.1/json) | 3.2.1 | BSD-3-Clause（本地完整 LICENSE.txt） | 间接 |
| [jsonschema](https://pypi.org/pypi/jsonschema/4.26.0/json) | 4.26.0 | MIT | 直接 |
| [jsonschema-specifications](https://pypi.org/pypi/jsonschema-specifications/2025.9.1/json) | 2025.9.1 | MIT | 间接 |
| [keyring](https://pypi.org/pypi/keyring/25.7.0/json) | 25.7.0 | MIT | 直接 |
| [langchain-core](https://pypi.org/pypi/langchain-core/1.6.12/json) | 1.6.12 | MIT | 间接 |
| [langchain-protocol](https://pypi.org/pypi/langchain-protocol/0.0.19/json) | 0.0.19 | MIT | 间接 |
| [langgraph](https://pypi.org/pypi/langgraph/1.2.16/json) | 1.2.16 | MIT | 直接 |
| [langgraph-checkpoint](https://pypi.org/pypi/langgraph-checkpoint/4.3.0/json) | 4.3.0 | MIT | 间接 |
| [langgraph-checkpoint-postgres](https://pypi.org/pypi/langgraph-checkpoint-postgres/3.2.0/json) | 3.2.0 | MIT | 直接 |
| [langgraph-prebuilt](https://pypi.org/pypi/langgraph-prebuilt/1.1.1/json) | 1.1.1 | MIT | 间接 |
| [langgraph-sdk](https://pypi.org/pypi/langgraph-sdk/0.4.8/json) | 0.4.8 | MIT | 间接 |
| [langsmith](https://pypi.org/pypi/langsmith/0.14.11/json) | 0.14.11 | MIT | 间接 |
| [lxml](https://pypi.org/pypi/lxml/6.1.3/json) | 6.1.3 | BSD-3-Clause | 间接 |
| [mako](https://pypi.org/pypi/mako/1.4.3/json) | 1.4.3 | MIT | 间接 |
| [markupsafe](https://pypi.org/pypi/markupsafe/3.0.4/json) | 3.0.4 | BSD-3-Clause | 间接 |
| [mcp](https://pypi.org/pypi/mcp/2.3.0/json) | 2.3.0 | MIT | 直接 |
| [mcp-types](https://pypi.org/pypi/mcp-types/2.3.0/json) | 2.3.0 | MIT | 间接 |
| [more-itertools](https://pypi.org/pypi/more-itertools/11.2.1/json) | 11.2.1 | MIT | 间接 |
| [openpyxl](https://pypi.org/pypi/openpyxl/3.1.5/json) | 3.1.5 | MIT | 直接 |
| [opentelemetry-api](https://pypi.org/pypi/opentelemetry-api/1.45.1/json) | 1.45.1 | Apache-2.0 | 间接 |
| [orjson](https://pypi.org/pypi/orjson/3.13.0/json) | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) | 间接 |
| [ormsgpack](https://pypi.org/pypi/ormsgpack/1.13.0/json) | 1.13.0 | Apache-2.0 OR MIT | 间接 |
| [packaging](https://pypi.org/pypi/packaging/26.3/json) | 26.3 | Apache-2.0 OR BSD-2-Clause | 间接 |
| [pillow](https://pypi.org/pypi/pillow/13.0.0/json) | 13.0.0 | MIT-CMU | 直接 |
| [playwright](https://pypi.org/pypi/playwright/1.64.0/json) | 1.64.0 | Apache-2.0 | 开发/可选浏览器运行 |
| [pluggy](https://pypi.org/pypi/pluggy/1.7.0/json) | 1.7.0 | MIT | 开发 |
| [psutil](https://pypi.org/pypi/psutil/7.2.2/json) | 7.2.2 | BSD-3-Clause | 直接 |
| [psycopg](https://pypi.org/pypi/psycopg/3.3.6/json) | 3.3.6 | LGPL-3.0-only | 直接 |
| [psycopg-binary](https://pypi.org/pypi/psycopg-binary/3.3.6/json) | 3.3.6 | LGPL-3.0-only | 间接 |
| [psycopg-pool](https://pypi.org/pypi/psycopg-pool/3.3.3/json) | 3.3.3 | LGPL-3.0-only | 间接 |
| [pwdlib](https://pypi.org/pypi/pwdlib/0.3.1/json) | 0.3.1 | MIT | 直接 |
| [pycparser](https://pypi.org/pypi/pycparser/3.11/json) | 3.11 | BSD-3-Clause | 间接 |
| [pydantic](https://pypi.org/pypi/pydantic/2.14.1/json) | 2.14.1 | MIT | 间接 |
| [pydantic-core](https://pypi.org/pypi/pydantic-core/2.50.1/json) | 2.50.1 | MIT | 间接 |
| [pydantic-settings](https://pypi.org/pypi/pydantic-settings/2.16.0/json) | 2.16.0 | MIT | 直接 |
| [pyee](https://pypi.org/pypi/pyee/14.0.0/json) | 14.0.0 | MIT | 开发/可选浏览器运行 |
| [pygments](https://pypi.org/pypi/pygments/2.21.0/json) | 2.21.0 | BSD-2-Clause | 开发 |
| [pyjwt](https://pypi.org/pypi/pyjwt/2.15.1/json) | 2.15.1 | MIT | 间接 |
| [pypdf](https://pypi.org/pypi/pypdf/6.21.1/json) | 6.21.1 | BSD-3-Clause | 直接 |
| [pytest](https://pypi.org/pypi/pytest/9.1.1/json) | 9.1.1 | MIT | 开发 |
| [python-docx](https://pypi.org/pypi/python-docx/1.2.0/json) | 1.2.0 | MIT | 直接 |
| [python-dotenv](https://pypi.org/pypi/python-dotenv/1.2.4/json) | 1.2.4 | BSD-3-Clause | 间接 |
| [python-multipart](https://pypi.org/pypi/python-multipart/0.0.32/json) | 0.0.32 | Apache-2.0 | 间接 |
| [python-pptx](https://pypi.org/pypi/python-pptx/1.0.2/json) | 1.0.2 | MIT | 直接 |
| [pywin32](https://pypi.org/pypi/pywin32/312/json) | 312 | PSF（PyPI 元数据；本机未安装） | 间接；本机未安装 |
| [pywin32-ctypes](https://pypi.org/pypi/pywin32-ctypes/0.2.3/json) | 0.2.3 | BSD-3-Clause | 间接；本机未安装 |
| [pyyaml](https://pypi.org/pypi/pyyaml/6.0.3/json) | 6.0.3 | MIT | 直接；扩展元数据解析复用既有锁定包 |
| [referencing](https://pypi.org/pypi/referencing/0.37.0/json) | 0.37.0 | MIT | 间接 |
| [requests](https://pypi.org/pypi/requests/2.34.2/json) | 2.34.2 | Apache-2.0 | 间接 |
| [requests-toolbelt](https://pypi.org/pypi/requests-toolbelt/1.0.0/json) | 1.0.0 | Apache 2.0 | 间接 |
| [rpds-py](https://pypi.org/pypi/rpds-py/2026.9.1/json) | 2026.9.1 | MIT | 间接 |
| [ruff](https://pypi.org/pypi/ruff/0.17.1/json) | 0.17.1 | MIT | 开发 |
| [secretstorage](https://pypi.org/pypi/secretstorage/3.5.0/json) | 3.5.0 | BSD-3-Clause | 间接 |
| [sniffio](https://pypi.org/pypi/sniffio/1.3.1/json) | 1.3.1 | MIT OR Apache-2.0 | 间接 |
| [sqlalchemy](https://pypi.org/pypi/sqlalchemy/2.1.5/json) | 2.1.5 | MIT | 直接 |
| [sse-starlette](https://pypi.org/pypi/sse-starlette/3.5.0/json) | 3.5.0 | BSD-3-Clause | 间接 |
| [starlette](https://pypi.org/pypi/starlette/1.8.0/json) | 1.8.0 | BSD-3-Clause | 间接 |
| [tenacity](https://pypi.org/pypi/tenacity/9.2.1/json) | 9.2.1 | Apache-2.0 | 间接 |
| [truststore](https://pypi.org/pypi/truststore/0.10.5/json) | 0.10.5 | MIT | 间接 |
| [typing-extensions](https://pypi.org/pypi/typing-extensions/4.16.0/json) | 4.16.0 | PSF-2.0 | 间接 |
| [typing-inspection](https://pypi.org/pypi/typing-inspection/0.4.4/json) | 0.4.4 | MIT | 间接 |
| [tzdata](https://pypi.org/pypi/tzdata/2026.5/json) | 2026.5 | Apache-2.0 | 间接；本机未安装 |
| [urllib3](https://pypi.org/pypi/urllib3/2.8.0/json) | 2.8.0 | MIT | 间接 |
| [uuid-utils](https://pypi.org/pypi/uuid-utils/0.17.1/json) | 0.17.1 | BSD-3-Clause | 间接 |
| [uvicorn](https://pypi.org/pypi/uvicorn/0.54.0/json) | 0.54.0 | BSD-3-Clause | 直接 |
| [websockets](https://pypi.org/pypi/websockets/16.1.1/json) | 16.1.1 | BSD-3-Clause | 间接 |
| [xlsxwriter](https://pypi.org/pypi/xlsxwriter/3.2.9/json) | 3.2.9 | BSD-2-Clause | 间接 |
| [xxhash](https://pypi.org/pypi/xxhash/4.0.1/json) | 4.0.1 | BSD-2-Clause | 间接 |
| [zstandard](https://pypi.org/pypi/zstandard/0.25.0/json) | 0.25.0 | BSD-3-Clause | 间接 |

需要保留的具体差异：

- `psycopg`、`psycopg-binary`、`psycopg-pool` 均为 **LGPL-3.0-only**，不是 MIT 或 Apache。当前安装包自带 LGPL 完整文本；binary wheel 所含 libpq 等原生库还具有各自声明。分发这些库或组合产物时须按实际形式履行 LGPL 的许可证、对应源码及用户替换/重新链接等适用义务，不以本项目 Apache 许可证替代。参见 [Psycopg 官方源码](https://github.com/psycopg/psycopg) 与其随包 `dist-info/licenses/`。
- `orjson` 的实际表达式为 **MPL-2.0 AND (Apache-2.0 OR MIT)**；`certifi` 为 MPL-2.0。分发适用 MPL 的文件及修改时保留其声明并履行相应源码可得性要求。此处没有把 MPL 覆盖部分改为 Apache。
- Pillow 的表达式是 **MIT-CMU**；greenlet 是 **MIT AND PSF-2.0**；cffi 是 **MIT-0**。表中保留这些差别。
- Python wheel 可能另带原生依赖的版权和许可证，例如 Pillow、lxml、cryptography、psycopg-binary。上表是 Python 发行包层级清单，不宣称穷尽 wheel 内所有原生组成。实际随包 `*.dist-info/licenses/`、原生库许可文件和源码声明需一并保留。
- colorama 的锁定版本元数据仅确认 BSD 类别，未在本机安装并逐条确定 BSD 变体；pywin32 的元数据为 PSF，本机未安装。不据此声称这些平台构建已完成分发材料验收。tzdata 的包代码标注 Apache-2.0，其内时区数据还应保留上游数据声明。

## 前端依赖

版本和许可来自 `apps/web/package-lock.json`，直接包同时核对 `node_modules/<包>/package.json` 和可用的 LICENSE/NOTICE。以下为全部锁定的非 dev 条目；它们是构建产物可能包含的依赖集合，并不表示每个模块都必然进入最终 bundle。传递包按锁文件安装路径区分，避免折叠不同版本。

| 包（npm 上游） | 锁定版本 | 许可 |
| --- | --- | --- |
| [node_modules/@tanstack/query-core](https://www.npmjs.com/package/@tanstack/query-core/v/5.104.3) | 5.104.3 | MIT |
| [node_modules/@tanstack/react-query](https://www.npmjs.com/package/@tanstack/react-query/v/5.104.3) | 5.104.3 | MIT |
| [node_modules/@types/debug](https://www.npmjs.com/package/@types/debug/v/4.1.13) | 4.1.13 | MIT |
| [node_modules/@types/estree](https://www.npmjs.com/package/@types/estree/v/1.0.9) | 1.0.9 | MIT |
| [node_modules/@types/estree-jsx](https://www.npmjs.com/package/@types/estree-jsx/v/1.0.5) | 1.0.5 | MIT |
| [node_modules/@types/hast](https://www.npmjs.com/package/@types/hast/v/3.0.5) | 3.0.5 | MIT |
| [node_modules/@types/mdast](https://www.npmjs.com/package/@types/mdast/v/4.0.4) | 4.0.4 | MIT |
| [node_modules/@types/ms](https://www.npmjs.com/package/@types/ms/v/2.1.0) | 2.1.0 | MIT |
| [node_modules/@types/react](https://www.npmjs.com/package/@types/react/v/19.3.0) | 19.3.0 | MIT |
| [node_modules/@types/unist](https://www.npmjs.com/package/@types/unist/v/3.0.3) | 3.0.3 | MIT |
| [node_modules/@ungap/structured-clone](https://www.npmjs.com/package/@ungap/structured-clone/v/1.4.0) | 1.4.0 | ISC |
| [node_modules/bail](https://www.npmjs.com/package/bail/v/2.0.2) | 2.0.2 | MIT |
| [node_modules/ccount](https://www.npmjs.com/package/ccount/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/character-entities](https://www.npmjs.com/package/character-entities/v/2.0.2) | 2.0.2 | MIT |
| [node_modules/character-entities-html4](https://www.npmjs.com/package/character-entities-html4/v/2.1.0) | 2.1.0 | MIT |
| [node_modules/character-entities-legacy](https://www.npmjs.com/package/character-entities-legacy/v/3.0.0) | 3.0.0 | MIT |
| [node_modules/character-reference-invalid](https://www.npmjs.com/package/character-reference-invalid/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/comma-separated-tokens](https://www.npmjs.com/package/comma-separated-tokens/v/2.0.3) | 2.0.3 | MIT |
| [node_modules/csstype](https://www.npmjs.com/package/csstype/v/3.2.3) | 3.2.3 | MIT |
| [node_modules/debug](https://www.npmjs.com/package/debug/v/4.4.3) | 4.4.3 | MIT |
| [node_modules/decode-named-character-reference](https://www.npmjs.com/package/decode-named-character-reference/v/1.3.0) | 1.3.0 | MIT |
| [node_modules/dequal](https://www.npmjs.com/package/dequal/v/2.0.3) | 2.0.3 | MIT |
| [node_modules/devlop](https://www.npmjs.com/package/devlop/v/1.1.0) | 1.1.0 | MIT |
| [node_modules/echarts](https://www.npmjs.com/package/echarts/v/6.1.0) | 6.1.0 | Apache-2.0 |
| [node_modules/escape-string-regexp](https://www.npmjs.com/package/escape-string-regexp/v/5.0.0) | 5.0.0 | MIT |
| [node_modules/estree-util-is-identifier-name](https://www.npmjs.com/package/estree-util-is-identifier-name/v/3.0.0) | 3.0.0 | MIT |
| [node_modules/extend](https://www.npmjs.com/package/extend/v/3.0.2) | 3.0.2 | MIT |
| [node_modules/hast-util-to-jsx-runtime](https://www.npmjs.com/package/hast-util-to-jsx-runtime/v/2.3.6) | 2.3.6 | MIT |
| [node_modules/hast-util-whitespace](https://www.npmjs.com/package/hast-util-whitespace/v/3.0.0) | 3.0.0 | MIT |
| [node_modules/html-url-attributes](https://www.npmjs.com/package/html-url-attributes/v/3.0.1) | 3.0.1 | MIT |
| [node_modules/inline-style-parser](https://www.npmjs.com/package/inline-style-parser/v/0.2.7) | 0.2.7 | MIT |
| [node_modules/is-alphabetical](https://www.npmjs.com/package/is-alphabetical/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/is-alphanumerical](https://www.npmjs.com/package/is-alphanumerical/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/is-decimal](https://www.npmjs.com/package/is-decimal/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/is-hexadecimal](https://www.npmjs.com/package/is-hexadecimal/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/is-plain-obj](https://www.npmjs.com/package/is-plain-obj/v/4.1.0) | 4.1.0 | MIT |
| [node_modules/longest-streak](https://www.npmjs.com/package/longest-streak/v/3.1.0) | 3.1.0 | MIT |
| [node_modules/lucide-react](https://www.npmjs.com/package/lucide-react/v/1.59.0) | 1.59.0 | ISC；另含 Feather 衍生图标 MIT 声明 |
| [node_modules/markdown-table](https://www.npmjs.com/package/markdown-table/v/3.0.4) | 3.0.4 | MIT |
| [node_modules/mdast-util-find-and-replace](https://www.npmjs.com/package/mdast-util-find-and-replace/v/3.0.3) | 3.0.3 | MIT |
| [node_modules/mdast-util-from-markdown](https://www.npmjs.com/package/mdast-util-from-markdown/v/2.1.0) | 2.1.0 | MIT |
| [node_modules/mdast-util-gfm](https://www.npmjs.com/package/mdast-util-gfm/v/3.1.0) | 3.1.0 | MIT |
| [node_modules/mdast-util-gfm-autolink-literal](https://www.npmjs.com/package/mdast-util-gfm-autolink-literal/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/mdast-util-gfm-footnote](https://www.npmjs.com/package/mdast-util-gfm-footnote/v/2.1.0) | 2.1.0 | MIT |
| [node_modules/mdast-util-gfm-strikethrough](https://www.npmjs.com/package/mdast-util-gfm-strikethrough/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/mdast-util-gfm-table](https://www.npmjs.com/package/mdast-util-gfm-table/v/2.0.0) | 2.0.0 | MIT |
| [node_modules/mdast-util-gfm-task-list-item](https://www.npmjs.com/package/mdast-util-gfm-task-list-item/v/2.0.0) | 2.0.0 | MIT |
| [node_modules/mdast-util-mdx-expression](https://www.npmjs.com/package/mdast-util-mdx-expression/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/mdast-util-mdx-jsx](https://www.npmjs.com/package/mdast-util-mdx-jsx/v/3.2.0) | 3.2.0 | MIT |
| [node_modules/mdast-util-mdxjs-esm](https://www.npmjs.com/package/mdast-util-mdxjs-esm/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/mdast-util-phrasing](https://www.npmjs.com/package/mdast-util-phrasing/v/4.1.0) | 4.1.0 | MIT |
| [node_modules/mdast-util-to-hast](https://www.npmjs.com/package/mdast-util-to-hast/v/13.2.1) | 13.2.1 | MIT |
| [node_modules/mdast-util-to-markdown](https://www.npmjs.com/package/mdast-util-to-markdown/v/2.2.0) | 2.2.0 | MIT |
| [node_modules/mdast-util-to-string](https://www.npmjs.com/package/mdast-util-to-string/v/4.0.0) | 4.0.0 | MIT |
| [node_modules/micromark](https://www.npmjs.com/package/micromark/v/4.0.3) | 4.0.3 | MIT |
| [node_modules/micromark-core-commonmark](https://www.npmjs.com/package/micromark-core-commonmark/v/2.0.4) | 2.0.4 | MIT |
| [node_modules/micromark-extension-gfm](https://www.npmjs.com/package/micromark-extension-gfm/v/3.0.0) | 3.0.0 | MIT |
| [node_modules/micromark-extension-gfm-autolink-literal](https://www.npmjs.com/package/micromark-extension-gfm-autolink-literal/v/2.1.0) | 2.1.0 | MIT |
| [node_modules/micromark-extension-gfm-footnote](https://www.npmjs.com/package/micromark-extension-gfm-footnote/v/2.1.0) | 2.1.0 | MIT |
| [node_modules/micromark-extension-gfm-strikethrough](https://www.npmjs.com/package/micromark-extension-gfm-strikethrough/v/2.1.0) | 2.1.0 | MIT |
| [node_modules/micromark-extension-gfm-table](https://www.npmjs.com/package/micromark-extension-gfm-table/v/2.1.2) | 2.1.2 | MIT |
| [node_modules/micromark-extension-gfm-tagfilter](https://www.npmjs.com/package/micromark-extension-gfm-tagfilter/v/2.0.0) | 2.0.0 | MIT |
| [node_modules/micromark-extension-gfm-task-list-item](https://www.npmjs.com/package/micromark-extension-gfm-task-list-item/v/2.1.0) | 2.1.0 | MIT |
| [node_modules/micromark-factory-destination](https://www.npmjs.com/package/micromark-factory-destination/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-factory-label](https://www.npmjs.com/package/micromark-factory-label/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-factory-space](https://www.npmjs.com/package/micromark-factory-space/v/2.1.0) | 2.1.0 | MIT |
| [node_modules/micromark-factory-title](https://www.npmjs.com/package/micromark-factory-title/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-factory-whitespace](https://www.npmjs.com/package/micromark-factory-whitespace/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-util-character](https://www.npmjs.com/package/micromark-util-character/v/2.1.1) | 2.1.1 | MIT |
| [node_modules/micromark-util-chunked](https://www.npmjs.com/package/micromark-util-chunked/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-util-classify-character](https://www.npmjs.com/package/micromark-util-classify-character/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-util-combine-extensions](https://www.npmjs.com/package/micromark-util-combine-extensions/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-util-decode-numeric-character-reference](https://www.npmjs.com/package/micromark-util-decode-numeric-character-reference/v/2.0.2) | 2.0.2 | MIT |
| [node_modules/micromark-util-decode-string](https://www.npmjs.com/package/micromark-util-decode-string/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-util-edit-map](https://www.npmjs.com/package/micromark-util-edit-map/v/1.0.0) | 1.0.0 | MIT |
| [node_modules/micromark-util-encode](https://www.npmjs.com/package/micromark-util-encode/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-util-html-tag-name](https://www.npmjs.com/package/micromark-util-html-tag-name/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-util-normalize-identifier](https://www.npmjs.com/package/micromark-util-normalize-identifier/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-util-resolve-all](https://www.npmjs.com/package/micromark-util-resolve-all/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-util-sanitize-uri](https://www.npmjs.com/package/micromark-util-sanitize-uri/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-util-subtokenize](https://www.npmjs.com/package/micromark-util-subtokenize/v/2.1.0) | 2.1.0 | MIT |
| [node_modules/micromark-util-symbol](https://www.npmjs.com/package/micromark-util-symbol/v/2.0.1) | 2.0.1 | MIT |
| [node_modules/micromark-util-types](https://www.npmjs.com/package/micromark-util-types/v/2.0.3) | 2.0.3 | MIT |
| [node_modules/ms](https://www.npmjs.com/package/ms/v/2.1.3) | 2.1.3 | MIT |
| [node_modules/parse-entities](https://www.npmjs.com/package/parse-entities/v/4.0.2) | 4.0.2 | MIT |
| [node_modules/parse-entities/node_modules/@types/unist](https://www.npmjs.com/package/@types/unist/v/2.0.11) | 2.0.11 | MIT |
| [node_modules/property-information](https://www.npmjs.com/package/property-information/v/7.2.0) | 7.2.0 | MIT |
| [node_modules/react](https://www.npmjs.com/package/react/v/19.3.1) | 19.3.1 | MIT |
| [node_modules/react-dom](https://www.npmjs.com/package/react-dom/v/19.3.1) | 19.3.1 | MIT |
| [node_modules/react-markdown](https://www.npmjs.com/package/react-markdown/v/10.1.0) | 10.1.0 | MIT |
| [node_modules/remark-gfm](https://www.npmjs.com/package/remark-gfm/v/4.0.1) | 4.0.1 | MIT |
| [node_modules/remark-parse](https://www.npmjs.com/package/remark-parse/v/11.0.0) | 11.0.0 | MIT |
| [node_modules/remark-rehype](https://www.npmjs.com/package/remark-rehype/v/11.1.2) | 11.1.2 | MIT |
| [node_modules/remark-stringify](https://www.npmjs.com/package/remark-stringify/v/11.0.0) | 11.0.0 | MIT |
| [node_modules/scheduler](https://www.npmjs.com/package/scheduler/v/0.28.0) | 0.28.0 | MIT |
| [node_modules/space-separated-tokens](https://www.npmjs.com/package/space-separated-tokens/v/2.0.2) | 2.0.2 | MIT |
| [node_modules/stringify-entities](https://www.npmjs.com/package/stringify-entities/v/4.0.4) | 4.0.4 | MIT |
| [node_modules/style-to-js](https://www.npmjs.com/package/style-to-js/v/1.1.21) | 1.1.21 | MIT |
| [node_modules/style-to-object](https://www.npmjs.com/package/style-to-object/v/1.0.14) | 1.0.14 | MIT |
| [node_modules/trim-lines](https://www.npmjs.com/package/trim-lines/v/3.0.1) | 3.0.1 | MIT |
| [node_modules/trough](https://www.npmjs.com/package/trough/v/2.2.0) | 2.2.0 | MIT |
| [node_modules/tslib](https://www.npmjs.com/package/tslib/v/2.3.0) | 2.3.0 | 0BSD |
| [node_modules/unified](https://www.npmjs.com/package/unified/v/11.0.5) | 11.0.5 | MIT |
| [node_modules/unist-util-is](https://www.npmjs.com/package/unist-util-is/v/6.0.1) | 6.0.1 | MIT |
| [node_modules/unist-util-position](https://www.npmjs.com/package/unist-util-position/v/5.0.0) | 5.0.0 | MIT |
| [node_modules/unist-util-stringify-position](https://www.npmjs.com/package/unist-util-stringify-position/v/4.0.0) | 4.0.0 | MIT |
| [node_modules/unist-util-visit](https://www.npmjs.com/package/unist-util-visit/v/5.1.0) | 5.1.0 | MIT |
| [node_modules/unist-util-visit-parents](https://www.npmjs.com/package/unist-util-visit-parents/v/6.0.2) | 6.0.2 | MIT |
| [node_modules/vfile](https://www.npmjs.com/package/vfile/v/6.0.3) | 6.0.3 | MIT |
| [node_modules/vfile-message](https://www.npmjs.com/package/vfile-message/v/4.0.3) | 4.0.3 | MIT |
| [node_modules/zrender](https://www.npmjs.com/package/zrender/v/6.1.0) | 6.1.0 | BSD-3-Clause |
| [node_modules/zwitch](https://www.npmjs.com/package/zwitch/v/2.0.4) | 2.0.4 | MIT |

当前前端直接开发/构建工具如下。它们的可执行程序、类型声明和开发服务器没有作为本项目源码的一部分提交；构建工具生成或注入产物的第三方运行代码仍须保留对应声明，不能仅凭 `devDependency` 排除。

| 包 | 锁定版本 | 许可 |
| --- | --- | --- |
| [@tailwindcss/vite](https://www.npmjs.com/package/@tailwindcss/vite/v/4.3.3) | 4.3.3 | MIT |
| [@types/react](https://www.npmjs.com/package/@types/react/v/19.3.0) | 19.3.0 | MIT |
| [@types/react-dom](https://www.npmjs.com/package/@types/react-dom/v/19.3.0) | 19.3.0 | MIT |
| [@vitejs/plugin-react](https://www.npmjs.com/package/@vitejs/plugin-react/v/5.2.0) | 5.2.0 | MIT |
| [openapi-typescript](https://www.npmjs.com/package/openapi-typescript/v/7.13.0) | 7.13.0 | MIT |
| [prettier](https://www.npmjs.com/package/prettier/v/3.9.10) | 3.9.10 | MIT |
| [tailwindcss](https://www.npmjs.com/package/tailwindcss/v/4.3.3) | 4.3.3 | MIT |
| [typescript](https://www.npmjs.com/package/typescript/v/5.9.3) | 5.9.3 | Apache-2.0 |
| [vite](https://www.npmjs.com/package/vite/v/7.3.7) | 7.3.7 | MIT |

开发工具的其余传递依赖和平台构建包仍列于 npm 锁文件，许可包括 MIT、ISC、BSD、Apache-2.0、MPL-2.0、CC-BY-4.0、Python-2.0 等；这些类别来自整个锁文件，不能视作每种都进入产品运行 bundle。上游包原始版权/许可证保存在实际安装目录，不能用本表替代。

已核对的前端原始声明：

- React / React DOM 19.3.1：MIT；[React 上游](https://github.com/facebook/react)。
- Apache ECharts 6.1.0：Apache-2.0，其随包 `NOTICE` 已原文保留到根 `NOTICE`；[Apache ECharts 上游](https://github.com/apache/echarts)。
- Lucide React 1.59.0：随包 `LICENSE` 包含 Lucide 的 ISC 正文，以及指定 Feather 衍生图标的 MIT 正文与 Cole Bemis 版权声明。使用相关图标时两部分声明均须保留；不能仅保留 package.json 的 ISC 标签。[Lucide 上游许可证](https://github.com/lucide-icons/lucide/blob/main/LICENSE)。
- zrender 6.1.0 为 BSD-3-Clause，tslib 2.3.0 为 0BSD；不能因为其使用者 ECharts 为 Apache 而覆盖这两者的许可。

## 独立原生、搜索与浏览器组件

这些是用户按功能单独取得的第三方组件，不在本仓库 Git 源码中捆绑，也没有被统一改成 Apache。安装来源与已有核对记录见相应来源文件；若另行打包分发其二进制或源码，必须保留各组件及其依赖的版权、完整许可证和适用的对应源码/修改说明等材料。

| 组件 | 已有真实安装/来源记录 | 上游许可边界 |
| --- | --- | --- |
| SearXNG | 独立 `.runtime/search-env/`；2026.10.17+2a6fc07，revision `2a6fc07be3760543c7b6d1d1bfe62f5765d5d9d6`；[仓库来源记录](apps/server/src/workbench/modules/search/sources.md) | **AGPL-3.0-or-later**。上游源码和 LICENSE 保留在本机独立环境。其依赖另有各自许可，不整体改授 AGPL 或 Apache。分发和修改后通过网络提供服务时分别遵守 AGPL 的适用源码提供义务。[官方源码及 LICENSE](https://github.com/searxng/searxng)，[官方安装说明](https://docs.searxng.org/admin/installation.html)。 |
| LibreOffice | 用户选择安装的可选 Office 引擎；本机从 Ubuntu 官方源取 ARM64 包置于 `.tools/file-components/`，版本/URI 见该目录 `packages.json`；[仓库来源记录](runtime/file-processing/sources.md) | 主体 **MPL-2.0**，含 Apache 等其他许可来源和独立第三方库，不能将全部包简单称为单一许可。实际 Ubuntu 包的版权保留在 `root/usr/share/doc/<包>/copyright`，共同许可证保留在 `licenses/`。[官方许可说明](https://www.libreoffice.org/licenses/)，[官方源码](https://git.libreoffice.org/core)。 |
| FFmpeg / ffprobe | 复用用户原已安装的构建；本项目未下载、未捆绑。已有记录确认此构建启用 `--enable-gpl` 与 `--enable-version3`；[仓库来源记录](runtime/file-processing/sources.md) | 当前实际构建按 **GPL v3 构建**处理，不能笼统按 FFmpeg 基础 LGPL 版本标注。具体组件、编解码器和再分发义务以该构建、相应源码和上游法律说明为准。[FFmpeg 官方法律说明](https://ffmpeg.org/legal.html)，[官方源码](https://ffmpeg.org/download.html#get-sources)。 |
| whisper.cpp | 可选本地ASR原生CPU引擎，**1.9.5**，固定提交 `d1be6fde11ac6e0407606b4e42fe72d34add8037`；私有 `.tools/asr/`，源码与LICENSE保留；[来源记录](runtime/file-processing/sources.md) | **MIT**；上游版权 `Copyright (c) 2023-2026 The ggml authors`。引擎不改授Apache，内含第三方声明仍各自适用；再分发时保留原始版权与完整许可。[固定上游LICENSE](https://github.com/ggml-org/whisper.cpp/blob/d1be6fde11ac6e0407606b4e42fe72d34add8037/LICENSE)。 |
| bubblewrap | 用户系统已有可选Linux组件；本机Ubuntu包 **0.9.0-1ubuntu0.3**，许可依据实际 `/usr/share/doc/bubblewrap/copyright`；本项目未下载、未捆绑或重新分发系统组件 | 主体按包内 `Files: *` 的 **LGPL-2+（LGPL-2.0-or-later）**，保留其原始版权/许可及适用源码义务。`m4/attributes.m4` 单文件另列的GPL-2+ with Autoconf exception不代表整个bubblewrap的许可，不能套用于运行组件。[上游源码](https://github.com/containers/bubblewrap/)。 |
| Poppler / pdftoppm | 复用本机现有原生工具；来源记录中的版本为 24.02.0，未作为项目源码提交 | 保留该实际安装包的上游许可证与依赖声明；未在本次对该本机二进制的全部组成逐项复核，不将其重新许可为 Apache。[Poppler 上游](https://poppler.freedesktop.org/)。 |
| Tesseract | 可选 OCR；已有来源记录未将引擎安装视作完成 | 引擎上游 Apache-2.0；语言 traineddata 的来源和许可独立核对，本文件不声称已安装或统一许可。[官方源码](https://github.com/tesseract-ocr/tesseract)，[官方语言数据](https://github.com/tesseract-ocr/tessdata)。 |
| 浏览器与 Playwright | Python Playwright 1.64.0；本机既有 Google Chrome 155.0.8059.84，本项目未下载浏览器；[仓库来源记录](apps/server/src/workbench/modules/browser/sources.md) | Playwright Apache-2.0；Google Chrome 有其独立条款，不等同于 Chromium 的所有源文件许可。用户自行安装的浏览器不因被调用而成为本项目 Apache 软件。[Playwright](https://github.com/microsoft/playwright-python)，[Chrome 条款](https://www.google.com/chrome/terms/)。 |

系统 Python、Node.js/npm、uv、PostgreSQL 及其客户端、OS Keyring 服务、图形桌面服务和 GPU 驱动/工具链也由用户环境独立提供。它们的安装程序或完整运行时不是本次 Git 源码交付物；各自原始许可证仍适用。uv/npm 等仅用于安装或构建时，不应误标为本项目自有代码。参见 [安装来源记录](runtime/installation/sources.md)。

## 模型、权重与 GPU 运行时

`models/` 整个路径不在本项目 Apache-2.0 授权范围内；权重、tokenizer、模型配置和上游模型卡按各自条款使用。模型路径被 Git 忽略，本文没有将权重纳入源码发行，也没有下载或复制权重来制作许可文件。

本机选用的 `nvidia/Qwen3.8-Flash-Next-NVFP4`，固定 revision `fc694b54fb0174e0913e6adf86691ef85a4ead47`，其模型卡元数据标注 `license: other`、`license_name: nvidia-open-model-license`，并列明 NVIDIA Open Model License 与 Qwen Community License 1.0。此处仅记录已读模型卡的许可来源，未宣称已完整核验所有用途、地域、再分发或商业使用条件，也不作“权重可自由商用”的保证。使用者须阅读该固定 revision 对应的完整条款并保留所需声明。

- [NVIDIA 官方模型卡](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4/tree/fc694b54fb0174e0913e6adf86691ef85a4ead47)
- [NVIDIA Open Model License 官方文本](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/)
- [模型卡指向的 Qwen Community License 1.0](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE)，以其上游原文为准，本文件不另行替换条款。

可选ASR另使用 **Whisper tiny多语言 GGML** `ggml-tiny.bin`，来自维护者 `ggerganov/whisper.cpp`，固定revision `5359861c739e955e79d9a303bcbc70fb988958b1`，实际 **77,691,713B**。该[固定转换模型卡](https://huggingface.co/ggerganov/whisper.cpp/blob/5359861c739e955e79d9a303bcbc70fb988958b1/README.md)标注 **MIT**；[原OpenAI Whisper固定README](https://github.com/openai/whisper/blob/86098128c0b4f24f0e2aa2994de830614b474227/README.md#license)明确代码和权重均按MIT发布，[原LICENSE](https://github.com/openai/whisper/blob/86098128c0b4f24f0e2aa2994de830614b474227/LICENSE)版权为 `Copyright (c) 2022 OpenAI`，原文保存在私有模型目录 `LICENSE.openai-whisper`。权重未纳入Git源码或统一Apache授权，应用不自动下载；按用户显式授权准备的收据为 `.local/asr/model-source.json`。本机真实ASR页面已完成转录与下载，但不是识别准确率基准。该tiny的MIT证据不适用于Flash-Next或其他模型；若另行分发引擎或tiny权重，须随附其完整MIT版权/许可及适用的第三方声明。

SGLang、PyTorch、FlashInfer 等模型运行软件位于独立 `.runtime/model-env/`，本机FLUX使用独立 `.runtime/media-env/`及[runtime/media/requirements.txt](runtime/media/requirements.txt)；这些不是根 `uv.lock` 的应用依赖集合，其软件许可与模型权重许可分别适用。CUDA、NVIDIA 驱动及相关库有各自条款，不能从某个开源推理引擎的许可推导出整个 GPU 环境或模型权重的许可。

## 保留与再分发

当前交付范围是原创源码及锁文件，第三方安装目录与用户已装原生工具未打入 Git。自行制作带 Python wheel、前端 bundle、原生工具或模型的安装包时，应按实际纳入内容附带第三方的完整许可证、版权声明及所需 NOTICE，并履行适用的 LGPL/MPL/GPL/AGPL 源码等义务；本表和上游网址不能替代这些随包材料。保留上游既有的许可/来源文件；不删除或覆盖其版权，不把本机已有程序描述为本项目下载或捆绑的软件。

本文件只对上述锁定版本与已有真实来源作许可记录，不表示已经完成其他平台安装包、未来版本、模型全部用途或实际发布制品的逐项法律审阅。

## BFL FLUX 3 Image Klein参考实现

本项目`scripts/media/flux_phase.py`的flow-map采样流程改编自Black Forest Labs参考实现（Apache-2.0），固定commit `bfc8889a4497f846416f253a3972a1941ac698b9`；来源为2026-10-19生成能力阶段，后续内存优化没有更换该上游修订。上游源代码、版权与许可证见[固定参考实现](https://github.com/black-forest-labs/flux-3-image-klein/tree/bfc8889a4497f846416f253a3972a1941ac698b9)。权重及其许可证单独管理，HF配置/权重固定revision为`337337621b8526e120f2275d250242f4a6bedd75`，不随本项目源码分发；实际环境与用途边界见[本机生成环境](runtime/media/README.md)。
