# DSH++ 新壳（DSH Desktop）适配方案

> 状态：**方案稿（待用户确认，未实施）** · 日期：2026-09 · 配套文档：`DSHPP_PLUGINIZATION.md`（P0 已完成）、`dsh-desktop-architecture-report.md`（新壳架构调研）、`PROJECT_ANALYSIS.md`（旧版分析，部分过期）
>
> 本方案回答三件事：
> 1. DSH++ 如何**完美适配当前新壳**（DSH Desktop 2.0.11，`D:\DSH\dsh-desktop`）；
> 2. 如何把 **DSH++ 做成独立插件**（`dsh-plugin-dshpp`）、把**增强器做成独立插件**（`dsh-plugin-dshpp-enhancer`），对齐官方"桌面壳本身就是一个插件"的范例（`dsh-plugin-desktop`）；
> 3. DSH++ **现有功能逐项解析**：实现 → 缺陷 → 优化方向。

---

## 1. 目标与硬约束

### 1.1 目标

| # | 目标 | 说明 |
|---|------|------|
| G1 | 完美适配新壳 | DSH++ 的全部能力在新壳（DSH Desktop）中可用，且不再依赖 CDP 9222 调试端口 |
| G2 | DSH++ 独立插件 | `dsh-plugin-dshpp`：标准 Cordis bundle（host 半 + client 半），走与官方/第三方插件相同的组合路径，无特权 |
| G3 | 增强器独立插件 | `dsh-plugin-dshpp-enhancer`：DSHSkin 运行时 + 内置模块 + 用户脚本，独立于核心插件，可单独安装/卸载 |
| G4 | 对齐"桌面即插件" | 以 `dsh-plugin-desktop` 为范例：bundle + client 声明 + 官方 slot/service，不假设、不覆盖他者内部实现 |
| G5 | 功能逐项整改 | 每个现有功能给出缺陷清单与优化方向（§5） |

### 1.2 硬约束（用户已确认，不可违背）

| # | 约束 | 来源 |
|---|------|------|
| C1 | **DSH++ 永不自动拉起/自愈 DSH Desktop**；任何启动/重启 DSH 的动作必须用户显式发起 | 用户反复强调 |
| C2 | 安装机制锁定为 **file:// managed block**（dshskin 模式）：bundle 复制到 `~/.dsh-skins/plugins/<name>/<version>/` + profile `cordis.patch.yml` 管理块。**不走** pnpm profile 依赖直装（P0 已实证被启动期 reconcile 删除），不走裸 market install | 用户否决 + 2026-09-17 实证 |
| C3 | 新壳变更生效语义：profile/bundle 变更需**重启 DSH** 生效（packaged 构建无 HMR/patchReload 接线）→ 面板必须提示用户手动重启，不得静默重启 | 新壳事实 |
| C4 | 上游不可动：方案只落在 DSH++ 侧（新插件包 + 安装器），不修改 `D:\DSH\dsh-desktop` 任何源码 | 新壳是安装目标 |

---

## 2. 现状解析

### 2.1 DSH++ 现状（Python 栈）

**运行时拓扑**（全部为 Python 进程侧）：

```
┌─ DSH++ Python 栈 ─────────────────────────────────────────────┐
│ desktop_app.py  pywebview(edgechromium) → msedge --app → 浏览器 │
│   └─ server.py  ThreadingHTTPServer :8765 + SERVER_TOKEN       │
│        ├─ panel.html  198KB 单文件 SPA（10 分区）              │
│        ├─ theme_engine.py   14 参数模板 → CSS（v7）            │
│        ├─ cdp_skin.py       CDP :9222 WebSocket 注入           │
│        │    └─ enhance_engine.py  DSHSkin 运行时 + 5 内置模块   │
│        ├─ session_store.py  zstd-JSONL 只读解析                │
│        └─ plugin_manager.py 双轨插件管理（A: npm / B: file://） │
└────────────────────────────────────────────────────────────────┘
        ▲ CDP 9222（Electron --remote-debugging-port，packaged 版唯一硬缺口）
┌─ DSH Desktop 2.0.11（新壳）───────────────────────────────────┐
│ Electron main（in-process Host Cordis root）                   │
│   ├─ dsh-base(86 行) + dsh-web-app(491 行) + desktop(7 行)     │
│   ├─ loopback 随机端口 Web carrier + 403 浏览器围栏            │
│   └─ GUI（ui-* 60+ client 包，slot 体系）                      │
└────────────────────────────────────────────────────────────────┘
```

**核心模块清单**（行数 = 当前代码规模）：

| 模块 | 规模 | 职责 |
|------|------|------|
| `server.py` | 101KB / 2206 行 | :8765 HTTP 服务、token 鉴权、~65 个路由、5 个 watcher（cdp 6s / drift 120s / page-error 8s / source 8s+12s grace） |
| `plugin_manager.py` | 79KB / ~1900 行 | 双轨安装（Track A npm+pnpm 900s 超时 / Track B file://）、zip-bomb 防护（200MB/50MB/4000 成员/压缩比 100）、managed block 读写、`.orig` 备份还原 |
| `dsh_env.py` | 40KB / ~955 行 | 路径/exe/node/pnpm 解析、dev/packaged 启动模式、新壳探测（`NEW_SHELL_MONOREPO`、`DSH Desktop.exe`、layout globs） |
| `cdp_skin.py` | 27KB / ~664 行 | CDP 连接、TargetSessions 多页同步、inject/remove、probe_regions、page logs、SkinWatcher |
| `enhance_engine.py` | 31KB / ~746 行 | DSHSkin 运行时模板（`RUNTIME_VERSION 1.0.0`）、5 内置模块、用户脚本（`~/.dsh-skins/enhance/`）、权限静态分析 |
| `theme_engine.py` | 28KB / ~628 行 | 参数 schema（glow/glass/radius/overlay/font_scale/force_dark 等 14 项）、CSS 生成、region 诊断、漂移检测 |
| `session_store.py` | 24KB / 580 行 | 会话只读解析、搜索/详情/对比/导出(md,json)/备份、`delete_sessions`（备份后物理删除，用户授权） |
| `desktop_app.py` | 215 行 | pywebview/Edge 窗口、单实例 mutex、托盘 |
| `dsh-skin.py` | 43KB / ~833 行 | CLI 20+ 子命令 |
| `panel.html` | 198KB / 3834 行 | 10 分区：home/themes/sessions/prov/enh/market/plugins/diag/logs/settings |
| `tray.py` / `updater.py` | 6.6KB / 65 行 | 托盘自启；GitHub Release 检查（`UPDATER_REPO=''` 未配置，死代码） |
| `market/` | 7 个内置脚本 | copy-code-button、focus-mode、message-index、night-schedule、prompt-library、selection-counter、wide-screen |
| `enhance-modules/` | 5 个模块 | session-tools、ui-tweaks、input-plus、usage-meter、thinking-zh |

**数据目录** `~/.dsh-skins/`（SKIN_ROOT）：`config.json`（主题 + 启动设置）、`enhance.json`、`selectors.json`、`logs.json`、`server.token`、`backups/`、`exports/`、`plugins/`（已装 bundle + `registry.json`）、`enhance/`（用户脚本）。

**新壳适配现状（已完成）**：
- `dsh_env.py`：新壳 monorepo 路径、`DSH Desktop.exe`、layout globs、`launcher_mode: packaged`；
- `cdp_skin.py::find_page_targets`：新壳 loopback 页面选择（随机端口页面如 `http://127.0.0.1:52341/app/`），兼容旧 `dsh-app://`；
- `config.json`：`dsh_home: D:\DSH\DSHdata\.dsh`、`desktop_exe` 已指向新壳；
- 插件化 P0 探针：`dsh-plugin-dshpp@0.1.0` bundle（host：`/dshpp/health` `/capabilities` `/tree` + HTML console；client：浮动按钮）+ `installer-core.mjs`（file:// 模式）已构建并通过安装/卸载验证。

