# DSH Desktop Monorepo 架构报告

> 只读调研结果，供插件化方案设计使用。所有路径相对于 `D:\DSH\dsh-desktop\`（下文简称 `$ROOT`）。
>
> 关键结论先行：**DSH Desktop 产品壳本身就是一个 Cordis 插件**（`dsh-plugin-desktop`，`package.json` description 原文："DSH Desktop: an Electron shell composed as a DeepSeek Harness Cordis plugin"）。它把上游 `deepseek-harness`（独立 pnpm monorepo）打包成的官方运行时以 tgz 形式 vendored 进 `vendor/dsh-runtime/`，Electron 主进程只做四件事：单实例锁 → 解析 profile → 提供 native runtime → 在进程内启动 Host Cordis 根。随后 Loader 加载官方 `dsh-base` + `dsh-web-app` bundle，再在 `dsh-web-app` 之后注入 7 行 desktop 插件。UI 完全走 loopback HTTP/WebSocket Web carrier，**没有任何 Electron IPC 插件面、没有 preload 桥**。

---

## 1. 顶层 workspace 布局

### 1.1 根 `package.json`（`$ROOT\package.json`，1191 行）

- `name: deepseek-harness-desktop`，`private: true`，`packageManager: yarn@4.18.0`，`engines: node ^22.19.0 || >=24.0.0`。
- **workspaces 只有 4 个**：`dsh-plugin-desktop`、`dsh-plugin-desktop-beta`、`dsh-community-fabric`、`dsh-community-market`。
  **`deepseek-harness` 不是 workspace** —— 它是被固定 commit 的"上游检出"，保持自己独立的 pnpm workspace（`$ROOT\deepseek-harness\pnpm-workspace.yaml`），通过 `upstream:*` 脚本与 vendored tgz 同产品解耦（决策记录 `.agents\notes\implemented\architecture\2026-08-15-pinned-upstream-and-isolated-yarn-workspace.md`，被 `$ROOT\docs\architecture.md` 引用）。
- `resolutions`（第 16–1155 行，占文件约 95%）：把全部 `@deepseek-ai/dsh-*` 包精确解析到两个 vendored 运行时的 file tgz：
  - `vendor/dsh-runtime/0.1.5-rc.2/*.tgz`（stable 通道）
  - `vendor/dsh-runtime/0.1.6-alpha.1/*.tgz`（beta 通道）
  每个包名同时 pin 精确版本与 `^` 范围两条 resolution，保证"每个 workspace 只能解析自己的 DSH 运行时"（`docs\architecture.md` 发行通道协议一节）。
  另有第三方 patch：`app-builder-lib`、`open`、`dshmarket@1.38.1`（patch 文件 `.yarn\patches\dshmarket-npm-1.38.1-775916754e.patch`）、`fs-ext`、`pnpm`、`@vscode/ripgrep`（源 patch 文件在 `$ROOT\patches\`）。
- `scripts`（第 1156–1190 行）：
  - `build` = 依次构建 `dsh-community-market` → `dsh-plugin-desktop` → `dsh-plugin-desktop-beta`。
  - `dev`/`start`/`package:dir`/`dist:mac`/`dist:win`/`dist:win-portable` 及 `:beta` 变体；均先跑 `aa:prepare-release`（`scripts\prepare-agents-anywhere-release.mjs`）与 market build。
  - `upstream:install`/`build`/`build:official`/`pack:dsh`/`prepare-runtime`：`cd deepseek-harness && corepack pnpm …` —— 产品构建消费**检入的官方 runtime tarballs**（`upstream.json` 记录 commit 与版本），不直接 link 源码检出。
  - 质量门：`check:bilingual-docs`（`scripts\bilingual-docs.mjs` + `verify-bilingual-docs.mjs`）、`check:architecture`（market 依赖方向 `scripts\verify-market-dependency-direction.mjs`）、`check:vendored-runtime`（`scripts\sync-vendored-runtime.mjs --check`，stable+beta 双通道）、`check:desktop-variants`（`scripts\verify-desktop-variants.mjs`，强制 stable/beta 共享功能同步）、`check:layout`（`scripts\verify-layout.mjs`）。

### 1.2 双通道 pin（`$ROOT\upstream.json`）

```jsonc
{ "repository": "https://github.com/deepseek-ai/deepseek-harness.git",
  "activeChannel": "beta",
  "channels": {
    "stable": { "commit": "fb2c4b9e698e30edb738bca4cf0618587db7d203",
                "sourceVersion": "0.1.5-rc.2", "runtimePackageVersion": "0.1.5-rc.2",
                "runtimeSource": "vendor/dsh-runtime/0.1.5-rc.2/manifest.json",
                "package": "dsh-plugin-desktop" },
    "beta":   { "commit": "0a15e36e7f82b6ed45af6fa9759f29b40dcd965d",
                "sourceVersion": "0.1.6-alpha.1", "runtimePackageVersion": "0.1.6-alpha.1",
                "runtimeSource": "vendor/dsh-runtime/0.1.6-alpha.1/manifest.json",
                "package": "dsh-plugin-desktop-beta" } } }
```
stable/beta 是**两个实体 npm 包 + 两个系统应用**（app id `ai.deepseek.dsh.desktop` / `ai.deepseek.dsh.desktop.beta`），不由 Git 分支区分（`docs\architecture.md`）。

### 1.3 顶层目录与角色

| 目录 | 角色 |
| --- | --- |
| `deepseek-harness\` | 上游 monorepo（`@deepseek-ai/dsh-root@0.1.6-alpha.1`，pnpm 11.7.0）。产品代码不修改它；升级走 vendored tgz 重打包。 |
| `dsh-plugin-desktop\` | 产品本体：Electron 壳 + Cordis 插件（§3）。npm 包 `dsh-plugin-desktop@2.0.11`。 |
| `dsh-plugin-desktop-beta\` | beta 通道并行包，`cordis.patch.yml` 与 stable 版逐行同构、仅包名不同（已核对两文件）。 |
| `dsh-community-fabric\` | 社区互操作标准 RFC 工作区（**只有文档，无运行时/SDK/schema 发布**），见 §4.6。 |
| `dsh-community-market\` | 内置插件市场（`dsh-community-market@0.1.0-dev.0`，private）：catalog 源适配、Host 路由、client UI，见 §4.6。 |
| `assets\` | 仓库 README 截图/赞助商图。 |
| `docs\` | 产品文档（架构、插件开发、生态倡议、用户指南、FAQ、why-desktop、evidence/），见 §6。 |
| `patches\` | yarn patch 源文件（app-builder-lib、open、fs-ext、pnpm、vscode-ripgrep）。 |
| `scripts\` | 仓库级门与发布准备脚本（见 §1.1）。 |
| `vendor\` | `dsh-runtime\{0.1.5-rc.2,0.1.6-alpha.1}\`（上游预构建 tarball + `manifest.json`）、`agents-anywhere\`。 |
| `_deprecated\` | 弃置区。 |
| `.agents\` | 工程决策笔记（`notes\implemented\architecture\2026-08-*.md`）。 |
| `.yarn\` | Yarn 4 缓存/patches。 |

---

## 2. `deepseek-harness` 包（上游）

### 2.1 自身布局

`$ROOT\deepseek-harness\package.json`：`name: @deepseek-ai/dsh-root@0.1.6-alpha.1`，`type: module`，workspaces：
`vendor/*`、`packages/*/*`、`native/system`、`native/system/packages/*`、`apps/*`、`website`。

- `apps\`：
  - `cli\` —— **`dsh` 启动器**（`src\bin.ts`、`src\profile-boot.ts`、`src\plugin.ts`（`dsh plugin` 命令实现）、`src\dump-config.ts`（`--dump-config`）、`src\args.ts`、`src\process-shutdown.ts`）。自带 `composition.md`（由 `scripts/gen-doc-graphs.ts` 生成的 dsh-base 组合图，§2.3 全文引用）和 `config\examples\`（`cordis/`、`github-review/`、`mcp-memory/`、`schedule/` 四组 cordis.yml 示例）。
  - `web\` —— **Web 壳 Vite 入口**（`index.html` 含 `#root` + `/src/main.ts`、`src\preview.ts` worker 预览页、`public\manifest.webmanifest` PWA 清单、大量 e2e 测试）。构建产物即 `@deepseek-ai/dsh-web-frontend` dist（上游脚本 `build:web` = `pnpm --filter @deepseek-ai/dsh-web-frontend build`）。
  - `desktop\` —— **上游自己的 Electron 应用**（`src\{main,ipc,preload,preload-app,host-process,host-protocol,backend-controller,single-instance,...}.ts`、`renderer\{plugin-manager,startup}.html`、`scripts\`（electron-builder、macOS 打包/公证、Windows 签名、NSIS、auto-update 环境、upload plan））。按上游文档它使用 `dsh-app://` 协议 + file:// + IPC，**不开 Web server**（`packages\host\webserver\src\index.ts` 头注："Electron uses file:// plus IPC instead, and this package never prints the URL"）。**产品壳 `dsh-plugin-desktop` 不复用它**，见 §3.5 边界。
  - `desktop-host\` —— 上游 desktop 的宿主补丁（`config\desktop.cordis.patch.yml` + `src\{index,wire}.ts`）。
- `packages\<group>\<pkg>\`（`@deepseek-ai/dsh-<pkg>`；完整 group 表见上游 `AGENTS.md`，48 个 group）：
  - `boot\app-boot`（`dsh-app-boot`：profile 解析、bundle 层叠、patch 应用、启动；`src\profile.ts`、`src\profile-resolution\`）、`boot\cmdline`（`cmdlineArgs` 服务）。
  - `bundle\{base,web-app,headless,sdk-app,sdk-minimal,acp-app}` —— 六个发行 bundle，各自 `cordis.patch.yml`（§4.2）。
  - `core\{agent,agent-loop,session,scope,tools,system-prompt,agent-default-model}`、`llm\*`（llm、llm-deepseek、llm-retry、llm-pi-ai、deepseek-llm-api-extensions）、`shell\*`（shell-env）、`subprocess\*`（`subprocess-local`、`win32-process`，node-pty）、`terminal\*`（PTY seam，`packages\terminal\terminal\package.json` 描述："Persistent PTY session seam … owner-scoped ids, backend registry"）、`sandbox\*`（`sandbox-local`、`sandbox-policy`、`sandbox-windows-acl`、`ssh`、`bash-sandbox`、`pwsh-sandbox`）、`fs\*`（fs-sandbox、fs-observation-policy、tool-fs、tool-fs-search）、`session\*`（persistence-jsonl、projection、projection-cache、title、title-first-prompt-llm、query-sqlite、checkpoint-policy、telemetry-otel）、`settings\settings-file`、`credentials\credentials-local`、`storage\*`（storage、storage-json、storage-domain）、`subagent\*`（subagent、spawn/fork-in-process、tool-subagent(-control/-fork)）、`jobs\jobs-local`、`mcp\*`（mcp-resources 等）、`skill\*`（skill、skill-filesystem、skill-badge、tool-skill）、`goal\*`（goal、goal-round-driver、tool-goal、command-goal）、`plan\plan-mode`、`workflow\*`（workflow-ptc、tool-workflow）、`webhook\*`、`interaction\*`（`user-approval`、`user-questions`、`tool-ask-user`、`permission-presets`）、`api\*`（`api-gateway` Typert Remote gateway + session/terminal/workspace-files/settings controllers）、`typert\*`（typert-registry、typert-loader）、`host\*`（`webserver`、`frontend-static`、`plugin-inventory`、`plugin-package-inventory-deepseek`、`open-in-app`、directory-picker 族）、`client\*`（约 60 个 UI 包，§5.5）、`extensions\*`（`cordis-host-runner`、`cordis-client-runner`、`tool-cordis`、`ui-cordis` —— 动态插件运行面，§4.5）、`preset\{agent-presets,persona}`、`sdk\*`、`acp`、`hooks\*`、`computer-use`、`browser-use`、`compaction\*`（basic、tool-result-pruner、image-offload）、`context\*`、`attachment\attachment-local`、`spill\*`、`todo\tool-todo`、`goal\*`、`schedule\*`、`experimental\*`、`util\*`、`test-support`、`runtime-diagnostics` 等。
- `vendor\` —— **vendored Cordis 框架源码**：`cordis`、`loader`、`include`、`hmr`、`timer`、`group`、`logger-console`、`cosmokit`、`schemastery`（`vendor\README.md` 记录同步流程；发布包 `@deepseek-ai/cordis@4.0.2` 的 repository.directory 就是 `vendor/cordis`）。
- `native\system\` —— koffi 系 node addon 工程（Windows ACL 等原生能力）。
- 其他：`python\`（Python SDK）、`benchmarks\`、`website\`（VitePress）、`docs\`（§6）、`.agents\`。

### 2.2 启动流程（Host 进程）

1. 入口是 `dsh` CLI（`apps\cli\src\bin.ts`）；`dsh web` 是 `--profile web` 的别名。只有 `dsh` profile 能启动受支持 Node 应用（`AGENTS.md` "Application launch" 规则："package bins, demos, and public SDK argv escapes are forbidden"）。
2. `@deepseek-ai/dsh-app-boot`（`packages\boot\app-boot\src\profile.ts`、`src\profile-resolution\`）：解析 `$DSH_HOME/profiles/<name>`（profile = package.json 的 `dsh.profile.bundles` + 目录内 `cordis.patch.yml` + `patchReload: live|startup`），按层叠顺序（§4.2）组合 Loader 树，计算一次不可变的包解析 generation（link/runtime/dual 三种模式，`ctx.pluginPackages` 暴露解析结果），然后挂载全部插件行。
3. **服务模型**：没有独立"service registry"文件 —— 注册表就是 Cordis `Context`（`vendor/cordis/src/{context,service,registry,fiber,events}.ts`）。插件以 `ctx.<key>` 声明服务（如 `ctx.tools`、`ctx.llm`、`ctx.sessions`、`ctx.webServer`、`ctx.settings`），依赖通过 `inject: [...]` 声明，加载顺序由服务依赖推导而非手工排序（`docs/cordis-primer.md`："load order is expressed through service requirements rather than manual boot sequencing"）。所有注册都是可回滚 effect（`ctx.effect()`/`ctx.on()`），fiber dispose 时按序解除。
4. 全局 required 行（缺失即整 app dispose + 非零退出）：`agent-loop`、`webserver`、`modules`、`connection`、`headless-runner`、`acp`、`sdk-jsonrpc-server`（`packages\boot\app-boot\README.md`）。

### 2.3 dsh-base 服务目录（全 profile 共享首层）

来源：`$ROOT\deepseek-harness\apps\cli\composition.md`（由 `scripts/gen-doc-graphs.ts` 从 `packages\bundle\base\cordis.patch.yml` 生成，共 87 行插件，逐行列出）：

| plugin id | package |
| --- | --- |
| `timer` | `@deepseek-ai/cordis-plugin-timer` |
| `hmr` | `@deepseek-ai/cordis-plugin-hmr` |
| `llm` | `@deepseek-ai/dsh-llm` |
| `deepseek-llm-api-extensions` | `@deepseek-ai/dsh-deepseek-llm-api-extensions` |
| `session` | `@deepseek-ai/dsh-session` |
| `session-log-deepseek` | `@deepseek-ai/dsh-session-log-deepseek` |
| `typert` | `@deepseek-ai/dsh-typert-registry` |
| `typert-loader` | `@deepseek-ai/dsh-typert-loader` |
| `typert-gateway` | `@deepseek-ai/dsh-api-gateway` |
| `session-title` | `@deepseek-ai/dsh-session-title` |
| `session-title-llm` | `@deepseek-ai/dsh-session-title-first-prompt-llm` |
| `user-questions` | `@deepseek-ai/dsh-user-questions` |
| `agent` | `@deepseek-ai/dsh-agent` |
| `plugin-package-inventory-deepseek` | `@deepseek-ai/dsh-plugin-package-inventory-deepseek` |
| `agent-default-model` | `@deepseek-ai/dsh-agent-default-model` |
| `jobs` | `@deepseek-ai/dsh-jobs-local` |
| `llm-retry` | `@deepseek-ai/dsh-llm-retry` |
| `settings` | `@deepseek-ai/dsh-settings-file` |
| `credentials` | `@deepseek-ai/dsh-credentials-local` |
| `llm-pi-ai` | `@deepseek-ai/dsh-llm-pi-ai` |
| `session-persistence-jsonl` | `@deepseek-ai/dsh-session-persistence-jsonl` |
| `attachment-local` | `@deepseek-ai/dsh-attachment-local` |
| `session-query-sqlite` | `@deepseek-ai/dsh-session-query-sqlite` |
| `session-projection` | `@deepseek-ai/dsh-session-projection` |
| `storage` | `@deepseek-ai/dsh-storage` |
| `storage-json` | `@deepseek-ai/dsh-storage-json` |
| `storage-domain` | `@deepseek-ai/dsh-storage-domain` |
| `session-projection-cache` | `@deepseek-ai/dsh-session-projection-cache` |
| `session-telemetry-otel` | `@deepseek-ai/dsh-session-telemetry-otel` |
| `subprocess` | `@deepseek-ai/dsh-subprocess-local` |
| `sandbox` | `@deepseek-ai/dsh-sandbox-local` |
| `sandbox-policy` | `@deepseek-ai/dsh-sandbox-policy` |
| `bash-sandbox` | `@deepseek-ai/dsh-bash-sandbox` |
| `pwsh-sandbox` | `@deepseek-ai/dsh-pwsh-sandbox` |
| `approval` | `@deepseek-ai/dsh-user-approval` |
| `permission` | `@deepseek-ai/dsh-permission-presets` |
| `shell-env` | `@deepseek-ai/dsh-shell-env` |
| `tool-bash` | `@deepseek-ai/dsh-tool-bash` |
| `tool-pwsh` | `@deepseek-ai/dsh-tool-pwsh` |
| `tool-jobs` | `@deepseek-ai/dsh-tool-jobs` |
| `fs-observation-policy` | `@deepseek-ai/dsh-fs-observation-policy` |
| `tool-fs` | `@deepseek-ai/dsh-tool-fs` |
| `tool-fs-search` | `@deepseek-ai/dsh-tool-fs-search` |
| `agent-instructions` | `@deepseek-ai/dsh-agent-instructions` |
| `skill` | `@deepseek-ai/dsh-skill` |
| `skill-filesystem` | `@deepseek-ai/dsh-skill-filesystem` |
| `skill-badge` | `@deepseek-ai/dsh-skill-badge` |
| `tool-skill` | `@deepseek-ai/dsh-tool-skill` |
| `commands` | `@deepseek-ai/dsh-commands` |
| `command-feedback` | `@deepseek-ai/dsh-command-feedback` |
| `goal` | `@deepseek-ai/dsh-goal` |
| `goal-round-driver` | `@deepseek-ai/dsh-goal-round-driver` |
| `command-goal` | `@deepseek-ai/dsh-command-goal` |
| `plan-mode` | `@deepseek-ai/dsh-plan-mode` |
| `token-meter` | `@deepseek-ai/dsh-token-meter` |
| `compaction-basic` | `@deepseek-ai/dsh-compaction-basic` |
| `command-compact` | `@deepseek-ai/dsh-command-compact` |
| `subagent` | `@deepseek-ai/dsh-subagent` |
| `subagent-spawn-in-process` | `@deepseek-ai/dsh-subagent-spawn-in-process` |
| `subagent-fork-in-process` | `@deepseek-ai/dsh-subagent-fork-in-process` |
| `tool-subagent-control` | `@deepseek-ai/dsh-tool-subagent-control` |
| `tool-subagent-list-agents` | `@deepseek-ai/dsh-tool-subagent-control/list-agents` |
| `tool-subagent` | `@deepseek-ai/dsh-tool-subagent` |
| `tool-subagent-fork` | `@deepseek-ai/dsh-tool-subagent` |
| `ptc-runtime` | `@deepseek-ai/dsh-ptc-runtime-node` |
| `workflow-ptc` | `@deepseek-ai/dsh-workflow-ptc` |
| `tool-workflow` | `@deepseek-ai/dsh-tool-workflow` |
| `timeout-policy` | `@deepseek-ai/dsh-tool-call-timeout-policy` |
| `spill-local` | `@deepseek-ai/dsh-spill-local` |
| `spill-policy` | `@deepseek-ai/dsh-spill-policy` |
| `session-checkpoint-policy` | `@deepseek-ai/dsh-session-checkpoint-policy` |
| `tool-result-pruner` | `@deepseek-ai/dsh-compaction-tool-result-pruner` |
| `image-offload` | `@deepseek-ai/dsh-compaction-image-offload` |
| `tool-todo` | `@deepseek-ai/dsh-tool-todo` |
| `tool-goal` | `@deepseek-ai/dsh-tool-goal` |
| `tool-ralph` | `@deepseek-ai/dsh-tool-ralph` |
| `repeat-tool-reminder` | `@deepseek-ai/dsh-repeat-tool-reminder` |
| `web` | `@deepseek-ai/dsh-web` |
| `web-search-deepseek` | `@deepseek-ai/dsh-web-search-deepseek` |
| `web-fetch-http` | `@deepseek-ai/dsh-web-fetch-http` |
| `tool-web` | `@deepseek-ai/dsh-tool-web` |
| `mcp-resources` | `@deepseek-ai/dsh-mcp-resources` |
| `tools` | `@deepseek-ai/dsh-tools` |
| `system-prompt` | `@deepseek-ai/dsh-system-prompt` |
| `agent-loop` | `@deepseek-ai/dsh-agent-loop` |
| `fs-sandbox` | `@deepseek-ai/dsh-fs-sandbox` |
| `llm-deepseek` | `@deepseek-ai/dsh-llm-deepseek` |

web 面再叠加 `dsh-web-app` bundle（`packages\bundle\web-app\cordis.patch.yml`，491 行，行清单逐行列出；行号引该文件）：

| 行号 | 行 id | 性质/备注 |
| --- | --- | --- |
| L16 | `system-prompt` | patch base 行（叠加 web-surface 段等） |
| L27 | `session-query-sqlite` | patch base 行 |
| L32 | `tools` | patch base 行 |
| L44 | `- insert:`（host 行组） | 以下 L47–L126 行均为此 insert 内的新行 |
| L47 | `subagent-model-selection-settings` | host 行 |
| L50 | `message-feedback` | host 行 |
| L56 | `session-log-download` | host 行 |
| L62 | `open-in-app` | host 行 |
| L69 | `ui-open-in-app` | client 行 |
| L72 | `workspace` | host 行 |
| L75 | `session-reference` | host 行 |
| L78 | `file-reference-local` | host 行 |
| L83 | `session-stats` | host 行 |
| L88 | `session-turn-outline` | host 行 |
| L94 | `directory-picker` | host 行 |
| L98 | `plugin-inventory` | host 行 |
| L102 | `session-controller` | api controller（session） |
| L107 | `terminal-controller` | api controller（terminal） |
| L109 | `workspace-files` | api controller（workspace files） |
| L114 | `settings-controller` | api controller（settings） |
| L118 | `workspace-controller` | api controller（workspace） |
| L121 | `cordis-host-runner` | 动态插件 host 运行面 |
| L126 | `web-startup` | 提供 `webStartup`（`--host`/`--port`） |
| L134 | `webserver` | `host: !!js ctx.webStartup.host ?? '127.0.0.1'`、`port: !!js ctx.webStartup.port ?? 3080` |
| L153 | `web-runtime` | frontend dist 挂载、URL 打印/开浏览器、`webRuntime`（被 desktop 层整 config 替换） |
| L166 | `client-hmr` | client bundle 热替换 node 半边 |
| L175 | `modules` | `__DSH_BOOT__` 合成 + `/plugins/<id>/client.js`（required） |
| L180 | `connection` | Typert gateway 绑定 `/api`；注入 `webRuntime.trustedHosts`（required） |
| L191 | `file-upload` | Blob/ReadableStream 上传 |
| L194 | `api-remotes` | 浏览器侧 remote 代理 |
| L197 | `cordis-client-runner` | 动态插件 client 运行面 |
| L200 | `ui-theme` | 主题设置 + boot palette 注入 |
| L203 | `locale` | 客户端 locale |
| L206 | `ui-layout` | `AppFrame` 占 `root`，声明 sidebar/main/rightbar/shell.overlay |
| L209 | `ui-renderer` | renderer 接管 + 注册表 |
| L212 | `ui-session` | 会话面 |
| L216 | `resources` | 资源协议 |
| L219 | `ui-sidebar` | 占 `sidebar` |
| L223 | `ui-sidebar-right` | sidebar 右列扩展 |
| L229 | `ui-sidebar-documentpreview` | 文档预览 |
| L233 | `ui-sidebar-terminal` | 终端 sidebar |
| L235 | `ui-sidebar-files` | 文件 sidebar |
| L238 | `ui-settings` | 设置页 |
| L241 | `ui-settings-general` | 通用设置卡 |
| L244 | `ui-settings-models` | 模型设置卡 |
| L247 | `ui-settings-plugin-inventory` | 插件清单设置卡 |
| L250 | `ui-settings-unarchive-sessions` | 恢复会话设置卡 |
| L253 | `ui-conversation` | 会话容器 |
| L256 | `ui-approval` | 审批卡 |
| L259 | `ui-chat` | 聊天节点/命令/turnTail |
| L263 | `ui-brand-official` | 官方品牌 |
| L266 | `ui-attachment` | 附件呈现 |
| L270 | `ui-tool` | 工具卡片 |
| L273 | `ui-cordis` | 动态插件运行卡片 |
| L278 | `ui-workflow-run` | 工作流运行面 |
| L283 | `ui-deliverables` | 交付物面 |
| L287 | `ui-workspace` | 工作区面 |
| L292 | `ui-input-trigger` | 输入触发 |
| L295 | `ui-commands` | 命令面 |
| L298 | `ui-skill` | skill 面 |
| L301 | `ui-subagent` | 子代理面 |
| L304 | `ui-reference` | 引用面 |
| L310 | `ui-schedule` | 计划面 |
| L315 | `ui-jobs` | jobs 面 |
| L319 | `ui-goal` | goal 面 |
| L325 | `ui-message-feedback` | 消息反馈 |
| L329 | `ui-model-selection` | 模型选择 |
| L332 | `ui-permission` | 权限面 |
| L337 | `ui-agent-preset` | 预设面 |
| L342 | `ui-settings-plugins` | 插件管理设置卡 |
| L346 | `ui-plan` | plan 面 |
| L349 | `ui-user-questions` | 用户提问面 |
| L352 | `ui-trajectory` | 轨迹面 |
| L372 | `tool-bash` | base 行覆盖 |
| L375 | `tool-pwsh` | base 行覆盖 |
| L388 | `tool-jobs` | base 行覆盖 |
| L391 | `tool-fs` | base 行覆盖 |
| L394 | `tool-fs-search` | base 行覆盖 |
| L406 | `skill-filesystem` | base 行覆盖 |
| L409 | `tool-skill` | base 行覆盖 |
| L415 | `command-goal` | base 行覆盖 |
| L418 | `tool-goal` | base 行覆盖 |
| L421 | `plan-mode` | base 行覆盖 |
| L431 | `compaction-basic` | base 行覆盖 |
| L434 | `command-compact` | base 行覆盖 |
| L437 | `tool-result-pruner` | base 行覆盖 |
| L447 | `tool-subagent-control` | base 行覆盖 |
| L450 | `tool-subagent-list-agents` | base 行覆盖 |
| L453 | `tool-subagent` | base 行覆盖 |
| L456 | `tool-subagent-fork` | base 行覆盖 |
| L459 | `workflow-ptc` | `disabled: true` |
| L462 | `tool-workflow` | `disabled: true` |
| L468 | `tool-ralph` | `disabled: true`（注释：preset-plane gate 读行 id 不看 disabled，此行必须保留以把 `tool-ralph` 挡在 host 面外） |
| L471 | `agent-instructions` | `disabled: true` |
| L474 | `tool-todo` | `disabled: true` |
| L477 | `tool-web` | `disabled: true` |
| L487 | `- insert:` `agent-presets` | `name: '@deepseek-ai/dsh-agent-presets'`，`config.default: standard`（shipped presets 为只读 `system` root，`$DSH_HOME/.agent-presets` 为用户 root，"a preset IS a composition"） |

### 2.4 Web GUI server（`dsh web`）

- **传输**：`packages\host\webserver\src\index.ts`（`@deepseek-ai/dsh-host-webserver`）—— 纯 `node:http` 路由注册器（exact/prefix 路由 + upgrade 路由 + gzip + 一个 fallback seat），"knows no harness concepts, serves no files"，由组合方持有 dist。`Config: {host: '127.0.0.1' | '0.0.0.0', port, compression}`。
- **webserver 行配置**（`packages\bundle\web-app\cordis.patch.yml` 第 134–142 行）：`host: !!js ctx.webStartup.host ?? '127.0.0.1'`、`port: !!js ctx.webStartup.port ?? 3080`（`webStartup` 由 `@deepseek-ai/dsh-web-app/startup`（`packages\bundle\web-app\src\startup.ts`）从 CLI flag 提供；LAN 绑定 `0.0.0.0` 只在显式 trustedHosts 确认后）。
- **web-runtime 行**（同文件第 153–160 行；实现 `packages\bundle\web-app\src\index.ts`）：解析 frontend dist（`@deepseek-ai/dsh-web-frontend` 包旁 `dist/index.html`，第 162–170 行），经 fallback seat 挂 `FrontendStatic`，注册 `app:web-surface` system-prompt 段与 `DSH_WEB_URL` shell 环境变量（第 236–249 行），打印 URL，必要时打开默认浏览器；bind 后采样 LAN trust 并 provide `webRuntime`。
- **`window.__DSH_BOOT__` 注入**：由 `@deepseek-ai/dsh-client-modules` 的 node 半边完成（`packages\client\modules\src\index.ts`，1003 行）—— 扫描 host Loader 里所有声明 `dsh.client` 的包，按 module-graph 顺序合成 boot entry graph，经 `webserver/index-inject` 事件（`packages\host\webserver\src\injections.ts`，每次 index 渲染时 emit `table: IndexInjection[]`，逐行读取最新值）注入到渲染的 index 中（`src/index.ts` 第 474 行 `rows.push({kind:'global',name:'__DSH_BOOT__',value:graph})`）；同时 serve `/plugins/<id>/client.js` combo 脚本 + sourcemap；增量扫描（每个 cordis `internal/plugin` 发射标脏该 fiber entry，microtask flush 调和）。
- **客户端内核**：`packages\client\web\src\boot.ts`（110 行）的 `AppWebEntry.run()`（第 46–92 行）—— 等 `__DSH_BOOT_READY__` → 取 `window.__ModuleLoader__` → `moduleLoader.create({boot: win.__DSH_BOOT__, staticModules, loadBundle})` → 预取 immediate tier → `bootClient`（浏览器侧 Cordis Context 逐行激活 client 行，boot 页显示每行状态）→ `mountClient(ctx, container)` 把挂载点交给 UI renderer；失败渲染在 boot 页上。
- LAN 暴露：`trustedHosts` 信任围栏（web-app patch 第 180–187 行，connection 行注入 `webRuntime.trustedHosts`）。

### 2.5 上游 Electron 主进程（`apps/desktop`）

`src\main.ts` + `host-process.ts`：Electron 作为**父进程** spawn `dsh` Host 子进程（`host-protocol.ts` 定义 IPC 协议），`preload.ts`/`preload-app.ts` 暴露受限 IPC；`single-instance.ts` 单实例；`renderer\plugin-manager.html|startup.html` 是独立启动/插件管理页面；`scripts\` 含 auto-update 环境（`desktop-auto-update-environment.mjs`）、macOS 打包/公证（`package-macos.ts`、`notarize-macos-disk-images.mjs`）、Windows 签名（`windows-sign.mjs`/`.cmd`）、NSIS（`installer.nsh`）、上传计划（`upload-target.ts`）。**注意**：这是上游形态；产品壳（§3）选择另一条路 —— 把 Host 直接跑进 Electron 主进程内（in-process），UI 走 Web carrier，不复用 `dsh-app://`/IPC 体系。两条路线并存于仓库，但产品发行的是后者（根 scripts 只构建/发行 `dsh-plugin-desktop*`）。

---

## 3. `dsh-plugin-desktop` 包（产品壳）

### 3.1 包身份（`$ROOT\dsh-plugin-desktop\package.json`，422 行）

- `name: dsh-plugin-desktop@2.0.11`；`main: lib/main.js`；`bin: dsh-desktop / dsh-plugin-desktop → lib/bin.js`。
- `exports` 即公开 contract 面：`.`、`./profile`、`./client`、`./webserver`、`./windows-pwsh-sandbox`、`./terminal`、`./pnpm`、`./profile-service`、`./desktop-plugins`、`./profiles`、`./diagnostics`、`./notifications`、`./updates`。
- **`dsh` 字段（Cordis 插件 + bundle 声明）**：
  ```jsonc
  "dsh": {
    "client": { "platform": "web",
      "inject": ["@deepseek-ai/dsh-api-remotes","@deepseek-ai/dsh-client-connection",
                 "@deepseek-ai/dsh-client-locale","@deepseek-ai/dsh-client-ui-renderer",
                 "@deepseek-ai/dsh-client-ui-settings","@deepseek-ai/dsh-client-ui-theme"] },
    "bundle": { "patch": "./cordis.patch.yml" }
  }
  ```
  即：这个 npm 包同时是一个 **bundle**（`dsh.bundle.patch`）与一个 **client 包**（`dsh.client`，浏览器半边注入 6 个官方 client 服务）。
- 关键依赖：`electron@43.3.0`（peer）、`dsh-community-market@0.1.0-dev.0`（workspace）、`dshmarket@1.38.1`（被 patch 的第三方市场运行时）、`pnpm@11.8.0`（内置 pnpm）、`koffi@3.1.5`、`fs-ext@2.1.1`、`selfsigned@5.5.0`（LAN HTTPS 本地 CA）、`react@18.3.1` + `sonner`（native-ui 用）、`adm-zip`、`node-addon-require-builtin`；以及 ~100 个精确 pin 的 `@deepseek-ai/dsh-*@0.1.5-rc.2`（stable 通道）。
- `electron-builder` 配置内嵌 package.json：macOS dmg、Windows NSIS（`installer.nsh`，oneClick:false、perMachine:false）与 Portable、`@electron/fuses`（`afterAllArtifactBuild: ./scripts/verify-electron-fuses.ts`）。
- 构建：`tsdown`（主进程 + 子入口）+ `vite build --config vite.native-ui.config.ts`（native-ui 独立 HTML 应用）+ `scripts\prepare-fs-ext.ts`（`prepare:electron-native`）+ 图标生成脚本（`generate-{windows,mac}-app-icon.mjs`、`generate-tray-icons.mjs`）。
- 测试面（`check:win-package`/`check:mac-package`）：package/installer/update/safe-mode/windows-pwsh-sandbox/window-options 等 vitest 用例 + `verify:closure`（运行时闭包校验）。

### 3.2 组合声明（`$ROOT\dsh-plugin-desktop\cordis.patch.yml`，28 行，全文）

```yaml
- insert:
    - id: desktop-shell         # name: dsh-plugin-desktop, config: { mode: compatibility }
    - id: desktop-terminal      # dsh-plugin-desktop/terminal; disabled: !!js process.platform === 'linux'
    - id: desktop-diagnostics   # dsh-plugin-desktop/diagnostics
    - id: desktop-notifications # dsh-plugin-desktop/notifications
    - id: desktop-pnpm          # dsh-plugin-desktop/pnpm
    - id: desktop-profiles      # dsh-plugin-desktop/profiles
    - id: desktop-updates       # dsh-plugin-desktop/updates
- id: web-runtime
  config: { openBrowser: false, printUrl: false, surfaceContext: true, trustedHosts: [] }
```
- 7 行 desktop 插件插入官方 web bundle 之上；`web-runtime` 行被**整体替换**（patch 语义 = 整 config 替换），关掉开浏览器/打印 URL。
- `dsh-plugin-desktop\README.md`：launcher 在**每次 generation** 把这一层插在 `dsh-web-app` 之后，且**从不持久化**进所选 profile 的 bundle 列表 —— 桌面层是运行时注入，不污染用户 profile。
- beta 版 `dsh-plugin-desktop-beta\cordis.patch.yml` 与之逐行相同（仅包名 `dsh-plugin-desktop-beta`），由 `scripts\verify-desktop-variants.mjs` 强制同步（`check:desktop-variants` 门）。

### 3.3 贡献了什么（Electron main 侧，`src\` 约 120 个 TS 文件）

按职责分组：

- **进程/启动**：`main.ts`（1843 行可执行入口：单实例锁 → 解析 profile → 安装运行时环境 → `boot()` 启动 Host Cordis 根；imports 自 `@deepseek-ai/dsh-app-boot` 的 `boot`、`installFailLoud`、`loadLayeredEnv`、`resolveProfileDir`）、`bin.ts`、`host-process.ts`（`startIsolatedDesktopHost`，把 Host 跑进主进程隔离域）、`host-bootstrap.ts`、`host-process-entry.ts`、`host-launch-environment.ts`、`startup-*.ts`、`safe-mode.ts`、`renderer-surface-watchdog.ts`。
- **窗口/托盘/菜单**：`electron-runtime.ts`（`ElectronDesktopRuntime`）、`electron-shell-generation.ts`（每次启动一个 `ElectronShellGeneration` 完整拥有 BrowserWindow/Tray/listener，`release()` 幂等释放，禁止跨 generation 缓存窗口对象/服务引用/子进程句柄 —— `docs\architecture.md` "原生 Shell generation" 节）、`electron-platform.ts`（`ElectronPlatformStrategy` 平台 seam：win32/darwin/linux adapter 各自声明目录选择、shell 切换、更新下载）、`window-{options,chrome,material}.ts`（36px 顶部 frame、mica/transparent/off 材质、拖动区）、`main-window-state.ts`、`local-window-policy.ts`、`auxiliary-window-options.ts`、`native-menu.ts`、`tray-icons.ts` + `tray-locale.ts`（托盘含 profile 选择器）。
- **三种呈现模式**（`dsh-plugin-desktop\client\`）：compatibility（官方 UI 原样 + 独立 36px Desktop frame，`compatibility-chrome.html` 校验页）、extended（`ExtendedFrame.tsx`/`ExtendedTitlebar.tsx`：Desktop 自注册 layout/sidebar surface 承载官方 occupant，倒 L 材质）、advanced（`AdvancedFrame.tsx`/`AdvancedShell.tsx`：独立 root registration + 紧凑内部 caption）。`client\layout-service.ts`、`layout-state.ts`、`window-service.ts`（`desktopWindow` client 服务）、`theme-presenter.ts`、`boot-health.ts`、`contracts.ts`。
- **独立原生窗口（native-ui，Vite 多页）**：`desktop-dialog-window.ts`（`DesktopDialogWindow`：独立沙箱化模态 BrowserWindow）；`native-ui\{desktop-dialog,profile-create,profile-selector,recovery,setup-wizard,compatibility-chrome}\*.html`（各带 `App.tsx`/`main.tsx`，共享 `shared\{DesktopFrame,ProfileSelector,RecoveryWindowPrimitives}.tsx` + `shared\theme.css`，shadcn 风格）—— 恢复、新建 profile、设置向导等**不进入 Web Client 组件树**的桌面级窗口（`docs\architecture.md`）。
- **profile 管理**：`profile-manager.ts`（list/select/create/delete，state 存 Electron userData）、`profile-service.ts`（`desktopProfiles` 服务）、`profiles.ts`、`profile-checkpoint.ts`（健康启动三槽轮转 checkpoint：profile 的 5 个声明文件 + `settings.yaml` + `cordis.patch.yml`；凭据/.env/session/存储永不入 checkpoint）、`profile-{create,selection}-window.ts`、`profile-materializer.ts`、`profile-channel-admission.ts`。
- **内置 pnpm / 插件安装**：`pnpm.ts`（`desktopPnpm` 服务：`run(argv)` 直接跑内置 pnpm；`runPlugin(args,cwd,signal)` 经打包 DSH CLI 维持 profile 初始化与 bundle reconcile；`installPlugin({invokingDir, recovery})`）、`pnpm-policy.ts`、`desktop-plugins.ts`（`DesktopPluginsService`，插件状态清理）。
- **终端**：`terminal.ts` + `desktop-terminal.ts`（托盘终端：隔离的命令行环境，注入 `DSH_DESKTOP_DEFAULT_PROFILE` 等环境变量、`ELECTRON_RUN_AS_NODE` 语义、默认 profile 解析；PTY 能力来自上游 `dsh-terminal`/`subprocess-local`(node-pty)）。
- **更新**：`update-checker.ts`（带 `X-DSH-Desktop-Channel: stable|beta` header 的版本检查）、`update-download.ts`（先原生"保存安装包"对话框，再请求固定下载地址）、`update-lifecycle.ts`（macOS 开 DMG / Windows 备 NSIS 并询问退出安装）、`updates.ts`。
- **网络**：`desktop-network.ts`（默认 loopback 随机端口，`dsh-desktop.port: 0`，`src\desktop-port.ts`；显式确认后 LAN 全接口）、`lan-{addresses,https-certificate,https-ingress,https-runtime}.ts`（LAN 暴露走本地自签 CA，`selfsigned`）、`desktop-lan-https-runtime.ts`。
- **设置**：`desktop-settings-{contract,controller,route}.ts`（`DESKTOP_SETTINGS_NAMESPACE` 设置命名空间 + `/api/desktop/*` webserver 路由，§3.4）。
- **诊断/恢复**：`diagnostics.ts` + `diagnostic-export.ts`（`dsh-desktop --export-diagnostics` 不启动 Host 直接导出诊断 ZIP）、`crash-evidence.ts`、`desktop-logger.ts`、`log-files.ts`、`startup-recovery-{controller,window}.ts`、`recovery-{copy,plugin-uninstall}.ts`、`session-projcache-recovery.ts`、`mask-secrets.ts`。
- **Windows 专属**：`windows-pwsh-sandbox.ts`（PowerShell 沙箱执行面，导出 `./windows-pwsh-sandbox`）、`windows-acl-runner.ts`（koffi 调 ACL）、`windows-console-host.ts`、`windows-volume-diagnostics.ts`、`asar-module-resolver-state.ts` + `packaged-filesystem-smoke.ts`（ASAR 与物理运行时入口校验）。
- **市场集成**：`desktop-market.ts`（机器级市场 provider 选择，§3.6）。
- **其他**：`remote-control-offer.ts`（手机远程预留面）、`workspace-admission.ts`、`module-resolution.ts` + `package-overlay.ts`（profile 包解析安装）、`setup-wizard-*.ts`、`electron-reveal.ts`、`file-exporter.ts`、`file-path-bridge-contract.ts`、`directory-picker-{contract,route}.ts`、`native-dialog-copy.ts`、`compatibility-{chrome-contract,preload,shell}.ts`（兼容模式渲染进程校验）、`product-identity.ts`、`dsh-product-version.ts`、`desktop-installation-id.ts`。

### 3.4 Desktop 私有 webserver 路由（`src\desktop-settings-contract.ts`，191 行，同 origin 仅供捆绑渲染器）

| 路径 | 用途 |
| --- | --- |
| `GET /api/desktop/settings` | 读当前 Desktop 设置状态（profile 视图、market 视图、web 视图：`localUrl`/`lanUrls`/`lanState: inactive|starting|ready|failed`/`lanCaFingerprint`/`lanCaUrls`） |
| `POST /api/desktop/profiles/create` | 创建一个安全 Web profile（不选中） |
| `POST /api/desktop/profiles/select` | 为下一个 generation 选中一个兼容 profile（`DesktopSettingsProfileView: {name, exists, webCapable, selectable, deletable}`） |
| `POST /api/desktop/profiles/delete` | 删除一个非活动、用户创建的 Web profile |
| `POST /api/desktop/aa/select` | 持久化 Agents-Anywhere provider 选择 |
| `POST /api/desktop/market/select` | 持久化市场 provider（`'disabled' \| 'community-market' \| 'dsh-market'`） |
| `POST /api/desktop/terminal/open` | 打开 launcher 拥有的 DSH 终端（不接受命令文本） |
| `POST /api/desktop/restart` | 确认后排队一次有序重启 |
| `POST /api/desktop/restart/recovery` | 排队重启并在 Host 启动前打开恢复助手 |
| `POST /api/desktop/developer/reload` | 经 launcher 重载渲染器（不暴露 Electron API） |
| `POST /api/desktop/developer/devtools` | 切换已挂载窗口的 DevTools |
| `POST /api/desktop/updates/check` | 运行 generation 拥有的手动更新检查 |
| `POST /api/desktop/diagnostics/export` | 经 launcher 导出诊断归档 |

这些路由全部由 `desktop-shell` 行（`src\index.ts`，519 行）经 `webServer` 服务注册（`src\desktop-settings-route.ts` 提供 handler），是"渲染器只与 loopback carrier 说话"这一边界的具体体现。

### 3.5 与 core 的边界（`dsh-plugin-desktop\docs\plugin-services.md`，330 行，全文已读）

- **公开 contract（第三方插件可用）**：
  - Host 服务 `ctx.desktopProfiles`（`current`/`list()`/`select()`，导出面 `dsh-plugin-desktop/profile-service`）
  - Host 服务 `ctx.desktopPnpm`（`run()`/`runPlugin()`/`installPlugin()`，单 generation 单操作，导出面 `dsh-plugin-desktop/pnpm`）
  - Client 服务 `ctx.desktopWindow`（`mode`/`platform`/`material`/`micaSupported`/`safeAreaInsets`/`dragRegion`，导出面 `dsh-plugin-desktop/client`，仅浏览器侧 inject）
- **私有（不是 API）**：`desktopRuntime`（launcher 私有的 native adapter）、`desktopPnpmBootstrap`、Electron executable/Node helper/ABI 环境、生成 shim、state 文件格式、Loader 行序、Electron 实现细节（"Stability boundary" 节明示可随时变）。
- **渲染器边界**：renderer 是沙箱化 Web 渲染进程，**没有 preload、没有 Electron IPC 桥**；浏览器侧插件继续用普通 DSH 的 routes/RPC/client metadata/services/slots（`docs\architecture.md`、`README.md`）。
- 文档同时给出两个模式范本（`docs\plugin-development.md`）：纯 Desktop 插件直接 `inject: ['desktopProfiles','desktopPnpm']`；双栖插件先判 `ctx.get('desktopProfiles') === undefined`，再用嵌套 `ctx.inject([...])` 让 adapter 随 generation 一起卸载（兼容纯 `dsh web` 环境）。

### 3.6 市场 provider 与 beta

- `src\desktop-market.ts`：机器级 `DesktopMarketProvider = 'disabled' | 'community-market' | 'dsh-market'`；状态文件 `<userData>\desktop-market\state.json`（0o600、4KB 上限、`@deepseek-ai/dsh-atomic-write` 原子写 + 文件锁）；`DESKTOP_MARKET_IDENTITIES` 冻结三个 provider 的 Loader 身份；无效/缺失状态产生 fail-safe 默认（`legacyDefaulted` 标记，见 §3.4 settings 视图）。
- beta：`dsh-plugin-desktop-beta\` 与 stable 同构（包名、app id `ai.deepseek.dsh.desktop.beta`、runtime 通道 0.1.6-alpha.1、`dsh-plugin-desktop-beta/*` 导出路径）；共享功能由 `check:desktop-variants` 门约束（`docs\architecture.md` 第 62、66 段）。

---

## 4. 插件 / Cordis 系统

### 4.1 Cordis 框架本体

- 源码：`$ROOT\deepseek-harness\vendor\cordis\`（vendored；`vendor\README.md` 记录同步流程）。发布为 `@deepseek-ai/cordis@4.0.2`（产品侧安装于 `dsh-plugin-desktop\node_modules\@deepseek-ai\cordis\`，`src\{context,service,events,fiber,registry,reflect,logger,utils}.ts`）。
- 五个核心概念（`docs\cordis-primer.md`）：
  1. **插件** = 实现 Service 的对象：普通函数（可带 `inject`/`apply(ctx)`）或 `Service` 子类。
  2. **Context** = 服务仓库，服务占 `ctx.<key>`（`ctx.tools`、`ctx.llm`、`ctx.sessions`…），插件间按 key 查找而非 import 具体实现。
  3. **inject** = 声明式服务依赖："A plugin that names required services waits until those services exist, so load order is expressed through service requirements rather than manual boot sequencing."
  4. **Typed Events**：经 TS declaration merging 声明事件名；dispatch 模式表：

     | Mode | Awaited? | Dispatch Order | Has Return Value? |
     |---|---|---|---|
     | `emit` | No | listeners observe in registration order | No |
     | `waterfall` | No | listeners observe in registration order | Yes |
     | `parallel` | Yes | all listeners observe the event in parallel | No |
     | `serial` | Yes | listeners observe in registration order | Yes |
     | `bail` | No | listeners observe in registration order until one bails | Yes |

     dispatch mode 是事件的公开契约一部分；新 harness 事件用 `@mode` tag 文档化，生成目录会检查声明与 dispatch 站点一致（`cordis-primer.md` 第 27 行）。
     **waterfall 语义**（第 29–35 行）：around-middleware，listener 收 `(...args, next)`；调 `next()` 委托可能被包装的结果，不调即短路；值经 `next()` 返回值传递。单决策事件以短路为设计：拥有决策的 listener 可返回而不 `next()`；只标注/观察的必须委托。`prepend: true` 仅用于必须早于普通注册运行的 listener。
  5. **可回滚 effect**：`ctx.effect()`/`ctx.on()` 安装的注册在 fiber dispose 时按序解除。
- 官方配套包：`@deepseek-ai/cordis-plugin-loader`（运行时插件树 + Loader 服务；`src\config\{entry,group,isolate,tree}.ts`）、`cordis-plugin-include`（YAML/JSON include，**解析 `!!js` 表达式**）、`cordis-plugin-hmr`（HMR）、`cordis-plugin-timer`（dispose-aware 定时器）、`cordis-plugin-group`。

### 4.2 组合文件格式（cordis.yml / cordis.patch.yml）

**行（row）语义**（`docs\architecture.md` "Profiles and bundles" 节 + `packages\boot\app-boot\README.md` + `docs\cordis-primer.md` "Loader Configuration" 节）：
- 组合文件是一个有序行列表。两种操作：
  - `{ id, config }` —— **patch**：按 id 找到目标行，**整体替换其 config**（不深合并；保留的字段必须重述）。
  - `{ insert: [ {id, name, config?, inject?, disabled?}, … ] }` —— 插入新行（`name` 是包 specifier：npm 名或子路径如 `dsh-plugin-desktop/terminal`、`dsh-tool-subagent-control/list-agents`）。
- `!!js` 表达式（**必须是 `!!js` 不是 `!js`**，`AGENTS.md` 明示）：
  - `cordis-plugin-include` 把 `!!js` 解析为表达式节点；Loader 对 entry 的 `config` 在**声明的 injects 激活之后**、针对该插件 context（`ctx.serviceName`）求值（例：`packages\bundle\web-app\cordis.patch.yml` 第 138–139 行 `!!js ctx.webStartup.host ?? '127.0.0.1'`）；
  - entry 的 `disabled` 字段在**每次挂载决策**时针对 loader context 求值（例：`dsh-plugin-desktop\cordis.patch.yml` 第 10 行 `!!js process.platform === 'linux'`）；
  - Include 对目标激活前的嵌套行表达式保持惰性。"Other entry metadata stays literal. Use overlays when the environment selects plugins."
- patch 指向不存在的行 → stderr 警告；空文件 → 启动失败（用 `[]` 禁用）。
- **层叠顺序**（对空 entry list）：profile 按列出顺序的每个 bundle → profile 的 `cordis.patch.yml` → home 级 `cordis.patch.yml` → `--patch` overlay。
- **profile**：`$DSH_HOME/profiles/<name>`，含 `dsh.profile` manifest（bundles 列表）、npm `dependencies`、自己的 `cordis.patch.yml`、`patchReload: live|startup`（自定义 profile 默认 live；shipped `web` 模板 live，`headless`/`sdk`/`acp` 为 startup）。`dsh --profile <name> --from-default-profile <template>` 创建自定义 profile。
- **bundle**：`package.json` 声明 `dsh.bundle.patch`。发行 bundle 六个：`dsh-base`（§2.3 全 86 行）、`dsh-web-app`（491 行）、`dsh-headless`、`dsh-sdk-app`、`dsh-acp-app`、`dsh-sdk-minimal`（独立树，不走 base）。
- 查看有效组合：`dsh --profile web --dump-config`（任何行都可被自己的 patch 替换）。
- 真实示例（`apps\cli\config\examples\`）：`cordis\cordis.yml`（webserver host/port 3081 + 插入 `cordis-host-runner`/`tool-cordis`）、`schedule\cordis.yml`、`mcp-memory\*.yml`、`github-review\cordis.yml`。

### 4.3 插件能注册什么

| 能力 | 机制 | 例子 |
| --- | --- | --- |
| 服务 | `class extends Service` 或 `ctx.provide()` | `packages\host\webserver`（`webServer`）、`packages\session\*` |
| 事件 | `declare module '@deepseek-ai/cordis' { interface Events {...} }` + `ctx.emit/waterfall/…`（`@mode` tag） | `webserver/index-inject`（`packages\host\webserver\src\index.ts`） |
| 工具 | tools 流水线（`ctx.tools`） | `packages\core\tools`、`tool-todo`、`tool-workflow` |
| Web 路由 | `webServer` 路由/upgrade 注册 | `packages\api\gateway`、`dsh-plugin-desktop\src\desktop-settings-route.ts` |
| 浏览器 UI | client 行 slot 注册（§5.4）、theme、settings 命名空间 | `packages\client\ui-*` 60+ 包 |
| system-prompt 段 | `systemPrompt.section()` | web-runtime 行（`packages\bundle\web-app\src\index.ts` 第 236 行） |
| shell 环境变量 | `shellEnv.register()` | `DSH_WEB_URL`（同上 242–249 行） |
| 定时器 | `@deepseek-ai/cordis-plugin-timer` | dsh-base 行 |
| 设置命名空间 | `settings.register(namespace, schema)` | `packages\client\ui-theme\src\theme-settings.ts`（namespace + `ThemeSettingsSchema`） |
| HMR | `cordis-plugin-hmr` + `@deepseek-ai/dsh-client-hmr`（client bundle 重建链） | web-app patch 第 162–167 行 |

### 4.4 加载 / 启用 / 禁用

- 行级 `disabled`（布尔或 `!!js` 表达式，每次挂载决策求值）。
- required 行缺失/失败 → 整 app dispose + 非零退出；普通失败行 → 带标签警告（`packages\boot\app-boot\README.md`）。
- **live patch reload**：`patchReload: live` 的 profile 监视两个用户 patch 文件，变更即重组合（web 默认）。
- **HMR**：`dsh-client-hmr` 行常驻；`pnpm run dev:web` 的 watcher 重写 client bundle 时触发浏览器热替换（web-app patch 第 162–165 行注）。
- **插件安装**（`dsh plugin`，`apps\cli\src\plugin.ts`）：thin pnpm forwarder —— 在 profile 目录跑 `pnpm <args>`，然后按**已安装状态** reconcile `dsh.profile.bundles`：解析到声明 `dsh.bundle` 的包就入层栈（`exportsPatch()` 检查 `manifest.dsh?.bundle?.patch !== undefined`），移除/失去声明的出栈；`update` 因此能自动激活新版新增 `dsh.bundle` 声明的包。变更后需重启应用使新 bundle 进入下一次 Loader 组合（`docs\user-guide.md` 插件管理节：`dsh plugin --profile desktop add|remove|update`；托盘终端里裸 `dsh` 默认当前 profile）。
- Desktop 侧：launcher 每 generation 在 `dsh-web-app` 之后注入 desktop 层（§3.2）；市场安装走 `desktopPnpm.run(['add','<pkg>@<exact>'])` + reconcile（`dsh-community-market\README.md` "Automatic installation" 节）；卸载 `desktopPnpm.run(['remove', packageName])`。

### 4.5 审批模型（两层）

1. **工具动作审批**（harness 运行时）：`packages\interaction\user-approval`（`@deepseek-ai/dsh-user-approval`，README 原文）—— channel 中立的 one-shot 审批 seam：`ask` 策略把每个敏感请求发给部署的 human 或 machine answerers，`never` 直接拒；answerer 缺失/失败返回 `unavailable`，动作 **fail-closed**；审批只对当次请求有效；全部请求与结果记入请求 session 的 audit log；模型只看到工具结果与当前策略，看不到人类权限 UI 或审计事件。tools 流水线与 sandboxed bash 的 `ask` 决策都经此 seam。UI 面在 `packages\client\ui-approval`（SlotMap 声明见其 `src\client\contract\slots.ts`）。
2. **动态插件审批**（本 harness 的 Dynamic Cordis Plugins）：`cordis_define`/`cordis_run` 产生 approval request（`pluginRunId` 关联审批、Host/Client 加载、Run 卡片、错误）；`pluginId`（可演化）/`packageId`（不可变 Host/Client 源码版本）/`currentPackageId`（最近完全成功的包）/`nextPackageId`（待审批/进行中/最近失败的包）版本指针；单勾授权当前包、双勾授权同插件未来版本；失败不清 `currentPackageId`，旧版本保留可回滚。实现面：`packages\extensions\{cordis-host-runner,cordis-client-runner,tool-cordis,ui-cordis}`（`tool-cordis` 给模型 define/run/stop/undefine/inspect 工具；`ui-cordis` 渲染运行卡片）。web-app patch 默认挂载 `cordis-host-runner`（第 121–122 行）。

### 4.6 市场 / Fabric

- **`dsh-community-market`**（`$ROOT\dsh-community-market\`，`v0.1.0-dev.0`，private，exports `.`/`./client`/`./contracts`）：
  - 四视图：Discover / Installable / Installed / Sources（`src\client\{MarketLauncher,MarketOverlay,MarketSettingsTab,market-view-store,api,locales}.tsx`）。
  - catalog 源契约：`docs\schemas\catalog-{source,provider-page,query,snapshot}.schema.json`（ajv 校验，`src\contracts\{schemas,validate,generated\*}.ts`）。
  - 适配器：`src\adapters\{dsh-1024store,dshfind,standard-http}.ts`（DSH 1024Store 走 `/api/v2/plugins` 分页目录；dshfind 走 versioned REST）。
  - Host 路由：`src\host\routes.ts`；网络面：`src\network\restricted-http.ts`（受限 HTTP）；媒体：`src\media\{service,normalize-image,restricted-image}.ts`（sharp 归一化）。
  - 安装（`src\install\{service,github,manual}.ts`）：自动安装资格 = 条目恰好暴露一个 npm 包名、非 Desktop 自有 bundle、官方 npm `latest` 回显同名精确版本、npm manifest 声明有效 `dsh.bundle.patch`；安装只调 `desktopPnpm.run` 加精确版本并 reconcile，**不建 receipt/snapshot/rollback**（恢复交给 Desktop 三槽 checkpoint）；卸载只接受 generation 作用域的 `bundleId`；renderer 永不提交包名/命令。
  - `package.json` 的 `dsh.client` inject 官方 UI 包（`dsh-client-locale`、`dsh-client-ui-layout`、`dsh-client-ui-renderer`、`dsh-client-ui-settings`、`dsh-client-ui-sidebar`）—— 它本身也是 Cordis 插件。
- **`dshmarket`**（第三方市场运行时，1.38.1，被 `.yarn\patches` patch）：`dsh-plugin-desktop\docs\plugin-services.md`：bundled dshmarket 运行时消费 `runPlugin()` 与 `runExternalMarketPluginInstall()`（解析精确版本后跨服务）。
- **provider 开关**：`dsh-plugin-desktop\src\desktop-market.ts` —— 机器级 `DesktopMarketProvider`（§3.6）。
- **`dsh-community-fabric`**（`$ROOT\dsh-community-fabric\`）：文档型 RFC 工作区。README 明示 "Draft and documentation only"（无运行时/SDK/schema/badge/可加载插件）。四个构件：静态 manifest、versioned capability、可预测生命周期/事件契约、机器可读兼容性结果。RFC：
  - `docs\rfcs\0001-plugin-manifest-capabilities-events.md`（manifest/capability/事件模型，含 `messages.observe` 单一不可变事件的 v0.1 里程碑）
  - `0002-runtime-presentation-invocation-transport.md`（runtime/presentation/transport）
  - `0003-service-providers-and-composition.md`（service providers/composition）
  - `0004-provenance-validation-and-diagnostics.md`（provenance/validity/diagnostics）
  研究文档对标成熟插件框架（`docs\research\{mature-plugin-frameworks,vscode-extension-model,dsh-plugin-needs,community-issue-23-review}.md`）；`docs\architecture\compatibility-layer.md`。**安全边界**（README "An important safety boundary" 节）：capability 声明用于兼容/同意/审计，**不是安全沙箱**；同进程 JS 插件仍可越权，只有实现真实隔离的 host 才能声称权限被技术强制。

---

## 5. Web 壳（GUI）

### 5.1 入口与 boot

- `apps\web\index.html`（`<div id="root">` + `/src/main.ts`）；`src\main.ts` 构造 `AppWebEntry`（`packages\client\web\src\boot.ts`）。
- `AppWebEntry.run()`（boot.ts 第 46–92 行）：等 `__DSH_BOOT_READY__` → 取 `window.__ModuleLoader__` → `moduleLoader.create({boot: window.__DSH_BOOT__, staticModules, loadBundle})` → 预取 immediate tier → `bootClient`（逐行激活，boot 页显示每行状态）→ `mountClient(ctx, container)`（UI renderer 接管挂载点）。
- **`apps/web` 的 Vite 入口不是独立应用**：只有 `dsh web` 注入 `window.__DSH_BOOT__`（`packages\bundle\web-app\src\index.ts` 第 144 行原注释）。`src\preview.ts` 是 worker 预览页（自带页面，不带 dist）。

### 5.2 客户端模块系统（dsh.client 双面包）

- 约定：client 插件的 `package.json` 声明 `dsh.client`（`inject`/`platform: web` 等；wire 类型 `DshClientManifest` 在 `@deepseek-ai/dsh-package-manifest`）。
- node 半边 `@deepseek-ai/dsh-client-modules`（`packages\client\modules\src\index.ts`，1003 行）：增量扫描（每个 cordis `internal/plugin` 发射把该 fiber entry 标 dirty，microtask flush 调和）；合成 `WebBootGraph`；serve `/plugins/<id>/client.js` combo + sourcemap；向 webserver index-inject 表贡献注册 facade/预载脚本/graph；provide `clientModules` 服务（HMR node 半边）。
- 浏览器半边 `client/modules/client`（`src\manifest.ts`，423 行头注 + 类型）：**lazy CJS 模型** —— 执行插件 bundle 只 `window.__ModuleLoader__.load({id, factory})` 注册工厂；一切模块体副作用（含 CSS 注入）都在工厂闭包里，materialization（factory(require) → exports）发生在首次 import/require 并 memoized，工厂间 require 递归 materialize，因此 load 顺序无需外部排序。解析分支顺序（import）：seed word → shell instance；memoized record → exports；graph row → 注册依赖工厂 + 自身工厂；registered factory → materialize；其余 → throw（构建期 bundle purity gate 的运行时镜像）。vendored cordis Loader 经其 `internal` contract 消费这个对象（唯一调用点 `EntryTree.import` → `internal.import`），entry 治理（fiber 生命周期、inject 等待、update/refresh）留在 vendored 侧。
- **`__DSH_BOOT__` wire 格式**（`packages\client\modules\src\client\manifest.ts`）：
  - `WebBootGraph: { rev, entries: WebBootEntry[], batches: WebBootBatch[] }` —— `rev` 是整图一致性锚（内容 + bundle 哈希）；`entries` 按 module-graph 顺序（动态包行先于 `external` 请求它的行；Cordis 激活顺序与此无关，仍由 fiber 服务等待拥有）；每个 entry 恰好属于一个 batch。
  - `WebBootEntry: { id (=包名), url, rev, inject?, immediately?, external? }` —— `inject` 是包名依赖边（工厂到达 + 插件组合用）；`immediately` 标记 stage-one 预取；`external` 携带精确的非 inject 模块请求。
  - `WebBootBatch: { phase: 'bootstrap' | 'application', url, rev, entries }` —— 内容寻址 combo 脚本端点；bootstrap 是 parser-blocking。
  - `BootModuleRow`/`BootPluginRow`：boot 行的 npm 包视图（`id/url/initialUrl/rev/inject/external`）与 cordis-plugin 视图（`id/inject/immediately`，wire 可选字段归一化）。
- 传输：`@deepseek-ai/dsh-client-connection`（`packages\client\connection\`，node 半边把 Typert Remote gateway 绑到 webserver `/api`，browser 半边 fetch/SSE）+ `file-upload`（Blob/ReadableStream 上传独立于 RPC）+ `api-remotes`。

### 5.3 主题系统

- `packages\client\ui-theme\`：`src\theme-settings.ts`（settings 命名空间 + schema：`preference`、`fontSize` 默认值）、`src\boot-theme.ts`（pre-plugin palette 注入行：经 `webserver/index-inject` 在插件前把主题 bootstrap 写进 index，避免闪屏）、`src\index.ts`（44 行：Host 注册 + 每次 index 渲染推送当前行）。
- 浏览器侧：`src\client\`（theme 服务/快照）+ `packages\client\ui-layout\src\client\index.ts` 第 8 段：`ThemePresenter` 把 `ctx.theme` 快照投影到 `document.body`；样式走 CSS modules（各包 `*.module.css`）+ 全局 `packages\client\web\src\base.css`；品牌 token 来自 vendored `@deepseek-ai/dsh-brand`。
- Desktop 侧材质（mica/transparent/off）由 `dsh-plugin-desktop\src\window-material.ts` 提供（`windowsSupportsMica` 能力探测），`desktopWindow` client 服务把 `material`/`safeAreaInsets`/`dragRegion` 传给浏览器（`docs\plugin-services.md` `desktopWindow` 接口）。

### 5.4 布局与 slot 扩展点

- Slot 核心 `packages\client\ui-slots\src\index.ts`：`SlotMap` 经 TS declaration merging 由各 owner 扩；一次 `register()` 同时贡献组件 + 声明子 slot + seat store + 注册者业务面（零运行时依赖，仅 React 类型）。
  - `SlotKind: 'single' | 'list' | 'keyed' | 'chain'`；`SlotScope: 'root' | 'session-maybe' | 'session'`（session slot 的 sessionId 由框架注入，owner 不传）。
  - 组件 props = 四份交集（runtime/standard kit、renderSlots、store、registrant 业务面 + `t` 翻译 seat）。
- **官方 slot 契约清单**（声明处全文已读；owner 包 + kind/scope 逐行列出）：

  声明于 `packages\client\ui-layout\src\client\index.ts` 第 46–92 行（`AppFrame` 占用 runtime 内置 `root` slot，同一 `register()` 声明以下 4 个子 slot）：

  | slot | kind/scope | 说明 |
  | --- | --- | --- |
  | `sidebar` | single / root | 被 `ui-sidebar` 的 SidebarRoot 占用；其内再声明 workspace/settings seat；注册这里 = **整体替换**导航列 |
  | `main` | keyed / root | 保留键 `conversation` 承载 Conversation，其他键无 session 绑定 |
  | `rightbar` | single / root | 右列；宽度/显隐由 occupant 经 `ctx.layout` 汇报 |
  | `shell.overlay` | list / root | **帧级浮层，click-through**；官方注释明示这是给你自己的帧级面（badge/toast/status pill/新面板）的 additive seat，新 id 与 shipped 条目并列而不是替换 |

  声明于 `packages\client\ui-conversation\src\client\contract\slots.ts` 第 117–177 行：

  | slot | kind/scope |
  | --- | --- |
  | `main.conversation` | single / session-maybe |
  | `conversation.session` | single / session |
  | `conversation.session.header` | single / session |
  | `conversation.session.header.lineage` | single / session |
  | `conversation.session.header.actions` | list / session |
  | `conversation.session.header.utilities` | list / session |
  | `conversation.session.header.corner` | single / session |
  | `conversation.view` | list / session（View 标签 roster） |
  | `conversation.composer` | chain / session（selector 路由的 composer 替换） |
  | `conversation.hero.workspace` | single / root（空会话 Hero） |
  | `conversation.hero.brand.mark` | single / root |
  | `conversation.hero.agentPreset` | single / root |
  | `conversation.input.dock` | list / session |
  | `conversation.input.overlay` | list / session |
  | `conversation.composer.dock` | list / session |
  | `conversation.input.left` | list / session（composer 工具行左） |
  | `conversation.input.right` | list / session（submit 前） |
  | `conversation.composer.bar` | single / session-maybe |
  | `conversation.input.attachments` | draft-attachment rail + drop target |

  声明于 `packages\client\ui-chat\src\client\contract\slots.ts` 第 180–218 行：

  | slot | kind/scope | 说明 |
  | --- | --- | --- |
  | `conversation.chat.node` | keyed / session | 按 `ChatNodeKind` 分派的最终节点渲染器；复用 key = 替换该节点渲染器，无 occupant 的 kind 不渲染 |
  | `conversation.message.images` | single / session | 连续消息图组渲染器（owner 提供引用 + 授权 loader） |
  | `conversation.chat.commandview` | keyed / session | 按命令名；未占用 key 用通用卡 |
  | `conversation.chat.turnTail` | chain / session | selector 路由的 Turn 尾部扩展；全链拒绝则为空 |
  | `conversation.chat.assistant-actions` | list / session | 按消息 id 的有序动作；新 id 添加、复用 id 替换 |

  声明于 `packages\client\ui-tool\src\client\contract\slots.ts` 第 10–42 行：

  | slot | kind/scope | 说明 |
  | --- | --- | --- |
  | `tool.call.toolview` | keyed / session | **key 域开放** —— 按 wire 工具名注册，可注册自己包注册的工具；已覆盖的 key 是替换而非共享，未认领 key 落回通用工具行（"registering is additive for your own tool and a takeover for a shipped one"） |
  | `tool.call.images` | single / session | 经 attachment 呈现插件渲染；同一子 slot 只能被一个 toolview 声明，重复声明加载时抛错 |
- 其余声明 SlotMap 的包（grep 结果，19 处）：`ui-chat`、`ui-conversation`、`ui-tool`、`ui-approval`、`ui-settings`、`ui-settings-models`、`ui-settings-plugins`、`ui-workspace`、`ui-sidebar`、`ui-sidebar-right`、`ui-sidebar-documentpreview`、`ui-renderer`、`ui-trajectory`、`resources` 等。
- **第三方 UI 注入路径**：写一个声明 `dsh.client` 的包 → 在 client 行里 `register()` 进现有 slot（最宽松的 additive seat 是 `shell.overlay`；工具卡片按 `tool.call.toolview` 的 key 注册）或经 declaration merging 声明自己的 slot 键 → 走同一 `__DSH_BOOT__` 模块图加载。无 Electron 通道、无 DOM 私有 API（`docs\why-desktop.md` "我们刻意不做什么"）。

### 5.5 主要 UI 包分组（`packages\client\`，60+）

- chat/conversation/composer：`ui-chat`、`ui-conversation`、`ui-input-trigger`
- sidebar 族：`ui-sidebar`、`ui-sidebar-files`、`ui-sidebar-terminal`、`ui-sidebar-documentpreview`、`ui-sidebar-right`
- tool/trajectory：`ui-tool`、`ui-trajectory`
- 设置族：`ui-settings`、`ui-settings-general`、`ui-settings-models`、`ui-settings-plugins`、`ui-settings-plugin-inventory`、`ui-settings-unarchive-sessions`
- 交互：`ui-approval`、`ui-user-questions`、`ui-message-feedback`
- 任务面：`ui-jobs`、`ui-goal`、`ui-plan`、`ui-subagent`、`ui-workflow-run`、`ui-schedule`
- 会话/工作区：`ui-session`、`ui-deliverables`、`ui-reference`、`ui-workspace`、`ui-open-in-app`
- 基础设施：`ui-renderer`（renderer 接管 + 注册表）、`ui-layout`、`ui-slots`、`ui-theme`、`ui-primitives`、`ui-locale`、`ui-cordis`（动态插件运行卡片）

---

## 6. 文档

| 位置 | 内容 |
| --- | --- |
| `$ROOT\README.md`（240 行） | 产品门面（中文优先）："万物皆「插件」，桌面本身也是「插件」"；声明为独立社区项目（与深度求索无隶属关系）；Windows x64 + macOS Universal 安装器；下载入口 dshdesktop.cn；赞助区。 |
| `$ROOT\docs\architecture.md`（79 行，全文已读） | 产品架构：总览图、启动顺序 7 步、Host/Client/native runtime 三层、profile 与服务边界、打包运行时闭包（asar unpack）、发行通道协议、维护者深读链接（含 4 篇 `.agents` 决策记录）。 |
| `$ROOT\docs\plugin-development.md`（214 行，全文已读） | 插件开发指南：两层插件（普通 DSH 插件 vs Desktop 服务插件）、`desktopProfiles`/`desktopPnpm` 用法与失败语义、双栖插件 fallback 模式（`ctx.get('desktopProfiles')` + 嵌套 `ctx.inject(...)`）、外部开发沙箱（Desktop 旁的普通 `dsh web` 镜像）、测试要点、生态愿景三原则（组合优先/声明清晰/兼容优先）。 |
| `$ROOT\docs\plugin-ecosystem.md`（46 行） | 生态倡议书："桌面壳是第一个范例"（"没有任何特权"）；市场上线后按规范开发更有利；Fabric 与市场的当前状态。 |
| `$ROOT\docs\why-desktop.md`（49 行） | 产品定位与边界；"我们刻意不做什么"：不重写 Web UI、不在兼容模式覆盖上游组合、不建 Desktop 数据库、不给第三方 Electron 私有 API、**不把 roadmap（插件市场、手机远程、Channels）写成已交付**。 |
| `$ROOT\docs\user-guide.md` | 安装/首次启动/三种呈现模式/插件管理（`dsh plugin --profile desktop add|remove|update`；托盘终端里裸 `dsh` 默认当前 profile；变更后重启）/更新（托盘）/恢复/故障排查；默认端口 0（随机）。 |
| `$ROOT\docs\faq.md`、`evidence\` | 常见问题与证据。 |
| `$ROOT\dsh-plugin-desktop\docs\plugin-services.md`（330 行，全文已读） | **插件作者权威 contract**：`desktopProfiles`/`desktopPnpm`/`desktopWindow` 的类型、生命周期、失败语义、stability boundary；mermaid 层叠图。 |
| `$ROOT\dsh-plugin-desktop\docs\`（compatibility-chrome-isolation.md 等） | 兼容模式隔离等专题。 |
| `$ROOT\deepseek-harness\README.md` + `AGENTS.md` | 上游仓库说明；AGENTS.md 含完整 packages group 表、命令、`!!js` 规则、SQLite 单调 `SCHEMA_VERSION`、应用启动规则（只有 `dsh` profile 启动受支持 Node 应用）、pre-stable API 声明。 |
| `$ROOT\deepseek-harness\docs\` | `architecture.md`（上游架构：Cordis/profiles/bundles 层叠/应用启动/Desktop 应用/extension cookbook 索引）、`cordis-primer.md`（全文已读：五概念 + dispatch 模式表 + waterfall 语义 + Loader 配置）、`cordis-tutorial\`、`cordis-api\`、`cookbook\`（加 package/tool/LLM adapter/settings card 指南）、`subsystems\`、`config-catalog.md`（160KB 生成目录）、`tool-catalog.md`、`module-graph.md`、`capability-seams.md`、`web-styling.md`、`glossary.md`。 |
| `$ROOT\.agents\notes\implemented\architecture\` | 4 篇决策记录：pinned-upstream-and-isolated-yarn-workspace、desktop-profile-and-pnpm-services、desktop-advanced-shell、native-shell-generation-and-platform-adapters（2026-08-15/19）。 |

**插件化 roadmap 信号**：当前版本已交付"桌面即插件"（组合路径、无特权）+ 双公开 Host 服务 + 内置市场（dev）+ 生态倡议；未交付/明确 roadmap：Fabric 标准（RFC draft，无运行时）、手机远程（`remote-control-offer.ts` 预留面）、Channels（`why-desktop.md` 明示不写进已交付）。

---

## 7. 原生/桌面能力清单（实现文件）

全部位于 `$ROOT\dsh-plugin-desktop\`（除注明外）。

| 能力 | 实现文件 | 备注 |
| --- | --- | --- |
| Electron 主进程/单实例 | `src\main.ts`、`src\bin.ts` | 单实例锁在最前（`docs\architecture.md` 启动顺序 1） |
| Host 进程管理 | `src\host-process.ts`（`startIsolatedDesktopHost`）、`src\host-bootstrap.ts`、`src\host-process-entry.ts`、`src\host-launch-environment.ts` | Host Cordis generation 跑在 Electron main 进程内隔离域 |
| 窗口创建/状态 | `src\electron-runtime.ts`（`ElectronDesktopRuntime`）、`src\electron-shell-generation.ts`、`src\window-options.ts`、`src\main-window-state.ts`、`src\auxiliary-window-options.ts`、`src\local-window-policy.ts` | generation 幂等 `release()`，禁跨代缓存 |
| 平台适配 | `src\electron-platform.ts`（win32/darwin/linux adapter） | 目录选择、shell 切换、更新下载、菜单、Dock 图标、材质 |
| 窗口材质/拖拽 | `src\window-material.ts`（mica/transparent/off，`windowsSupportsMica`）、`src\window-chrome.ts`（36px frame，`DESKTOP_FRAME_HEIGHT`） | Windows 按能力启用 Mica |
| 系统托盘 | `src\tray-icons.ts`、`src\tray-locale.ts`、`scripts\generate-tray-icons.mjs` | 托盘含 profile 选择器 + 有序 command 注册（desktop-shell/profiles/terminal/updates 贡献） |
| 原生菜单 | `src\native-menu.ts` | |
| 缩放快捷键/导航限制/外链 | `src\electron-shell-generation.ts`（`docs\architecture.md` "原生 Shell generation" 节） | generation 内监听 |
| 原生通知 | `src\notifications.ts`（`desktop-notifications` 行） | |
| 原生对话框 | `src\desktop-dialog-window.ts`（`DesktopDialogWindow`）、`src\native-dialog-copy.ts`、`native-ui\desktop-dialog\` | 独立沙箱化模态 BrowserWindow，一次性有界本地结果 |
| 独立桌面窗口 | `native-ui\{recovery,profile-create,profile-selector,setup-wizard,compatibility-chrome}\*.html` + `src\recovery-*.ts`、`src\profile-create-window.ts`、`src\setup-wizard-window.ts`、`src\startup-recovery-window.ts` | shadcn 风格，不进 Web 组件树 |
| 终端（PTY） | `src\terminal.ts`、`src\desktop-terminal.ts` + 上游 `packages\terminal\*`、`packages\subprocess\subprocess-local`（node-pty） | 托盘终端，隔离 env（`DSH_DESKTOP_*`、`ELECTRON_RUN_AS_NODE`），不改用户全局 PATH |
| 文件访问/选择 | `src\directory-picker-route.ts`、`src\directory-picker-contract.ts`、`src\file-path-bridge-contract.ts`、`src\electron-reveal.ts`、`src\file-exporter.ts` | 目录选择器走 webserver 路由 + native adapter |
| 进程生成 | `src\pnpm.ts`（内置 pnpm 11.8.0）、`src\host-process.ts`、上游 `packages\subprocess\*` | `desktopPnpm` 单 generation 单操作，subprocess 服务管完整进程树 |
| 自动更新 | `src\update-checker.ts`、`src\update-download.ts`、`src\update-lifecycle.ts`、`src\updates.ts` | `X-DSH-Desktop-Channel` 通道协议；用户确认下载；macOS DMG/Windows NSIS 交接；Beta 可并行安装 stable |
| 设置存储 | `src\desktop-settings-controller.ts`（`DESKTOP_SETTINGS_NAMESPACE`）、上游 `packages\settings\settings-file`（`$DSH_HOME\settings.yaml`） | Desktop 私有状态存 Electron userData（`src\profile-manager.ts`） |
| Session 存储 | 共享 DSH home（上游 `packages\session\session-persistence-jsonl`）+ `src\profile-checkpoint.ts` 三槽轮转 checkpoint | 切换 profile 不迁移数据；checkpoint 不含凭据/.env/session/存储 |
| LAN/HTTPS 暴露 | `src\lan-{addresses,https-certificate,https-ingress,https-runtime}.ts`、`src\desktop-network.ts`（`selfsigned` 本地 CA，`DESKTOP_LAN_HTTPS_CA_PATH`） | 默认 loopback 随机端口（`dsh-desktop.port: 0`）；显式确认后全接口 |
| Windows 沙箱 | `src\windows-pwsh-sandbox.ts`（导出 `./windows-pwsh-sandbox`）、`src\windows-acl-runner.ts`（koffi）、上游 `packages\sandbox\sandbox-windows-acl`、`native\system\` | PowerShell ConstrainedLanguage 等 |
| 崩溃/诊断 | `src\crash-evidence.ts`、`src\diagnostic-export.ts`、`src\diagnostics.ts`、`src\log-files.ts`、`src\desktop-logger.ts`、`src\mask-secrets.ts` | `dsh-desktop --export-diagnostics` 冷启动导出 ZIP |
| 恢复/安全模式 | `src\safe-mode.ts`、`src\startup-recovery-*.ts`、`src\desktop-boot-recovery.ts`、`src\recovery-copy.ts`、`src\recovery-plugin-uninstall.ts` | 恢复窗口四 Tab（插件管理/回滚/切换配置/诊断） |
| 工厂重置/数据目录 | `src\desktop-factory-reset.ts`、`src\desktop-data-directory.ts`、`src\desktop-data-operation-lock.ts` | |
| 市场 | `src\desktop-market.ts`（provider 开关 + fail-safe 状态）+ `dsh-community-market\src\*` + `dshmarket`（vendored/patched） | 见 §4.6 |
| ASAR/打包完整性 | `src\asar-module-resolver-state.ts`、`src\packaged-filesystem-smoke.ts`、`src\packaged-runtime-{path,smoke}.ts`、`scripts\verify-electron-fuses.ts` | 物理 unpack 依赖（pnpm/node-pty/ACL native）在 `app.asar.unpacked` |
| 远程预留 | `src\remote-control-offer.ts` | 手机远程（roadmap，未交付） |

---

## 附：对插件化方案设计的直接可用的事实

1. **组合是唯一的扩展路径**：bundle（`dsh.bundle.patch`）+ profile + 层叠 patch + `dsh.client` 声明，已覆盖 Host/Client/市场/桌面壳自身；"插件化"在 DSH Desktop 语境下 ≈ 如何安全地用这套机制表达新能力（`docs\plugin-development.md` 的两层模型）。
2. **桌面壳无特权**：`desktop-shell` 等 7 行与第三方插件走同一 Loader 路径；私有面（`desktopRuntime` 等）与公开面（`desktopProfiles`/`desktopPnpm`/`desktopWindow`）边界成文（`dsh-plugin-desktop\docs\plugin-services.md` Stability boundary）。
3. **审批/信任已有三层**：工具动作（user-approval，fail-closed + audit）、动态插件（cordis define/run 审批 + 版本指针 + 单勾/双勾授权）、市场安装（npm `latest` 回显 + `dsh.bundle.patch` 资格 + 用户确认，无 receipt/rollback）。
4. **Fabric 是标准层的占位**：manifest/capability/host descriptor 只有 RFC（0001–0004），无运行时；capability 明确"不是沙箱"。插件化方案若要引入 capability 模型，应对齐 RFC 0001 的 v0.1 里程碑范围（静态 manifest + Host Descriptor + required/optional capability + 确定性生命周期 + `messages.observe` + conformance suite）。
5. **上游不可动**：产品侧修改只落在 4 个 workspace；上游升级走 `upstream.json` + vendored tgz 重打包（`scripts\sync-vendored-runtime.mjs`），stable/beta 双通道独立 pin。
6. **UI 扩展只认 slot**：第三方浏览器面 = `dsh.client` 包 + slot 注册（`shell.overlay` 是最宽松的 additive seat；工具卡片走 `tool.call.toolview` 开放 key 域）+ settings 卡片；Electron API 永远不进 renderer。
7. **变更生效语义**：profile/bundle 变更 → 下次 generation（重启）生效；live patch reload 只覆盖两个用户 patch 文件；HMR 只覆盖 `dev:web` watcher 重写 client bundle 的场景。
8. **端口/传输事实**：默认 loopback 随机端口（`dsh-desktop.port: 0`，`dsh-plugin-desktop\src\desktop-port.ts`）；`dsh web` 单独跑默认 127.0.0.1:3080；LAN 暴露需显式确认 + 本地 CA。
