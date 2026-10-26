# RDesk

**English** · [简体中文](README.zh-CN.md)

**An AI-friendly, local-first AI agent workbench.** Connect models, work with files, write code, automate a browser and generate images in one self-hosted web interface. Agents can discover tools, read their operating guide and inspect or configure workbench settings.

RDesk brings together chat, durable tasks, multi-agent collaboration, MCP tools and resources, persistent Markdown memory, browser automation and image generation. Tasks retain their state, tool results and file provenance so you can inspect, steer, stop and resume work.

**1.0.0 · Apache-2.0 · Authors: 任天卓 (Ren Tianzhuo) & Codex**

[Quick start](#first-time-setup) · [Capabilities and limitations](#capabilities-and-limitations) · [AI operating guide](docs/ai-guide.md) · [Documentation](docs/README.md) · [Authors](#authors)

## Features

| Capability | What it provides |
| --- | --- |
| AI-friendly configuration | Discover tools by purpose; read configuration contracts; preview changes, save settings and verify receipts; read the built-in operating guide in sections |
| Multi-agent collaboration | Bidirectional parent/child messages, multiple rounds, follow-up instructions, interruption and closure; waiting releases inference capacity |
| Durable workflows | Streaming chat, file operations, long-running commands, plans, steering and cancellation, with records available after a refresh |
| Files and memory | Uploads, extraction, previews, versioned artifacts and persistent Markdown memory; permission-scoped search and reading of other conversations |
| Tools and extensions | MCP tools and resources, plugins/Skills, code intelligence, Notebook editing and managed browser automation |
| Generation | A dedicated generation page and agent tools share tasks and files; local FLUX image generation and reference editing have been tested on real hardware |

> **This repository contains source code, documentation and dependency configuration. It does not contain model weights, inference environments, user files, conversations, databases or credentials.** Prepare models and optional components separately; see the [model runtime guide](runtime/model/README.md) and [image generation guide](runtime/media/README.md).

Version **1.0.0 was completed on October 26, 2026**. See the [implementation and acceptance record](docs/implementation-status.md) for the exact scope and remaining limitations. Detailed project documentation and the AI operating guide are currently in Chinese.

## How it works

RDesk uses React/TypeScript, FastAPI, PostgreSQL and LangGraph. Native deployment paths are provided for Linux, Windows and macOS without requiring Docker. **Actual runtime and browser acceptance has primarily been performed on Ubuntu 24.04 ARM64 on NVIDIA DGX Spark.** Windows and macOS still require real-device validation. The deliverable is source code and native source-release tooling, rather than a complete signed binary installer.

Version 1.0.0 listens on loopback only and does not require an application login password. Opening the local page establishes an owner session; random sessions and cross-site write protection remain in place, as does ownership of existing data. LAN listening is disabled. The credential vault has its own unlock process.

By default, a separate request to the configured local model reviews operations that require authorization, including file writes and command execution. Uncertain decisions, review failures and an unavailable local reviewer result in denial with a reason. Manual approval is used only when explicitly selected by the user. Workspace, file-version, network and credential boundaries still apply.

While a task runs, new messages can be queued or used to steer it, according to **Settings → General**. Queued messages can be promoted to immediate steering. In-progress tools reach a safe boundary before new instructions take effect. Stopping is a separate action. Failure or cancellation pauses and preserves the queue; automatic dispatch resumes only after successful completion and resource cleanup. Conversations support renaming, archiving, branching from edited user messages and deletion after a scope preview.

Chat displays progress and expandable tool calls in execution order, including preparation and running states. Readable reasoning is shown when the model supplies it; parameters and debugging details are collapsed by default. Child tasks open within the parent conversation, while diagnostics live on the background-tasks page. A context indicator provides usage and compaction controls. Type `/` for shortcuts, including `/compact`, `/context`, `/new`, `/settings`, `/model` and `/help`, with Chinese aliases. Compaction retains original messages. An empty output limit allocates space from the remaining context. Children inherit budgets and permitted read tools; model inference concurrency remains **one**.

Upload attachments by selection, drag-and-drop or paste: up to **1 GB per file and eight files per message**, without an extension allowlist. Supported previews and content preparation run automatically; unsupported formats retain the original file and clearly report that their content was not read. The first two images in a message are prepared automatically. Sending attachments to a remote model requires transfer confirmation.

Install plugins and Skills from a local directory, ZIP or Git source in **Settings → Plugins and Skills**. Tasks freeze the installed version; disabling an extension revokes subsequent use. Dedicated read tools expose registered rules and Skills. Model settings offer native behavior, five workbench instruction presets or custom instructions; these presets do not imply every model supports a native “Max” parameter.

The **Generate** page supports configured image, video, audio and 3D adapters. Local FLUX 3 Image Klein NVFP4 generation and reference editing have been tested on Spark. Other modalities require a compatible provider; unavailable ones are identified in the interface. Outputs enter the shared file library. Batch deletion stops active tasks and checks resource closure first; deleting records preserves output files and existing references. See [generation architecture](docs/technical/extensions-and-generation.md).

## Prerequisites

| Requirement | Purpose |
| --- | --- |
| Python 3.12 and uv ≥ 0.9.25 | Install the locked Python environment; the launcher does not download Python automatically |
| PostgreSQL 16 | Store tasks and recovery state in a dedicated application database with a limited-privilege role |
| Node ≥ 22.12 and npm ≥ 9.5 | Needed to build a missing frontend or explicitly rebuild it; unnecessary for daily startup when a release already includes `apps/web/dist` |
| A configured model endpoint | A separately prepared local inference service or an explicitly configured external API; model weights and inference environments are not bundled |

Dependency installation uses the default PyPI/npm registries and existing lockfiles. Browser, Office, media, search and ASR components are optional; opening RDesk does not silently download large models or install system services.

## First-time setup

Clone this repository and enter its directory. From the project or an extracted source release, run:

```sh
./start.sh --install doctor
```

This explicitly prepares the Python environment, reuses a bundled frontend or builds a missing one from the npm lockfile, then prints diagnostics. Continue with database setup if it is not configured yet.

Choose one database route:

- **Project-private PostgreSQL on Ubuntu 24.04 ARM64:** run `./start.sh --prepare-database doctor`. This explicitly downloads, extracts and starts native PostgreSQL 16 under the application data root, without sudo, modifying system packages or taking over another database.
- **An existing native PostgreSQL installation:** have its administrator prepare a dedicated database and role, then run `./start.sh configure-database`. The local terminal wizard hides the password, performs read-only validation and asks before saving. It accepts loopback connections only and does not create or migrate the database or overwrite existing configuration. See the [database setup guide](runtime/distribution/database-setup.md).

Start the configured stack:

```sh
./start.sh start
```

Running `./start.sh` without arguments also starts it. The launcher checks configuration, restores the managed database, starts prepared search and local model services, then starts the API, agent/file/media workers and credential service. It reports actual stages, model-shard progress and elapsed time before opening the browser. Healthy existing services are reused; unknown ownership or failures are reported without replaying uncertain actions.

The default URL is **`http://127.0.0.1:8765`**. Saved local ports and frontend configuration take precedence; use the URL printed by the launcher. Private local configuration is excluded from Git and release packages. Unconfigured managed model/search components are not downloaded implicitly; use connections configured in the interface.

Stop the managed stack with:

```sh
./stop.sh
```

After startup completes, closing the terminal leaves services running. During startup, Ctrl+C cleans up components newly started by that invocation while preserving previously running services. Shutdown verifies exits and retains conversations, files, settings and weights. See [service control](runtime/distribution/one-click-services.md).

Windows uses `.\start.ps1` / `.\stop.ps1` with the same arguments; macOS uses the shell scripts. The Spark model, private PostgreSQL and search recipes depend on Linux. Other platforms need separately prepared services and still await full real-device acceptance. Automatic PostgreSQL preparation is not enabled there; use [official native PostgreSQL installers](https://www.postgresql.org/download/) and the configuration wizard.

`--install`, `--prepare-database` and `--rebuild-web` still run the selected command afterwards; without a command the default is `start`. Specify `doctor`, as above, when you only want preparation and diagnostics.

## Everyday commands

| Command | Behavior |
| --- | --- |
| `./start.sh --check` | Check toolchain, frontend and database without installing, migrating or starting services; return nonzero when requirements are missing |
| `./start.sh status` | Show configured database, search, model and workbench service status |
| `./start.sh doctor` | Inspect the selected application configuration and managed process identity without starting business components |
| `./start.sh` / `./start.sh start` | Start the configured stack and reuse healthy services; do not reinstall dependencies or update models |
| `./stop.sh` / `./start.sh stop` | Stop managed workbench, model, search and database services; retain and report unknown resources |
| `./start.sh --app-only start` / `./start.sh --app-only stop` | Advanced: control only the selected API, workers and credential service |
| `./start.sh recover-account` | Retired application-password recovery entry; local sessions are automatic, while vault unlocking remains separate |
| `./start.sh --rebuild-web doctor` | Explicitly install locked frontend dependencies and rebuild; requires Node/npm |

Status queries may create private empty state directories but do not modify business records. Data uses the standard OS application-data directory, with a `WORKBENCH_DATA_DIR` override. Updating source does not move or clear user data. Keep passwords, API keys and vault secrets out of command arguments, shared screenshots and diagnostic reports.

## Capabilities and limitations

The implementation includes workspaces, streaming conversations, persistent questions and approvals, cancellation and recovery, multi-round parent/child communication, Markdown memory search and version restoration, original files and previews, configurable search and web reading, managed browser downloads, MCP, usage/hardware dashboards and isolated restore entry points. Availability depends on configured components and the selected model. Source support alone does not establish end-to-end acceptance.

The current **schema 039** contains media credential columns not yet covered by the product backup exclusion review. Product backup therefore rejects the schema with **`backup_secret_schema_unsupported`**, even when those credential fields are empty. Earlier-schema restore evidence and operational PostgreSQL archives do not establish backup support for the current schema. See [maintenance documentation](apps/server/src/workbench/modules/maintenance/README.md).

Tool discovery, paginated search and line continuation, plans, code intelligence, Notebook editing, MCP resources, interactive Linux terminals, AI configuration APIs, user-synced theme and queue/steering preferences, automatic conversation naming and image-generation waiting animations are implemented. AI can list, search and read other conversations within a user-selected workspace/account scope; child agents are limited to their task tree. The built-in `read_workbench_guide` tool reads the [AI operating guide](docs/ai-guide.md) in sections. See the [acceptance record](docs/implementation-status.md) for real-browser and fixed-local-Qwen evidence.

- **Commands:** persistent ordinary and long-command execution supports logs and stopping. The default budget is one hour, configurable up to 24 hours. Execution currently supports Linux only. `native_scoped` maps HOME/TMP to the workspace but does not isolate host files, networks or processes.
- **Desktop input:** X11 supports printable ASCII. Real controlled English input and manual takeover were tested. Chrome/libpinyin can drop keystrokes; observe the target application before retrying. Other desktop/platform boundaries remain documented.
- **OS sandbox:** optional Linux bubblewrap is integrated, but the tested Spark environment returns `namespace_denied`; it does not silently fall back to host execution.
- **Code intelligence:** TypeScript/JavaScript definitions, references and diagnostics were tested. Python definitions/references need a configured LSP server. Notebooks support cell reading and editing, without kernel execution.
- **File processing:** Office-to-PDF, media processing and ASR require configured components and budgets. Local ASR was tested, without an accuracy benchmark. OCR currently lacks a suitable security-updated component. Arbitrary HTML/JavaScript execution is unavailable; the interface uses restricted trusted components.
- **Native multimodal inputs:** PDF encoding for Chat/Responses/Anthropic and MP3/WAV Chat inputs are implemented with bounded metadata checks. Live acceptance against external providers remains pending; encoding support does not prove a model understands an input.
- **Recovery:** consistent backups require a confirmed quiescent boundary and restoration to a new database/directory. Unfinished tasks wait for reconciliation instead of replaying side effects. Existing acceptance includes restored memory, version history, preserved tasks and full command-log downloads, subject to the current-schema backup limitation above. Bring-your-own databases do not automatically receive private-cluster maintenance capabilities.

**Desktop screenshots may only be sent to local models. They must not be transmitted to external models.** Computer-operation actions remain subject to permissions and review. Managed and everyday browsers are separate. Explicitly configured external models, search and MCP services may receive authorized content; a local web interface does not mean every configured tool operates offline.

## Releases and documentation

[Native source-release tooling](runtime/distribution/README.md) can build a package with a compiled frontend, per-file manifest, locked-dependency SBOM and license materials:

```sh
python3.12 scripts/release/package.py build
```

Its `validate` and `stage` commands check and stage a new directory without installing, migrating or switching the active version. Packages do not bundle Python, model weights, native databases, browsers or a complete offline runtime. Upgrade planning requires a verified backup route compatible with the actual schema, a prepared new version and controlled migration. Failed health checks do not make old code safe against an already migrated database. See the release guide for signing and platform-validation limits.

Detailed documentation (currently Chinese):

- [Documentation index](docs/README.md)
- [AI operating guide](docs/ai-guide.md), also available through `read_workbench_guide`
- [Installation and platform notes](runtime/installation/README.md)
- [Model runtime](runtime/model/README.md)
- [File processing components](runtime/file-processing/README.md)
- [Optional command sandbox](runtime/sandbox/README.md)
- [License](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md)

Version 1.0.0 has completed its listed local acceptance flows, including managed shutdown and reconciliation after a real reboot. Other platforms, external providers, complete offline dependencies and the upgrade/rollback matrix retain the documented limitations. Source, release tooling and a complete installer are distinct deliverables.

## Authors

**任天卓 (Ren Tianzhuo) & Codex**

- **任天卓:** project author; product vision, requirements and user feedback.
- **Codex:** AI development assistant contributing architecture, implementation, documentation and verification.

Original RDesk code is licensed under the [Apache License 2.0](LICENSE). Third-party software and models retain their respective licenses; see [NOTICE](NOTICE) and [third-party notices](THIRD_PARTY_NOTICES.md).