### 2.2 新壳（DSH Desktop 2.0.11）架构要点

> 完整调研见 `dsh-desktop-architecture-report.md`（650 行），此处只列与本方案直接相关的事实。

1. **桌面壳本身就是一个 Cordis 插件**。官方 README 口号："万物皆「插件」，桌面本身也是「插件」"。`dsh-plugin-desktop` 的 `cordis.patch.yml` 声明 7 行（desktop-shell/terminal/diagnostics/notifications/pnpm/profiles/updates），launcher 每 generation 在 `dsh-web-app` 之后注入，**从不持久化进用户 profile**。
2. **薄 Electron 宿主**：Electron main 只做 单实例锁 → 解析 profile → native runtime → 进程内启动 Host Cordis root；UI 全部走 loopback HTTP/WS carrier（**随机端口**，`dsh-desktop.port: 0`），**无 preload、无 Electron IPC 桥**给第三方。
3. **Profile 组合**：bundle 层（`dsh.profile.bundles` → 各自 `cordis.patch.yml`）→ profile `cordis.patch.yml` → home `cordis.patch.yml`；patch 语义 = **整 config 替换**；`!!js` 表达式在 inject 激活后求值。
4. **启动期 pnpm reconcile**（`reconcileProfilePnpmWorkspace`）：会**删除 profile 中非预期依赖** → 直接 profile 依赖安装已被实证否决（P0 假证，`DSHPP_PLUGINIZATION.md` §9.5）；**file:// 行不受影响**（4 个 dshskin 行生产存活）。
5. **官方插件语义**：`dsh plugin add|remove|update`（thin pnpm forwarder + bundle reconcile）；Desktop 侧公开 `desktopPnpm.runPlugin()`（经打包 DSH CLI，单 generation 单操作）；**变更后需重启**。
6. **Client 模块系统**：包 `package.json` 声明 `dsh.client`（`platform: web` / `inject` / `immediately` / `external`）→ node 半边 `dsh-client-modules` 增量扫描 → 合成 `__DSH_BOOT__` 图 → 浏览器 `window.__ModuleLoader__.load({id, factory})` lazy-CJS 注册；**bundle purity gate**（client 代码只能用默认 externals + 声明的 external + 内联安全层，无 Node API）。
7. **Slot 体系**（UI 扩展唯一官方路径）：`shell.overlay`（list/root，**最宽松的 additive 帧级浮层**，官方注释明示给第三方 badge/toast/状态面板）、`sidebar`（single/root，注册=整体替换导航列）、`main`（keyed）、`rightbar`、`conversation.*` 系列、`tool.call.toolview`（key 域开放）。
8. **主题系统**：`ui-theme`（settings 命名空间 + pre-plugin boot palette 注入防闪屏）+ `ThemePresenter` 投影 `document.body` + CSS modules + `@deepseek-ai/dsh-brand` token。
9. **Desktop 公开 service**（第三方可用，`dsh-plugin-desktop/docs/plugin-services.md` 权威 contract）：
   - Host `ctx.desktopProfiles`：`current` / `list()` / `select()`（select = 重启边界）；
   - Host `ctx.desktopPnpm`：`run()` / `runPlugin(args,cwd,signal)` / `installPlugin({invokingDir, recovery})`；
   - Client `ctx.desktopWindow`：`mode`/`platform`/`material`/`safeAreaInsets`/`dragRegion`。
   - 私有非 API：`desktopRuntime`、`desktopPnpmBootstrap`、Electron 细节、state 文件格式。
10. **浏览器围栏**：`DesktopWebServer` 经 `connection` 服务 trust + 403 cookie gate 拒绝非信任浏览器请求（loopback GUI 同源 fetch 正常）。
11. **市场现状**：vendored `dshmarket@1.38.1`（patched，机器级 provider 开关）；`dsh-community-market` 为 dev（**尚无可用页面/安装器**）；`dsh-community-fabric` 仅 RFC（capability **不是沙箱**，明示）。
12. **生效语义**：profile/bundle 变更 → 下次 generation（重启）；`patchReload: live` 只覆盖两个用户 patch 文件（**packaged 构建未接线**）；HMR 仅 `dev:web` watcher 场景。
13. **原生能力**（新壳自带，DSH++ 对应能力应退役）：托盘（含 profile 选择器）、原生通知、托盘终端（PTY）、自动更新（`X-DSH-Desktop-Channel` 通道协议）、诊断导出（`dsh-desktop --export-diagnostics` 冷启动 ZIP）、恢复/安全模式（四 Tab 恢复窗口）、LAN HTTPS（自签 CA）、Mica 材质。
14. **dsh-base 服务面**（全 profile 共享，host 插件可注入）：`webServer`、`settings`（命名空间）、`credentials`（local）、`session` + `session-query-sqlite`（**SQLite FTS5** `sessionQuery`）、`subprocess`、`storage`、`timer`、`approval`、`shellEnv`、system-prompt section 等 86 行。

### 2.3 差距总览（DSH++ 能力 → 新壳机制）

| DSH++ 能力 | 现状通道 | 新壳对应机制 | 适配结论 |
|-----------|---------|-------------|---------|
| 面板 UI | Python :8765 + panel.html | webServer 路由页 + client bundle + slot | 路由页（P1）+ 浮动入口（P0 已有） |
| 主题注入 | CDP 9222 注 `<style>` | client bundle style 注入（in-page） | 无需 CDP（P4） |
| 增强器 | CDP 9222 注运行时 | client bundle（in-page 运行时） | 无需 CDP（P3，核心收益） |
| 会话管理 | Python 解析 zstd-JSONL | host 插件直读 + `sessionQuery` 服务 | host 插件（P1） |
| 供应商/凭据 | Python 读 yaml | host 插件直读 + `credentials` 服务 | host 插件（P1） |
| 插件管理 | 双轨（pnpm / file://） | file:// 管理块（锁定）+ `desktopPnpm.runPlugin`（npm 补集） | 单轨化（P2） |
| 脚本市场 | 本地 7 脚本 + 文件复制 | 增强器内置模块包 + 用户脚本目录 | 降级整合（P3） |
| 桌面窗口/托盘 | pywebview + pystray | 新壳自带（Electron 窗口/托盘/通知） | **退役**（P5） |
| 自动更新 | GitHub 检查（死代码） | 新壳自带（通道协议更新） | **退役**（P5） |
| 诊断/日志 | CDP probe + 自管日志 | host 自检 + 新壳诊断导出 | 降级整合（P2） |
| 安全鉴权 | SERVER_TOKEN 注入 HTML | connection trust + 403 围栏（官方） | **退役**（P1） |
| CLI | dsh-skin.py 20+ 子命令 | 薄壳 → `dsh plugin ...` + 少量 dshpp 命令 | 保留薄壳（P5） |

---

## 3. 目标架构

### 3.1 总览

```
DSH Desktop（新壳，不动）
├─ dsh-base + dsh-web-app + dsh-plugin-desktop（官方/桌面，现有）
├─ dsh-plugin-dshpp            ← 新增：DSH++ 核心（host + client）
│    host : /dshpp/* API（状态/主题/会话/供应商/增强/插件/市场）
│    client: 面板页 UI + 浮动入口按钮
│    data : ~/.dsh-skins（兼容）+ settings 命名空间 dshpp
└─ dsh-plugin-dshpp-enhancer   ← 新增：增强器（host + client）
     host : 脚本状态/权限分析/模块管理/用户脚本目录
     client: DSHSkin 运行时（in-page）+ 5 内置模块 + 用户脚本
```

两个插件**互不硬依赖**（enhancer 可选安装）：核心插件负责"管理与呈现"，增强器负责"页面增强执行"。二者通过数据目录与 settings 命名空间解耦；enhancer 未安装时，核心面板的增强分区显示"未安装增强器"引导。

### 3.2 插件一：`dsh-plugin-dshpp`（核心）

**bundle 形态**（沿用 P0 已验证的 `dshpp/bundle/`，扩展）：

```
dshpp/bundle/
├─ package.json
│    name: dsh-plugin-dshpp
│    exports: { ".": "./lib/index.js", "./client": "./lib/client.js" }
│    "dsh.bundle.patch": "./cordis.patch.yml"
│    "dsh.client": { "platform": "web", "immediately": true }
├─ cordis.patch.yml        # insert: dshpp 行（host 插件）
├─ lib/
│    ├─ index.js           # host 半：webServer 路由 + dshpp 服务
│    └─ client.js          # client 半：__ModuleLoader__.load 面板入口
├─ web/                    # 面板静态资源（P1 起：构建产物或单文件）
│    ├─ index.html
│    └─ app.js / app.css
└─ installer/              # install.mjs / uninstall.mjs / installer-core.mjs（file:// 模式，已验证）
```

**host 半职责**（`lib/index.js`）：
- `inject: ['webServer', 'settings']`（硬依赖，P0 已验证 `inject: ['webServer']` 是正确模式——软 `ctx.get` 守卫曾致路由静默丢失）；
- 路由前缀 `/dshpp/`：
  - 只读：`/health` `/capabilities` `/tree`（P0 已有）+ `/status` `/themes` `/themes/:id` `/sessions` `/sessions/search` `/session/:id` `/providers` `/enhance` `/enhance/files` `/market` `/plugins` `/plugins/tree` `/logs` `/settings`；
  - 写入（P2）：`/switch` `/restore` `/rebuild` `/enhance/save|delete|apply` `/market/install` `/plugins/install|toggle|remove|config` `/restart-request`（**仅排队用户发起的重启提示，绝不自动执行**，C1）；
- 数据访问：会话/凭据优先官方 service（`ctx.get('sessionQuery')` / `ctx.get('credentials')`，可选注入），fallback 直读文件（格式版本检查）；
- 插件管理：file:// 管理块读写（从 `plugin_manager.py` 移植：managed block 标记、zip-bomb 防护、`.orig` 备份、origin 探测）；npm 包安装走 `ctx.get('desktopPnpm')` 可选注入（双栖模式，`dsh-plugin-desktop/docs/plugin-development.md` 范本：先 `ctx.get('desktopProfiles')` 判 Desktop，再嵌套 `ctx.inject`）；
- settings 命名空间 `dshpp`（skinRoot、dshHome、panel 偏好）。

**client 半职责**（`lib/client.js`）：
- 浮动入口按钮（P0 已有，保留）→ 打开 `/dshpp` 面板页；
- 面板页 = webServer 路由 serve 的独立页面（同源 fetch `/dshpp/*`，天然过 403 围栏）；
- （可选，P1 增强）`shell.overlay` slot 注册一个状态徽章（DSH++ 注入状态 pill），**additive 不替换**；
- 面板 UI：移植 panel.html 10 分区（P1 先用现有单文件 SPA 换皮到 `/dshpp`，P2+ 逐步 React 化）。

**为什么面板是独立路由页而不是 sidebar/slot 主体**：
- DSH++ 是**管理工具**（10 分区、大表格、多步操作），不是会话内嵌组件；
- `sidebar` slot 注册 = **整体替换**官方导航列（破坏官方 UI，违背"组合优先"）；
- 独立路由页 + 浮动入口 = 最小侵入，与 desktop-shell 自己的 `/api/desktop/*` + 独立 native 窗口思路一致；
- 未来若官方提供第三方设置卡机制，可再增补设置入口（不阻塞 P1）。

### 3.3 插件二：`dsh-plugin-dshpp-enhancer`（增强器）

**核心变化：DSHSkin 运行时从"CDP 外部注入"变为"页面内 Cordis client 插件"。**

```
dshpp-enhancer/bundle/
├─ package.json
│    exports: { ".": "./lib/index.js", "./client": "./lib/client.js" }
│    "dsh.bundle.patch": "./cordis.patch.yml"
│    "dsh.client": { "platform": "web", "immediately": true,
│                    "inject": [] }        # 不依赖官方 client 包（保持独立）
├─ cordis.patch.yml
├─ lib/
│    ├─ index.js      # host 半：enhance 状态/脚本管理/权限分析
│    └─ client.js     # client 半：DSHSkin 运行时 + 模块装载器
└─ installer/          # 同 file:// 模式
```

**client 半**（核心创新点）：
- 页面内直接执行 `window.DSHSkin` 运行时（现 `_RUNTIME_TMPL` 的 JS 化，**不再经 CDP `Runtime.evaluate`**）；
- 模块装载：5 内置模块（session-tools/ui-tweaks/input-plus/usage-meter/thinking-zh）随 bundle 携带（`enhance-modules/*.js` 原样迁移）+ 用户脚本（host 经 `/dshpp-enhance/modules` API 下发源码 → client 执行）；
- 保留 DSHSkin API 面（`def/ready/wait/on/interval/ui.style/ui.el/toast/log/error/storage/isHotkey`）→ 现有 7 个市场脚本 + 5 个内置模块**零改动**继续工作；
- 样式注入：`ui.style()` 走 DOM `<style>`（现有语义不变），为 P4 主题对齐预留 token 通道；
- 日志：`log/error` → 经同源 fetch 回传 host（落 settings 命名空间或 host 日志文件），替代 CDP page-log 收集。

**host 半**：
- `enhance.json` 状态读写、脚本 CRUD（安装/启用/删除/备份）、权限静态分析（现有 `analyze_script` 移植）、脚本目录管理（`~/.dsh-skins/enhance/` 保持兼容）；
- 内置模块清单下发（版本、源码、权限声明）。

**收益对比（CDP → in-page）**：

| 维度 | CDP 模式（现状） | in-page 插件模式（目标） |
|------|----------------|------------------------|
| 调试端口 | 需要 9222（packaged 唯一硬缺口） | **不需要** |
| 外部进程 | Python server + watcher 常驻 | 无（运行时随页面生命周期） |
| 注入时机 | 页面加载后轮询发现（6s watcher） | 页面 boot 即装载（`immediately: true`） |
| 多页同步 | TargetSessions 手动同步 | 每个页面独立 boot，天然一致 |
| 漂移检测 | 120s 轮询指纹 | 可选（模块内 `ready`/`wait` 事件驱动） |
| 安全面 | 9222 对本地开放 | 仅 loopback GUI 同源 |

### 3.4 插件三：桌面（参考，非实施对象）

`dsh-plugin-desktop` **已经是** Cordis 插件（7 行、无特权、与第三方同路径组合）——用户"如 D:\DSH\dsh-desktop 将桌面变为一个插件"所指的正是这个官方范例。本方案**不修改桌面壳**（C4），而是让 DSH++ 两个插件走同一条路：
- bundle + `dsh.client` 声明（双面包）；
- 只依赖官方 contract（`webServer`/`settings`/slot/`desktopProfiles`/`desktopPnpm`/`desktopWindow`）；
- 不碰私有面（`desktopRuntime` 等）；
- 可回滚 effect（`ctx.effect`/`ctx.on`），generation dispose 时全部解除。

> 更深层的"核心 harness 本身插件化"（如把 agent-loop/session 拆成可替换插件）属于上游 roadmap（Fabric RFC 方向），不在本方案范围；如用户有此意图，另立方案。

### 3.5 数据与配置布局（目标态）

```
~/.dsh-skins/                       # 保留为用户数据根（兼容旧数据，可迁移）
├─ config.json                      # 主题 + 启动设置（保留，settings 命名空间为其镜像）
├─ enhance.json                     # 增强器状态（enhancer 插件读写）
├─ selectors.json / logs.json
├─ enhance/                         # 用户脚本（enhancer 管理）
├─ plugins/<name>/<version>/        # file:// bundle 安装目录（installer 管理）
└─ backups/ / exports/
$DSH_HOME/profiles/desktop/
├─ cordis.patch.yml                 # managed block：# >>> dshpp:plugins (managed, do not edit inside) >>>
│                                    #   - id: dsh-plugin-dshpp ... file:// 行
│                                    #   - id: dsh-plugin-dshpp-enhancer ... file:// 行
└─ settings.yaml                    # settings 命名空间：dshpp / dshpp-enhancer
```

迁移策略（P5）：旧 `~/.dsh-skins` 原样保留可用；新增 settings 命名空间只存偏好类配置；不强制迁移（避免破坏现存用户数据）。

---

## 4. 关键设计决策

| # | 决策 | 结论 | 备选与否决理由 |
|---|------|------|----------------|
| D1 | 安装机制 | **file:// managed block**（锁定，C2）；npm 包补集走 `desktopPnpm.runPlugin`（官方 `dsh plugin` 语义，可选） | pnpm profile 直装：启动期 reconcile 删除（P0 实证否决）；裸 market install：无 managed 边界、无法还原 |
| D2 | 面板形态 | **独立路由页 `/dshpp`** + 浮动按钮入口；可选 `shell.overlay` 状态徽章（additive） | sidebar slot：注册=整体替换官方导航（破坏官方 UI）；纯设置卡：承载不了 10 分区管理面 |
| D3 | 主题注入 | client bundle in-page 注入（P4），对齐 ui-theme token（`--dsw-alias-*`/brand token）为**叠加层**，不覆盖官方主题 | CDP style：9222 依赖，退役；直接改 ui-theme settings：会覆盖官方主题状态，违背组合优先 |
| D4 | 增强器运行时 | **in-page client 插件**（DSHSkin 随页面 boot 装载） | 保留 CDP：仅作为旧壳 legacy 通道（P5 前保留，默认关） |
| D5 | 会话/凭据读取 | host 插件**优先官方 service**（`sessionQuery` SQLite FTS5 / `credentials`），可选注入；fallback 直读文件 + 格式版本检查 | 纯直读：文件格式随上游变（脆弱）；纯 service：API 面未全部公开（需探测） |
| D6 | 数据目录 | `~/.dsh-skins` 保留为用户数据根；settings 命名空间 `dshpp`/`dshpp-enhancer` 存偏好 | 整体迁移到 `$DSH_HOME`：破坏现有用户数据与文档路径引用，收益小 |
| D7 | CDP 通道 | P3 完成后新壳路径**停用** CDP；保留 legacy 模式（`config.json` `launcher_mode` 切换）至 P5 | 直接删除：旧壳用户（仍用 9222）失去能力 |
| D8 | Python 栈退役 | 分阶段：P1 起 Python 面板只读镜像 → P3 增强器 in-page 后 CDP 停用 → P5 server/tray/updater/desktop_app 全部退役，CLI 保留薄壳 | 一次性退役：无回退窗口，风险高 |
| D9 | 发布渠道 | 私有（本地 file:// 安装）；dshmarket 可用后对齐发布（包名/版本规范提前满足） | 现在发 dshmarket：市场无可用安装器（dev 阶段） |
| D10 | "桌面即插件"理解 | `dsh-plugin-desktop` 已是插件 → 本方案=让 DSH++ 两插件对齐同一范例；不做核心 harness 插件化 | 若用户意图是后者（harness core 可插拔），超出 C4 与 DSH++ 范围，另立方案 |

---

## 5. 现有功能逐项解析（实现 → 缺陷 → 优化方向）

> 16 项功能。每项：**现状实现**（一句话）/ **缺陷**（可验证的问题）/ **优化方向**（映射到 §3 目标架构）。

### 5.1 主题/皮肤管理（theme_engine + themes 分区 + switch/restore/rebuild + preview）

- **现状**：14 参数模板（glow_color/glow_strength/glass_opacity/blur_strength/radius/overlay_color/overlay_alpha/font_scale/force_dark…，TEMPLATE_VERSION 7）生成 CSS 字符串，存 `config.json`；经 CDP 注入 `<style id="dsh-skin-cdp">`；快照预览（`assets/*.css`，93 个内联文件）；漂移检测（120s DOM 指纹 + `selectors.json` region 选择器）。
- **缺陷**：
  1. 完整 CSS 字符串（数 KB）塞进 `config.json`，配置膨胀且 diff 不友好；
  2. 快照预览与真实页面**版本耦合**——新壳每次升级 DOM 变化，快照失真（preview 保真度依赖手动 recapture）；
  3. 依赖 CDP 9222（packaged 硬缺口）；
  4. 漂移检测 120s 轮询，`selectors.json` region 选择器需**手工维护**（新壳 DOM 重构即失效）；
  5. 与官方主题系统（ui-theme settings + brand token + boot palette）**两套并行**，用户同时操作会互相覆盖。
- **优化方向**：
  - 主题 CSS 由 **enhancer/core 的 client 半 in-page 注入**（P3/P4），参数（而非生成物）持久化到 settings 命名空间 `dshpp`，CSS 运行时生成；
  - **实时预览取代快照预览**：面板本就在 GUI 内，主题分区直接对当前页面生效/回滚，无需离线快照（快照降级为"离线设计模式"可选项）；
  - 对齐官方 token：主题输出 = brand token 覆盖（`--dsw-alias-*`）+ region 补丁 CSS 两层，`force_dark`/`font_scale` 等与 ui-theme settings 语义对齐（只叠加不覆盖官方状态）；
  - region 选择器改为**运行时探测 + 版本指纹**（模块内 `probe` API），`selectors.json` 降级为缓存而非唯一真相。

### 5.2 增强器（enhance_engine + enh 分区 + DSHSkin 运行时 + 模块 + 用户脚本）

- **现状**：`_RUNTIME_TMPL`（`window.DSHSkin`，RUNTIME_VERSION 1.0.0）经 CDP 注入；5 内置模块（session-tools/ui-tweaks/input-plus/usage-meter/thinking-zh）；用户脚本 `~/.dsh-skins/enhance/*.js`（frontmatter `// ==DSHSkin==`）；`enhance.json` 状态；权限静态分析（dom/storage/network/eval/cookie/clipboard/navigation 模式扫描，network/eval 高风险）；全局热键注册表。
- **缺陷**：
  1. CDP 依赖（9222 + 外部进程 + 轮询，见 5.11）；
  2. 脚本以**全页面权限**执行，权限分析是**模式匹配**（字符串拼接/编码可绕过，Fabric RFC 也明示 capability ≠ 沙箱）；
  3. 脚本无版本管理（覆盖式保存）、无更新机制、无依赖声明；
  4. 模块通过 DOM 探测（`ready`/`wait` 选择器）挂载，与页面版本强耦合（漂移即失效）；
  5. 存储走 `localStorage` 单命名空间，无配额/清理策略。
- **优化方向**：
  - 增强器 = 独立 client 插件（§3.3）：**in-page 运行时**，`immediately: true` 随页面 boot 装载，彻底去除 CDP 依赖（P3 核心收益）；
  - 权限模型显式化：frontmatter `@permissions` 声明 + host 侧**白名单校验**（未声明的敏感 API 运行时 stub 化拒绝），对齐 Fabric RFC 0001 的静态 manifest 思路；
  - 脚本包化：每个脚本 = 带版本/依赖/权限声明的小包（host 管理、可回滚），为未来 dshmarket 发布做准备（D9）；
  - 长期（可选）：Worker 隔离执行（真实沙箱证据出现前不承诺安全，只做稳定性隔离）；
  - 挂载策略：优先官方 slot 语义（`conversation.input.left/right`、`composer.dock` 等），DOM 探测降级为兜底。

### 5.3 会话管理（session_store + sessions 分区）

- **现状**：zstd-JSONL 会话文件只读解析：列表/搜索/详情/对比/导出(md,json)/全量备份；`delete_sessions` = 备份后物理删除（用户显式授权的破坏性操作）。
- **缺陷**：
  1. **每次列表调用全量重解析**所有会话文件（会话多时秒级延迟，无缓存/增量）；
  2. 文件格式（zstd-JSONL + 内部 schema）随上游变化，**无版本协商**，解析失败即整区不可用；
  3. 无分页/流式（大列表一次性返回）；
  4. `delete_sessions` 物理删除与"只读"原则相悖（虽有备份 + 显式确认，仍是唯一数据破坏路径）；
  5. 备份/导出是文件复制，无版本/校验和。
- **优化方向**：
  - host 插件**优先 `sessionQuery` 官方 service**（SQLite FTS5 索引，增量维护）做列表/搜索；直读文件降级为详情/导出兜底（P1 先探测该 service 公开面）；
  - 解析层加**格式版本探测**（未知版本 → 分区显示"格式不兼容"而非崩溃，并给出版本号）；
  - 列表分页 + 详情按需加载；
  - 删除操作保留（用户既有工作流），但改为"**导出 + 移入回收站目录**"（`~/.dsh-skins/backups/trash/`，可恢复），物理删除仅作二次显式动作；
  - 导出/备份加 SHA-256 校验和与版本元数据。

### 5.4 供应商/凭据（providers 分区 + 备份）

- **现状**：直读 `.credentials.yaml` + `settings.yaml`（`llm-deepseek` + `llm-pi-ai.providers` + `agent-default-model`），API key 掩码显示，备份 = 文件复制。
- **缺陷**：
  1. 文件 schema 耦合（上游字段变更即失效）；
  2. 凭据（含 key 明文）进入 Python 内存与 HTTP 响应（虽掩码，仍是完整读取）；
  3. 无官方 service 路径（`credentials` service 存在但未使用）；
  4. 备份无版本/加密。
- **优化方向**：
  - host 插件优先 `ctx.get('credentials')` 官方 service（可选注入）；fallback 直读 + **只取需要的叶子字段**（不整文件读入内存）；
  - API key 掩码策略保留（前 4 后 4）；
  - 备份 = 导出（掩码版）+ 提示用户用官方凭据管理（新壳有 credentials 体系），DSH++ 只做视图不做凭据管理者。

### 5.5 插件管理（plugin_manager 双轨 + plugins 分区）

- **现状**：Track A（npm profile 依赖 + pnpm，需 DSH 完全退出，900s 超时）/ Track B（file:// 行 + bundle 复制到 `~/.dsh-skins/plugins/`）；`adopt_profile_bundles` origin 探测（market/A/B 三源）；zip-bomb 防护；`registry.json` 自持台账；`restore_managed` 带 `.orig` 备份。
- **缺陷**：
  1. **Track A 已被实证否决**：启动期 `reconcileProfilePnpmWorkspace` 删除 profile 非预期依赖（P0 假证），且要求 DSH 完全退出（文件锁）、耗时不可控；
  2. `registry.json` 自持台账与 profile 实际状态（`package.json` + `cordis.patch.yml`）**双真相**，漂移后市场 UI 报 "no loader entry matched"；
  3. origin 探测启发式（market/A/B）复杂且随 dshmarket 版本变化（live profile 1.47.0 vs desktop vendored 1.38.1）；
  4. zip-bomb 防护参数硬编码，无审计日志；
  5. 安装/卸载/开关三条路径与官方 `dsh plugin` 语义**平行**，用户心智两套。
- **优化方向**：
  - **单轨化**：file:// 管理块为唯一主路径（C2 锁定，D1）；npm 包安装补集走 `desktopPnpm.runPlugin(['add', pkg@exact])`（官方语义、内置 pnpm、单操作互斥）；**Track A 退役**；
  - **真相源 = profile 本身**：插件列表实时从 profile `package.json` + `cordis.patch.yml` managed block 推导，`registry.json` 降级为安装元数据（来源/版本/时间戳）缓存；
  - origin 探测简化：只区分"managed block 内（DSH++ 管理）/ 外部"；
  - zip-bomb 防护保留（参数化 + 审计日志到 host 日志）；
  - 开关 = managed block 行 `disabled` 字段（patch 语义支持），与官方行级 disabled 一致。

### 5.6 脚本市场（market/ 7 脚本 + market 分区）

- **现状**：7 个内置脚本（copy-code-button/focus-mode/message-index/night-schedule/prompt-library/selection-counter/wide-screen，frontmatter `// ==DSHSkin==`），一键安装 = 复制到 `enhance/` + 权限分析展示。
- **缺陷**：
  1. 本地"市场"与官方 dshmarket **完全脱节**（无更新/无版本/无卸载状态）；
  2. 安装即覆盖（无版本记录，更新无 diff）；
  3. 权限分析结果只展示不执行（见 5.2-2）；
  4. 无远程源（纯内置）。
- **优化方向**：
  - 7 内置脚本升级为 **enhancer 内置模块包**（随 bundle 携带、版本化、权限声明齐全），市场分区改为"内置模块 + 用户脚本 + （未来）dshmarket 源"三栏；
  - 用户脚本包化（5.2）；
  - dshmarket 可用后（D9）：market 分区对接官方 provider（机器级开关），DSH++ 脚本按官方目录规则发布。

### 5.7 概览（home 分区）

- **现状**：聚合 DSH 状态 / 注入状态 / 模板健康 / 增强统计 / 快捷操作 / 最近活动，多 API 轮询（ping/cdp-status/drift/status）。
- **缺陷**：
  1. 状态语义全部**以 CDP 为中心**（"已注入/未注入"= 9222 可见性），新壳 in-page 模式下语义失效；
  2. 多接口轮询（3-6s 级），无事件驱动；
  3. 快捷操作（launch/restart-dsh）与 C1 约束贴得最近，需显式用户确认流。
- **优化方向**：
  - 状态重定义：**in-process 查询**（host 侧 profile 状态 + client 侧 enhancer 心跳），"注入状态"= enhancer 插件是否装载 + 模块激活计数；
  - 轮询降级为 on-demand + 用户手动刷新（in-page 后无外部 watcher 可轮询）；
  - 快捷操作：launch/restart 一律**显式确认对话框**（C1），restart 走 `desktopProfiles.select()`/用户手动重启（C3）。

### 5.8 诊断（doctor/probe/drift + diag 分区）

- **现状**：doctor 6 节（env/cdp/theme/enhance/plugins/logs）；probe = CDP 逐 region 选择器验证；drift = 120s 指纹对比；快照 recapture 走独立脚本 `tools/capture_dsh_snapshots.py`。
- **缺陷**：
  1. probe/drift 双 CDP 依赖；
  2. 快照 recapture 是**手工工具**（非面板闭环）；
  3. 诊断导出自研（无官方归档格式）；
  4. doctor 检查项与 CDP 生命周期耦合（9222 不可达时大面积误报）。
- **优化方向**：
  - 诊断重定义为 **in-host 自检**：profile 状态 / managed block 一致性 / 插件台账 vs 实际 / 数据目录可写性 / settings 完整性（无需 CDP）；
  - 页面侧诊断（region 探测结果、模块激活日志）由 enhancer client 半回传；
  - 对接新壳**官方诊断导出**（`dsh-desktop --export-diagnostics` / `/api/desktop/diagnostics/export`）：DSH++ 分区提供"导出新壳诊断"按钮 + 自研 DSH++ 诊断 ZIP；
  - probe/drift 保留为 legacy（旧壳模式），新壳模式下隐藏。

### 5.9 日志（logs 分区 + page-logs）

- **现状**：DSHSkin 运行时日志（`logs.json`，内存 + 文件）+ 页面错误收集（CDP 8s watcher）+ `redact_secrets` 脱敏。
- **缺陷**：
  1. 页面错误收集 CDP 依赖（新壳下 9222 不可用即失效）；
  2. 日志自管文件（无轮转/无级别/与 host 日志体系分离）；
  3. 脱敏是关键词替换（可被变体绕过）。
- **优化方向**：
  - enhancer 运行时日志 → 同源 fetch 回传 host（结构化：ts/level/module/msg），host 落文件（轮转 + 级别）；
  - 页面错误 → enhancer client 半 `window.onerror`/`unhandledrejection` 捕获回传（替代 CDP 收集）；
  - 脱敏保留（正则升级 + 长度启发式），并明示"脱敏是展示层，不是安全边界"；
  - 长期：对齐 host logger（Cordis logger 体系）。

### 5.10 设置（settings 分区）

- **现状**：CDP 端口 / harness root / 启动模式 / desktop exe / 自启开关，存 `config.json`。
- **缺陷**：
  1. 多数设置**只对 CDP 时代有意义**（cdp port、relaunch-with-debug 等），新壳 in-page 后成为死项；
  2. `config.json` 混合了主题/启动/路径多类状态，无 schema 校验；
  3. 自启走注册表项（`HKCU Run`），与新壳自身自启语义无对齐。
- **优化方向**：
  - 设置迁移到 settings 命名空间 `dshpp`（schema 化：`{skinRoot, dshHome, panel:{...}, legacy:{cdpPort, launcherMode, desktopExe}}`）；
  - CDP 相关项收进 `legacy` 组（新壳默认模式下隐藏）；
  - 自启：新壳路径下**退役**（由 DSH Desktop 自身管理自启），仅 legacy 模式保留。

### 5.11 CDP 注入通道（cdp_skin）——DSH++ 当前核心传输层

- **现状**：Electron `--remote-debugging-port=9222` + WebSocket `Runtime.evaluate` 注入样式/运行时；TargetSessions 多页同步；SkinWatcher 6s 轮询发现新页面；`find_page_targets` 已适配新壳 loopback 页面选择。
- **缺陷**：
  1. **packaged 新壳唯一硬缺口**：必须带 `--remote-debugging-port=9222` 重启（`relaunch_with_debug`），且调试端口对本地任意进程开放（安全面）；
  2. 轮询式（6s 发现、120s 漂移、8s 错误），非事件驱动；
  3. 多页同步需手动维护 TargetSessions 状态机；
  4. 注入时序脆弱（页面重载/导航后需重新注入，watcher 竞态）；
  5. 与 C1 约束的张力：relaunch-with-debug 是"用户发起"的边界最模糊的动作。
- **优化方向**：
  - **整体退役（新壳路径）**：主题（P4）+ 增强器（P3）全部 in-page 后，CDP 通道对新壳不再需要；
  - 保留为 **legacy 模式**（`launcher_mode: legacy`，旧壳/仍用 9222 的用户），默认关闭，P5 后随 Python 栈退役；
  - `find_page_targets` 的 loopback 页面选择逻辑移植为 enhancer 的"页面环境自检"（诊断用）。

### 5.12 桌面应用（desktop_app + tray + 自启）

- **现状**：pywebview（edgechromium）→ `msedge --app` → 普通浏览器三级降级窗口；单实例 mutex（`Local\DSHPP_SingleInstance`）；pystray 托盘；窗口关闭 → 托盘。
- **缺陷**：
  1. pywebview/edgechromium 依赖（Windows 专属、版本漂移）；`msedge --app` 降级路径 UX 差；
  2. **独立窗口 = 与 GUI 双 UX**（用户要在 DSH Desktop 之外再开一个窗）；
  3. pystray 可选依赖，缺失即无托盘；
  4. 单实例 mutex 与 DSH Desktop 单实例锁双套。
- **优化方向**：
  - **整体退役（P5）**：GUI（DSH Desktop）本身就是窗口；面板经浮动按钮/`shell.overlay` 徽章进入（§3.2）；
  - 托盘/自启/通知：由新壳自带能力接管（托盘含 profile 选择器、原生通知、`/api/desktop/*` 路由）；
  - 过渡期（P1-P4）：Python 窗口可继续作为 legacy 入口，但面板数据源逐步切到插件 API（同一份后端）。

### 5.13 自动更新（updater）

- **现状**：GitHub Release 检查（`UPDATER_REPO=''` **未配置，死代码**）；手动替换 exe（锁文件问题）。
- **缺陷**：未配置不可用；手动替换 exe 有文件锁风险；无通道/版本回显机制。
- **优化方向**：**整体退役（P5）**——DSH++ 作为插件的"更新"= 安装器重装新版 bundle（file:// 模式，版本目录切换）；DSH Desktop 本体更新走新壳官方更新系统（`X-DSH-Desktop-Channel` 通道协议）。

### 5.14 CLI（dsh-skin.py，20+ 子命令）

- **现状**：install/switch/restore/probe/doctor/enhance/update/settings 等；连 server token 调用 API 或直操作文件。
- **缺陷**：
  1. 连字符文件名 → `importlib` 加载 hack（`dsh-skin.py` 不能正常 `import`）；
  2. 与 server 双路径（token 可用走 API，不可用直操作）→ 行为分叉；
  3. 多数子命令语义将被插件 API 覆盖。
- **优化方向**：
  - 保留为**薄壳**（P5）：新命令 = `dshpp` 小 CLI（JS/Node，随 bundle 发布）调用插件 API 或直接操作数据目录；`dsh-skin.py` 保留 legacy 子命令（probe/doctor 旧壳模式）直至退役；
  - 插件安装/卸载/开关类命令对齐官方 `dsh plugin --profile desktop add|remove|update` 心智（D1）。

### 5.15 安全模型（SERVER_TOKEN + 403 origin）

- **现状**：`server.token` 注入 panel HTML 引导（**字符串拼接**进 HTML）；POST 403 origin 检查；`redact_secrets` 展示脱敏。
- **缺陷**：
  1. **token 内嵌 HTML 源码**——任何本地进程读 HTML 源即得 token（本地提权面）；
  2. token 经 URL query/参数传递（日志泄漏面）;
  3. origin 检查只查 `Origin` 头（可伪造）；
  4. 鉴权是 Python 层自研，无审计。
- **优化方向**：
  - **整体退役（P1）**：in-host webServer 路由天然走官方 **connection trust + 403 浏览器围栏**（`DesktopWebServer.permits()`，loopback GUI 同源 fetch 通过，外部拒绝）——比自研 token 更强且零配置；
  - 面板不再需要 token（同源）；脱敏保留（展示层）；
  - 审计：写入类路由（switch/restore/install/delete）记 host 审计日志（ts/操作/目标/结果）。

### 5.16 数据目录（~/.dsh-skins）

- **现状**：单根目录承载 config/enhance/selectors/logs/token/backups/exports/plugins/enhance 九类数据。
- **缺陷**：
  1. 与 DSH home（`$DSH_HOME`）**双根并行**，路径解析散落各模块；
  2. `registry.json` 双真相（见 5.5-2）；
  3. 无数据版本/迁移框架（上游升级后无自动修复路径）；
  4. `server.token` 权限位无强制（本地其他用户可读）。
- **优化方向**：
  - 保留为用户数据根（D6），但加**版本化数据层**：`schema_version` 字段 + 迁移函数链（host 侧启动时执行，失败不阻塞只告警）；
  - 偏好类迁入 settings 命名空间（`dshpp`/`dshpp-enhancer`），文件只留用户数据（脚本/备份/插件）；
  - `registry.json` 降级（5.5）；token 文件退役（5.15）。

### 5.17（横切）代码组织与工程质量

- **现状**：`server.py` 2206 行 / `plugin_manager.py` ~1900 行 / `panel.html` 3834 行单文件；`__pycache__` 多 Python 版本残留；`PROJECT_ANALYSIS.md` 过期（旧路径/旧行数）。
- **缺陷**：巨石模块（无包结构、路由与逻辑同文件）；无前端构建（单文件 HTML，无测试）；文档与代码漂移。
- **优化方向**：
  - Python 侧**不再扩张**（P1 起冻结新功能，只做维护），全部新能力落插件（JS）；
  - 插件侧：host 半按域分文件（theme/session/plugins/enhance 模块），client 半按分区分文件，带构建与最小测试；
  - 文档：`PROJECT_ANALYSIS.md` 标记 superseded by 本方案 + `DSHPP_PLUGINIZATION.md`。

---

## 6. 分阶段路线图

> 原则：每阶段**可独立验收、可回退**（managed block 还原 + `.orig` 备份）；DSH++ Python 栈全程保持可用（回退通道）直到 P5。

### P0 — 探针与安装验证（✅ 已完成，2026-09-17）

- 完成物：`dsh-plugin-dshpp@0.1.0` 探针 bundle（health/capabilities/tree + console + 浮动按钮）；`installer-core.mjs`（file:// 模式：bundle 复制 + managed block + registry + 快照）；profile 直装假证（§9.5）。
- 遗留：dshpp managed block 当前**不在 live profile**（探针已卸），P1 起重新安装新版。

### P1 — `dsh-plugin-dshpp` 完整 bundle（只读面 + 面板移植）

- **范围**：
  1. host 半：`/dshpp/*` 只读 API 全量（status/themes/sessions/providers/enhance/market/plugins/logs/settings/tree），数据访问 = 官方 service 优先 + 文件 fallback（5.3/5.4 方向落地）；
  2. client 半：浮动按钮（保留）+ `/dshpp` 面板页（panel.html 10 分区移植，先单文件 SPA 换皮）；只读分区（概览/主题视图/会话/供应商/增强状态/日志/插件树）全功能；
  3. settings 命名空间 `dshpp`；审计日志骨架；
  4. 安装器升级：P1 bundle 版本化安装（替换 P0 探针）。
- **退出标准**：GUI 内打开 `/dshpp`，只读分区数据与 Python 面板一致（抽查 10 分区 diff）；重启后存活；卸载还原 profile；不依赖 9222。
- **不做**：任何写入路由、CDP 改动、enhancer。

### P2 — 写入路径与插件管理单轨化

- **范围**：
  1. 主题写入：switch/restore/rebuild（in-page 生效暂仍经 CDP legacy 通道，若 legacy 开启；否则提示"需安装 enhancer 或 legacy 模式"）；
  2. 增强器状态 CRUD（脚本安装/启用/删除/备份）；
  3. 插件管理单轨化（5.5）：file:// 管理块为主 + `desktopPnpm.runPlugin` 可选补集；Track A 退役；真相源 = profile；
  4. 市场安装（内置 7 脚本 → 用户脚本目录）；
  5. `restart-request` 路由：**仅提示 + 用户手动重启**（C1/C3），对接 `/api/desktop/restart`（用户显式点击确认）；
  6. 诊断 in-host 自检 + 官方诊断导出按钮（5.8）。
- **退出标准**：GUI 内完成 主题切换/脚本安装/插件安装-开关-卸载/重启请求 全流程；profile 状态与 Python 版一致；所有破坏性操作有确认 + 备份 + 审计。
- **依赖**：P1；`desktopPnpm` 探测（若不可用则该补集禁用，file:// 仍闭环）。

### P3 — `dsh-plugin-dshpp-enhancer`（in-page 增强器，**核心阶段**）

- **范围**：
  1. enhancer bundle 构建：client 半 = DSHSkin 运行时（`_RUNTIME_TMPL` JS 化）+ 5 内置模块（原样迁移）+ 模块装载器 + `window.onerror` 日志回传；
  2. host 半：enhance 状态/脚本包管理/权限白名单校验（5.2）；
  3. 新壳路径 **CDP 停用**（`launcher_mode: new-shell` 默认不再 relaunch-with-debug）；legacy 模式保留（5.11）;
  4. enh 分区对接 enhancer host API（状态/模块/脚本全功能）；
  5. 现有 7 市场脚本 + 5 内置模块回归验证（API 面零改动承诺）。
- **退出标准**：**不带 9222** 启动 DSH Desktop，5 内置模块 + 任意 1 用户脚本全部生效；legacy 旧壳模式仍可注入；enhancer 卸载后页面无残留（style/热键/DOM 全清）。
- **关键风险**：DSHSkin 运行时与页面版本耦合（选择器漂移）→ 模块内 `ready`/`wait` 事件 + 诊断回传兜底；client bundle purity gate（无 Node API，全部数据走同源 fetch）。

### P4 — 主题 in-page 化与 token 对齐

- **范围**：
  1. 主题 CSS 由 enhancer（或 core client 半，若 enhancer 未装则 core 降级提供基础注入）in-page 生成注入，**取代 CDP style**；
  2. 参数持久化 settings 命名空间（生成物不落 config.json）；
  3. 实时预览（主题分区直接生效/回滚）；快照预览降级为可选离线模式（5.1）；
  4. brand token 叠加层（`--dsw-alias-*`）+ region 运行时探测（`selectors.json` 降级为缓存）。
- **退出标准**：不带 9222 完成 主题切换/参数调节/重建/还原 全流程；主题与官方 ui-theme 共存不互相覆盖；漂移场景下 region 探测结果正确回传。
- **依赖**：P3（in-page 通道）。

### P5 — Python 栈退役

- **范围**：
  1. server.py / desktop_app.py / tray.py / updater.py 退役（功能全部在 GUI/CLI 可用后）；
  2. CLI 薄壳化：`dshpp` JS CLI（install/uninstall/status/doctor）+ `dsh-skin.py` 仅留 legacy 子命令；
  3. 数据迁移工具（schema_version 迁移链，5.16）；
  4. 文档收口：README v3（新壳优先、legacy 模式附录）；`PROJECT_ANALYSIS.md` 标记 superseded；
  5. （可选）dshmarket 发布准备（D9，视市场可用进度）。
- **退出标准**：DSH Desktop 日常使用**零 Python 进程**；legacy 旧壳用户仍可走 `launcher_mode: legacy`（Python 栈最小集）；数据迁移前后 diff 校验通过。

---

## 7. 风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|------|------|------|
| R1 | 启动期 pnpm reconcile 删除 profile 依赖 | 插件消失 | **file:// 模式（已锁定/已实证）**；npm 补集走官方 `dsh plugin` 语义（reconcile 是它的正常工作方式） |
| R2 | packaged 构建无 HMR/patchReload | 插件变更需重启 | 安装器完成后**显式提示用户重启**（面板 + 托盘）；绝不静默重启（C1/C3） |
| R3 | 403 浏览器围栏 | 面板页被外部访问拒绝 | GUI 同源 fetch 天然通过；这正是目标安全语义（5.15）；诊断文档说明 |
| R4 | client bundle purity gate | client 半误用 Node API 构建失败 | 构建期 purity gate 即防线；client 半零 Node 依赖，数据全走同源 fetch |
| R5 | session/credentials 文件/service 格式漂移 | 分区不可用 | 官方 service 优先 + 格式版本探测 + "不兼容"优雅降级（5.3/5.4） |
| R6 | dshmarket 处于设计阶段 | 无法走官方市场发布 | file:// 主路径不依赖市场；D9 发布后置 |
| R7 | DSHSkin 运行时与页面 DOM 版本耦合 | 模块挂载失败 | 事件驱动挂载（ready/wait）+ 诊断回传 + legacy 回退；长期官方 slot 对齐 |
| R8 | 权限分析可绕过 | 用户脚本越权 | 显式权限声明 + 运行时白名单（未声明即拒绝）；明示"非安全沙箱"（对齐 Fabric 安全边界表述） |
| R9 | 旧壳用户能力回退 | 仍用 9222 的用户 | legacy 模式保留至 P5（`launcher_mode` 切换）；文档双轨说明 |
| R10 | 双插件安装顺序/版本耦合 | 核心与 enhancer 版本不匹配 | 各自独立安装（无硬依赖）；API 版本协商（`/capabilities` 含 runtime_version）；不匹配时降级提示 |
| R11 | live profile 的 dshmarket(1.47.0) 与 desktop vendored(1.38.1) 版本差 | 市场行为不可预期 | 不依赖 market UI 管理 DSH++ 插件（managed block 自治）；origin 探测简化（5.5） |
| R12 | 安装构建 provenance 疑问（`desktop-runtime-state.json` 链接旧检出 `D:\DSH\deepseek-harness`） | 版本认知混乱 | 本方案按**已安装 2.0.11 实体**为准；provenance 记录为开放问题（§9-5） |

---

## 8. 验收标准（总）

1. **功能对等**：P5 前，Python 面板每个只读分区在 GUI 面板有等价视图（数据一致，抽查 diff 通过）；每个写操作在 GUI 可完成（含确认/备份/审计）。
2. **零 9222**：P4 后，新壳全流程（主题 + 增强器 + 管理）**不需要** `--remote-debugging-port`。
3. **插件规范**：两个 bundle 满足官方两层模型（`docs/plugin-development.md`）：只依赖公开 contract、声明清晰（inject/slot 显式）、可回滚（generation dispose 全清理）、双栖安全（`dsh web` 环境不崩溃）。
4. **安装/卸载闭环**：file:// 安装器 安装→重启→生效→更新→卸载→profile 还原，全程无人工编辑 profile；`.orig` 备份可恢复。
5. **约束合规**：无任何自动拉起/自愈 DSH 的代码路径（C1）；无 pnpm profile 直装（C2）；变更生效仅提示重启（C3）；不改 `D:\DSH\dsh-desktop`（C4）。
6. **回退通道**：P5 前，任意阶段可还原到"Python 栈全功能可用"状态。

---

## 9. 开放问题（需用户确认）

| # | 问题 | 默认倾向 |
|---|------|---------|
| Q1 | 面板入口：仅浮动按钮，还是加 `shell.overlay` 状态徽章 / 设置页入口？ | 浮动按钮 + 可选徽章（P1 只做按钮，徽章 P2 评估） |
| Q2 | 面板 UI 技术：直接移植现有单文件 SPA（快、零学习成本）还是 React 重写（对齐新壳、可维护）？ | P1 移植（保进度），P2+ 按分区逐步 React 化 |
| Q3 | legacy（旧壳 CDP）模式保留到何时？是否保留旧壳用户？ | 保留至 P5；若确认无旧壳用户，P3 后即可退役 CDP |
| Q4 | 数据目录：`~/.dsh-skins` 原地保留（默认）还是迁到 `$DSH_HOME`？ | 原地保留 + 版本化（D6） |
| Q5 | dshmarket 发布意愿？（市场可用后是否发布 dshpp/enhancer/脚本） | 包结构提前满足规范，发布待市场可用（D9） |
| Q6 | "桌面变插件"意图确认：本方案理解为"DSH++ 对齐 dsh-plugin-desktop 范例"；若意图是**核心 harness 本身插件化**（harness core 可插拔），超出 C4/DSH++ 范围，需另立上游方案。 | 按前者执行 |
| Q7 | 已安装 2.0.11 构建的 provenance（runtime-state 链接旧检出 `D:\DSH\deepseek-harness\apps\desktop\.desktop-build`）是否需要核实？ | 记录待核实；不影响本方案（以实体行为为准） |

---

## 附录 A：Python 文件 → 插件模块映射（P5 退役对照）

| Python 文件 | 去向 | 阶段 |
|------------|------|------|
| `server.py`（路由层） | 退役 → `dsh-plugin-dshpp` host 半（webServer 路由） | P1-P2 并行，P5 退役 |
| `server.py`（watcher 层） | 退役 → enhancer client 半事件回传 + host 自检 | P3 |
| `theme_engine.py` | `dsh-plugin-dshpp` host 半（参数/生成）+ client 半（in-page 注入） | P2/P4 |
| `enhance_engine.py` | `dsh-plugin-dshpp-enhancer`（host 管理 + client 运行时） | P3 |
| `session_store.py` | `dsh-plugin-dshpp` host 半（service 优先 + 文件 fallback） | P1 |
| plugin 凭据读取（session_store 内） | `dsh-plugin-dshpp` host 半（credentials service 优先） | P1 |
| `plugin_manager.py`（Track B） | `dsh-plugin-dshpp` host 半（managed block 单轨） | P2 |
| `plugin_manager.py`（Track A） | **删除**（实证否决） | P2 |
| `cdp_skin.py` | legacy 模块（仅旧壳），P5 删除 | P3 降级 |
| `dsh_env.py` | 拆分：新壳探测 → 退役（host 进程内已知环境）；legacy 路径解析保留至 P5 | P5 |
| `desktop_app.py` / `tray.py` | **退役**（新壳窗口/托盘接管） | P5 |
| `updater.py` | **删除**（死代码） | P5 |
| `dsh-skin.py` | 薄壳保留（legacy 子命令）+ 新 `dshpp` JS CLI | P5 |
| `panel.html` | 移植为 `/dshpp` 页面（P1），逐步 React 化 | P1+ |
| `market/` 7 脚本 | enhancer 内置模块包（版本化） | P3 |
| `enhance-modules/` 5 模块 | enhancer bundle 携带 | P3 |
| `tests/`（21 文件） | 移植为插件测试（只读面 diff 用例保留） | 各阶段 |

## 附录 B：新壳官方能力速查（插件可用面）

| 类别 | 名称 | 用途 |
|------|------|------|
| Host service | `webServer` | 路由注册（`/dshpp/*`） |
| Host service | `settings` | 命名空间（`dshpp`/`dshpp-enhancer`） |
| Host service | `credentials`（可选探测） | 凭据视图 |
| Host service | `sessionQuery`（可选探测，SQLite FTS5） | 会话列表/搜索 |
| Host service | `subprocess` / `timer` | 进程/定时器（dispose-aware） |
| Host service（Desktop） | `desktopProfiles` | 当前/发现/切换 profile（select=重启边界） |
| Host service（Desktop） | `desktopPnpm` | `runPlugin`（官方 `dsh plugin` 语义）/`installPlugin` |
| Client service（Desktop） | `desktopWindow` | mode/platform/material/safeAreaInsets |
| Client 机制 | `dsh.client` 声明 + `__ModuleLoader__` | client 半装载（purity gate） |
| Slot | `shell.overlay` | additive 帧级浮层（徽章/状态） |
| Slot | `conversation.input.left/right`、`composer.dock` | 增强器模块官方挂载点（长期） |
| 主题 | `ui-theme`（settings + boot palette + brand token） | 主题对齐（P4） |
| 围栏 | `connection` trust + 403（`DesktopWebServer`） | 面板页安全（替代 token） |
| 诊断 | `/api/desktop/diagnostics/export`、`dsh-desktop --export-diagnostics` | 官方诊断导出 |

## 附录 C：参考文档

| 文档 | 位置 |
|------|------|
| 新壳架构报告（本方案 §2.2 来源） | `D:\DSH\DSH++\dsh-desktop-architecture-report.md` |
| 插件化 P0 记录（安装模式/假证） | `D:\DSH\DSH++\DSHPP_PLUGINIZATION.md` |
| DSH++ 现状文档 | `D:\DSH\DSH++\README.md`（v2.1） |
| 旧版分析（部分过期，P5 标记 superseded） | `D:\DSH\DSH++\PROJECT_ANALYSIS.md` |
| 新壳插件开发指南 | `D:\DSH\dsh-desktop\docs\plugin-development.md` |
| 新壳插件 service contract（权威） | `D:\DSH\dsh-desktop\dsh-plugin-desktop\docs\plugin-services.md` |
| 新壳架构 | `D:\DSH\dsh-desktop\docs\architecture.md` |
| 插件生态倡议（"桌面即插件"） | `D:\DSH\dsh-desktop\docs\plugin-ecosystem.md` |
| Cordis 框架入门 | `D:\DSH\dsh-desktop\deepseek-harness\docs\cordis-primer.md` |
| live profile 状态（P0 时点） | `D:\DSH\DSHdata\.dsh\profiles\desktop\{package.json,cordis.patch.yml}` |
