# DSHSkin 项目整体架构分析报告

> 分析范围：`D:\DSH\DSH-skin` 全仓库（Python 核心 8 个模块、CLI 入口、HTTP 后端、两个 HTML 前端、增强/市场 JS、bat 启动器、打包 spec、资源与工具目录、构建产物）。
> 报告基于逐文件通读，代码行号与常量均以仓库现状为准。

---

## 1. 项目定位与核心设计原则

**DSHSkin**（v2.0，`__version__='1.0.0'`）是一个为 **DeepSeek Harness 桌面端**（Electron，开发模式）提供「**换肤 + 增强 + 会话/凭据浏览 + 插件**」能力的本地工作台。它是纯本地、纯 Python 驱动的工具，不改动目标应用一行代码。

三条贯穿全仓库的铁律（README 与代码一致）：

| 原则 | 实现方式 |
|---|---|
| **零侵入（Zero-invasive）** | 不修改 `D:\DSH\deepseek-harness` 下任何文件；所有写操作只落在 `~/.dsh-skins/`。对 DSH 数据（会话、凭据、settings）**只读**。 |
| **CDP-only 注入** | 唯一注入通道是 Electron 开发模式暴露的 `--remote-debugging-port=9222`。通过 WebSocket 注入 `<style id="dsh-skin-cdp">` 与 `window.DSHSkin` 运行时。依赖 `websocket-client`（缺失时优雅降级而非报错）。 |
| **完全可逆（Reversible）** | 「还原官方样式」= 移除注入的 style/运行时；增强模块全部通过 `new Function` 隔离 + `<style>` 注入，关掉即还原。 |

**历史遗留（重要背景）**：项目由 **TraeSkin** 更名而来。证据链：
- `build\TraeSkin`（PyInstaller 中间目录仍用旧名）；
- `DSHSkin.spec` 图标候选回退到 `traeskin-icon.ico`；
- `panel.html` / `preview.html` 中遍布 `data-page-node-id="…"` 属性（Trae-SOLO 代码生成器残留）。

这解释了为什么品牌名（DSHSkin）与部分内部产物名（TraeSkin）不一致——属于更名未完成清理，不影响功能。

---

## 2. 技术栈与目录总览

```
D:\DSH\DSH-skin\
├── dsh-skin.py            # CLI 主入口（811 行）：install/switch/doctor/probe…
├── server.py              # HTTP 后端（1352 行）：面板 API + 守护线程
├── dsh_env.py             # 环境/配置/路径/进程探测（305 行）
├── theme_engine.py        # 主题 CSS 生成引擎（627 行）
├── cdp_skin.py            # CDP 注入 + 守护线程（362 行）
├── enhance_engine.py      # 增强模块加载/运行时（537 行）
├── session_store.py       # 会话/凭据只读访问（332 行）
├── plugin_manager.py      # 插件安装 + 注入守护（486 行）
├── panel.html             # 单文件 SPA 工作台（3309 行，无框架/无构建）
├── preview.html           # 主题保真预览页（665 行，iframe+postMessage）
├── 启动DeepSeekHarness.bat / 启动面板.bat / 导入主题包.bat / 还原官方样式.bat
├── _ensure_deps.bat       # Python 解析 + 依赖自检（59 行）
├── DSHSkin.spec           # PyInstaller onefile 打包配置（77 行）
├── requirements.txt       # websocket-client / zstandard / PyYAML
├── enhance-modules\       # 内置增强 JS：session-tools / ui-tweaks / input-plus / usage-meter
├── market\                # 脚本市场 JS（7 个，带 // ==DSHSkin== front-matter）
├── themes\keus\           # 内置主题（keus.zip + background.png + meta.json，builtin:true）
├── assets\                # 真实 DSH 面板快照 + 捕获的真实 CSS（见 §6.4）
├── tools\                 # 开发工具：快照抓取/探针/启动器生成/e2e（6 个）
├── dist\DSHSkin.exe       # 已构建的 onefile 可执行
└── .workbuddy\            # 早期会话产物（与主逻辑无关，可忽略）
```

**运行时依赖**（`requirements.txt`）：
- `websocket-client>=1.6.0` —— CDP 通道核心，缺失则注入不可用（降级）。
- `zstandard>=0.22.0` + `PyYAML>=6.0` —— v2 会话/凭据解析，缺失则相关功能降级而非崩溃。

**Node 要求**：Node ≥22 / pnpm 11（`resolve_node`/`resolve_pnpm` 用于拉起 DSH）。

---

## 3. 总体架构（分层视图）

```
┌──────────────────────────────────────────────────────────────────────┐
│  表现层  panel.html (SPA, 127.0.0.1:8765)  ·  preview.html (保真预览)  │
│            无框架/无构建 · 纯 JS + 内联 CSS · ?view= 深链 · ?app=1      │
└───────────────▲──────────────────────────────────────────────────────┘
                │ fetch /api/*（_origin_ok 跨域拦截，403 记录）
┌───────────────┴──────────────────────────────────────────────────────┐
│  服务层  server.py  (ThreadingHTTPServer :8765)                       │
│  · 静态资源服务（MIME 映射 html/css/js/png/jpg/svg/json/woff2）         │
│  · API：switch/restore/rebuild/probe/selectors/settings/cdp-apply/    │
│          restart-dsh/theme-params/ping/enhance-apply/repair           │
│  · 守护：cdp_watcher(6s) · drift_watch · page_error_watch · source_watcher · 插件注入 │
│  · 安全：永不自动拉起/重启 DSH（自愈链路已按用户要求移除，启动只能用户手动）  │
└───────────────▲──────────────────────────────────────────────────────┘
                │ importlib 复用（连字符文件名 workaround）
┌───────────────┴──────────────────────────────────────────────────────┐
│  核心逻辑层  dsh-skin.py (CLI，同一份核心函数被 server 复用)            │
│  install/switch/params/rebuild/export/restore/remove/pack/cleanup/    │
│  launch/detect/deps/doctor/probe/selectors/enhance/template/migrate   │
└───┬──────────┬───────────────┬──────────────────┬───────────────────┘
    │          │               │                  │
┌───▼───┐ ┌────▼──────┐ ┌──────▼───────┐ ┌────────▼─────────┐ ┌────────▼───────┐
│ dsh_env │ │theme_engine│ │  cdp_skin   │ │ enhance_engine  │ │ session_store │
│ 配置/路径 │ │ CSS 生成   │ │ CDP 注入    │ │ 增强运行时      │ │ 会话只读       │
│ 进程探测 │ │ 选择器覆盖  │ │ 守护线程    │ │ new Function    │ │ 凭据脱敏       │
│ 原子写   │ │ 模板版本   │ │ suppress_   │ │ 模块注册表      │ │ 4 级 home     │
└─────────┘ └───────────┘ │ origin      │ └────────────────┘ └────────────────┘
                          └─────────────┘        ┌──────────────────────┐
                                                 │   plugin_manager    │
                                                 │ 插件安装+注入守护     │
                                                 │ cordis.patch.yml     │
                                                 └──────────────────────┘
```

**关键耦合点**：
- `server.py` 通过 `importlib.util.spec_from_file_location('dsh_skin_main', dsh-skin.py)` 把**连字符文件名**的 CLI 当作模块加载，直接复用其核心函数（避免 shell 出去重新解析，也避免 `import` 连字符的语法问题）。
- `dsh_env.py` 是所有模块共享的「环境事实」单一来源：配置读取、路径解析、CDP 探测、进程拉起。
- 注入链路：`theme_engine` 生成 CSS/标记 JS → `enhance_engine` 生成增强 JS → `cdp_skin` 打包成 bundle → CDP WebSocket 注入 → 守护线程保活。

---

## 4. 核心 Python 模块逐一分析

### 4.1 `dsh_env.py` —— 环境 / 配置 / 路径 / 进程（305 行）
所有其它模块的「地基」。

- **常量**：`APP_NAME='DSHSkin'`、`APP_TAG='dsh-skin'`、`SKIN_ROOT=~/.dsh-skins`（全部用户态数据的根目录）、`DEFAULT_HARNESS_ROOT=r'D:\DSH\deepseek-harness'`、`DEFAULT_CDP_PORT=9222`。
- **配置优先级**（`load_config`/`save_config`）：`config.json` > 环境变量 `DSH_SKIN_*` > 内置默认。写盘走**原子写**（临时文件 + rename），避免半写坏配置。
- **DSH home 四级解析**（`home()`）：
  1. `config.home`
  2. 环境变量 `DSH_HOME`
  3. `<harness>/apps/desktop/.desktop-build/development/home`（开发模式实际位置）
  4. `~/.dsh`（回退）
- **CDP 探测**：`cdp_port()`、`cdp_ready`（探 9222 是否可连）。
- **进程工具**：`resolve_node`/`resolve_pnpm`（找 Node≥22 / pnpm）、`launch_dsh`（拉起 DSH 开发模式）、`detect_report`（环境体检，喂给 `doctor`）。

> 设计意义：把「DSH 在哪、用哪个 Node、CDP 通不通」这些易变事实收敛到一个模块，其余模块不各自硬编码路径。

### 4.2 `theme_engine.py` —— 主题 CSS 生成引擎（627 行）
把「参数」变成「可注入的 CSS + 标记 JS」。

- **`TEMPLATE_VERSION = 6`**：CSS 模板版本号。用于判断旧主题 CSS 是否过期（`theme_staleness`/`needs_rebuild`/`css_template_version`）。
- **`PARAM_SCHEMA`（9 参数）**：`glow_color`、`glow_strength`、`glass_opacity`、`blur_strength`、`radius`、`overlay_color`、`overlay_alpha`、`font_scale`、`force_dark`。
- **`REGIONS`（10 区域）**：frame/sidebar/chat/composer/rightbar/overlay/chat-head 等，每个区域一段 CSS。
- **`generate_marker_js()`**：生成运行时打标 JS——给真实 DOM 打 `data-dsh-skin="frame|sidebar|chat|composer|…"` 标记，作为**稳定的 CSS 选择器契约**。
- **选择器覆盖**：`~/.dsh-skins/selectors.json` 可覆盖默认选择器（由 `probe --apply` / `POST /api/probe` / `POST /api/selectors` 写入），应对 DSH 升级改类名。
- **`INJECT_TEMPLATE` / `DARK_INJECT_TEMPLATE`**：明/暗两套注入模板。

> **稳定选择器策略（核心）**：CSS Modules 的哈希类名（如 `.KDVgQq_frame`）不稳定，随 DSH 构建变化。引擎因此锚定 DSH 暴露的**语义化 data 属性**（`data-slot="conversation.composer.bar"`、`data-phase`、`data-conversation-scroll`、`data-composer-seat`、`data-composer-card`、`data-rightbar-col`、`data-shell-overlay`、`data-chat-flow-kind`、暗色 `body[data-ds-dark-theme]`、token `--dsw-alias-*`）。运行时打标 JS 再叠加一层 `data-dsh-skin=*` 兜底。这是整个项目「升级后仍能注入」的关键。

### 4.3 `cdp_skin.py` —— CDP 注入 + 守护（362 行）
唯一与 Electron 渲染进程通信的模块。

- **常量**：`STYLE_ID='dsh-skin-cdp'`（注入 style 的 id）、`PAGE_MARK='dsh-app://'`（页面 URL 特征，用于定位正确的 page target）。
- **`websocket` 可选导入**：缺失时降级（提示装依赖），不抛异常。
- **`find_page_target`**：在 9222 的 `/json` 列表里找 DSH 的 page target。
- **`launch_dsh` 状态机**：`already-running` / `launched` / `running-without-cdp`（在跑但没开调试端口）/ `launch-fail:`。
- **`class CDP`**：WebSocket 封装，建连必须 `suppress_origin=True`（否则 CDP 拒绝跨域 origin）。
- **守护线程**：后台比对「已注入 bundle 的 hash + `window.DSHSkin.__v` + 标记」，发现过期/丢失就**重新注入**。这是「热生效 + 抗页面刷新」的保活机制。

### 4.4 `enhance_engine.py` —— 增强模块运行时（537 行）
在注入的 `window.DSHSkin` 上提供一套**模块运行时**。

- **路径**：`ENHANCE_DIR=~/.dsh-skins/enhance`、`STATE_FILE=~/.dsh-skins/enhance.json`、`RUNTIME_VERSION='1.0.0'`。
- **模块集合**：`bridge`（常驻桥）、`session-tools`、`ui-tweaks`、`input-plus`、`usage-meter`、`user-scripts`、`custom-css`。
- **运行时 API（`window.DSHSkin`）**：`toast` / `log` / `storage`(localStorage 封装) / `ready` / `on` / `interval` / `wait` / `def`(模块注册)。
- **`new Function` 隔离**：每个增强模块在独立函数作用域执行，单模块异常不污染运行时。
- **`builtin_module_js()`**：把 `enhance-modules/*.js` 内置模块打包进 bundle。
- **`apply_preset`**：预设 `off` / `builtin` / `scripts` / `full` 的开关组合。

### 4.5 `session_store.py` —— 会话 / 凭据只读（332 行）
让面板能「浏览」DSH 的会话与供应商配置，但**绝不写**。

- **`_SESSION_RE = re.compile(r'^(session\.v\d+\.jsonl)(\.zstd)?$')`**：识别 `session.vN.jsonl[.zstd]`。
- **数据布局**：`<home>/sessions/<project-key>/session-<uuid>/session.v3.jsonl.zstd`；凭据 `.credentials.yaml`；设置 `settings.yaml`。
- **`iter_events`**：逐条解析事件（`zstandard` 可选，缺失则只能读未压缩）。
- **凭据/设置解析**：读 `settings.yaml` 的 `llm-deepseek` 路由/baseUrl，**API key 脱敏**（只回显前缀）。
- **四级 `home()`**：与 `dsh_env` 一致。

> 「铁律」：对 DSH 数据只读，任何导出/备份都是**复制出来**，不改源文件。

### 4.6 `plugin_manager.py` —— 插件双轨管理 + 注入守护（~1700 行）
把 DSH 插件装进 DSH 并用「托管块 / 官方 bundle」双轨管理。

- **托管块标记**：`BLOCK_BEGIN='# >>> dshskin:plugins (managed, do not edit inside) >>>'`，`ROW_ID_PREFIX='dshskin-'`。
- **存储**：`STORE=~/.dsh-skins/plugins`、`REGISTRY=plugins/registry.json`。
- **双轨**：A 轨 = 官方 bundle 直装（`pnpm add` 进 `<dsh_home>/profiles/<t>` + `dsh.profile.bundles` 挂名，DSH 启动自加载）；B 轨 = 装配行（库目录拷贝 + `cordis.patch.yml` 托管块 `file:///` Loader 行）。启停/卸载语义各轨独立（A 轨卸载需 DSH 完全退出）。
- **`adopt_profile_bundles()`（新壳适配 + 内置市场感知）**：`list_plugins()` 时幂等扫描 profile `package.json.dependencies`（内置市场 dshmarket 的「已安装」口径 `readInstalled`，不是 bundles——市场里「停用」的插件依赖保留、bundles 摘名，同样要可见可管），把不在册的第三方包收养为 A 轨记录，面板即可启停/卸载。`enabled` 取 name 是否在 `dsh.profile.bundles`；版本/描述取 `node_modules` 下真实包的 `package.json`；`origin`：`.dsh-market/log.ndjson` 有 `install`/`update` 证据 → `market`（内置市场安装，面板显示「市场安装」徽标），否则 `profile`。`ADOPT_SKIP_BUNDLES` 永不收养官方/宿主包（与桌面壳 `IMMUTABLE_BUNDLES` + 市场 `INBOX_BUNDLES` 对齐，含 `@deepseek-ai/dsh-headless`）。已在册的 A 轨记录会补 profile 接线、用磁盘真实包刷新版本/描述、补 `origin`，但**不覆盖**用户已设的 `enabled`。
- **内置市场（dshmarket）状态对齐**：市场持久态在 `<profile>/.dsh-market/`（`state.json` 的 `disabled` 列表 + 分组、`log.ndjson` 事件、`hot-*.yml` 会话级热挂载每次启动清空）。DSH++ 作为外部操作者只做**最小、best-effort、不阻断**的对齐，绝不追加市场形态的补丁行（那是市场自己的 HMR 通道）：
  - **A 轨停用** = bundles 摘名 + `_market_state_sync_disabled(pdir,name,True)`（只动 `state.json` 的 `disabled` 数组，其余字段原样，原子 tmp+rename；无 `state.json` 则跳过）。
  - **A 轨启用** = bundles 挂名 + `state.json` 摘名 + `_clear_market_disable_rows()` 清掉用户补丁层里针对本包插入 id 的顶层 `- id: X / disabled: …` 行（市场停用/启用都会写，包重新挂名后应清掉以免下次启动被强制禁/使能）。
  - **A 轨卸载** = bundles 摘名 + `pnpm remove` + `_strip_dependency` + `state.json` 摘名 + 清陈旧行（包插入 id 在 `pnpm remove` **之前**先收集，因目录删除后读不到）。
  - **id 口径** `_patch_inserted_ids_for_package()` 移植市场 `bundlePatchInsertedIds`（声明的 `dsh.bundle.patch` + 约定根 `cordis.patch.yml`，两源取并）；`_parse_patch_inserted_ids()` 是市场 `parsePatchRows` 的 line-wise 移植（仅 `insert:` 块内嵌套 id，顶层 `- id:` 不算）。
  - **行清理** `_clear_market_disable_rows()` 外科手术式按行删除**顶层**（indent 0）`- id: X` + `disabled: true|false` 对，注释/其余内容/嵌套 insert 块原样保留；无可删返回 False（幂等）。
- **数据根自愈**：`_heal_registry()` 在 `_load_registry()` 中把 B 轨 `record.dir` 指回当前 `STORE`（修复数据根迁移后残留的旧根路径，如 `D:\DATA`）；`_row_entry()` 入口文件缺失时回退到 `STORE` 规范落点。
- **补丁层路径**：`_web_patch_path()` / `_desktop_patch_path()` 的 home 均走 `dsh_env.dsh_home()`（config.dsh_home → env → ~/.dsh）；packaged 桌面端 = `<dsh_home>/profiles/desktop/cordis.patch.yml`（官方 `loadProfileDirectory` 的用户层）。
- **`extract_package`**：解 zip/tgz，**含路径穿越防护**（拒绝 `../`、绝对路径）。
- **注入守护线程 `dshskin-plugin-inject`（0.2s 循环）**：只在「DSH 正在运行 且 目标文件缺失」时，向 desktop 的 `cordis.patch.yml` 写入托管块行 `- insert: [{id: 'dshskin-…', name: 'file:///…'}]`。daemon 仅在文件丢失时重写，不覆盖用户改动。
- **热加载边界**：桌面壳（dsh-plugin-desktop）未接线 `watchUserPatches`（仅 CLI boot 有）——插件层任何改动（装/卸/启停/收养）都需**用户手动重启 DSH Desktop** 才生效；面板操作后以 `maybeShowRestart` 提示。

### 4.7 `dsh-skin.py` —— CLI 主入口（811 行）
面向终端用户的全部能力，也是 `server.py` 复用的核心函数库。

- **子命令**：`install / list / switch / params / rebuild / export / restore / remove / pack / cleanup / launch / detect / deps / doctor / probe / selectors / enhance / template / migrate`。
- **`install` 流水线**：`_validate_meta`（id 必须 `^[a-z0-9_-]+$`，appearance 限 light/dark）→ `_check_image_magic`（PNG/JPG/WebP 魔数校验）→ 大小上限 `MAX_ZIP_SIZE=50MB` / `MAX_IMG_SIZE=20MB` → 解到 `~/.dsh-skins/<tid>/{skin.css, background.png}` → 替换 `{{IMAGE}}` 占位符 → `te.theme_staleness` 过期告警。
- **`apply_theme`**：写 `config['active']=tid`，实际生效靠 CDP bundle（见 §5）。
- **`_migrate_theme`**：对过期 CSS 用当前模板**保参数重建**。
- **`probe(apply)`**：经 CDP 实测选择器存活情况 → 写 `selectors.json` 覆盖（应对 DSH 升级）。
- **`doctor_report()`**：返回 `{version, env, deps, cdp, selectors, selectors_ok, active, themes, overrides, enhance, bat_problems}`；`doctor()` 打印 6 段体检报告。
- **`main()`** 在约 758 行做子命令分发。

### 4.8 `server.py` —— HTTP 后端 + 守护（1352 行）
`panel.html` 的服务端，`PORT=8765`，`LOG_FILE=~/.dsh-skins/logs.json`。

- **打包感知**：`sys.frozen` 时 ROOT 指向 `_MEIPASS`（PyInstaller 临时解包目录）。
- **模块复用**：`importlib.util.spec_from_file_location('dsh_skin_main', dsh-skin.py)` 加载连字符 CLI 模块（workaround）。
- **`seed_builtin_themes()`**：import 时若 config 无主题，自动装 `themes/<id>/<id>.zip`（内置主题落地）。
- **API 路由**（POST）：`/api/switch`、`/api/restore`、`/api/rebuild/<id>`、`/api/probe`、`/api/selectors`、`/api/settings`、`/api/cdp-apply`、`/api/restart-dsh`、`/api/theme-params/<id>`、`/api/ping`（轻量无磁盘 IO，供心跳）、`/api/enhance-apply`、`/api/repair`（注入自检/修复）。
- **安全**：`_origin_ok()` 对跨站 POST 一律 **403 并记录**（防 CSRF 式跨域触发）；静态服务带 MIME 映射 + 路径穿越防护。
- **守护线程（`main()` 启动，全部只读/补注，绝不拉起进程）**：
  - `start_cdp_watcher()`（`WATCH_INTERVAL=6s`，页面 reload / target 重建后补注）
  - `drift_watch_loop()`（选择器漂移巡检，只告警）
  - `page_error_watch_loop()`（页面 error 日志回收）
  - `start_source_watcher()`（DSH++ 自身源码变更自重启，frozen 跳过）
  - `plugin_manager.start_injector()`
- **`cdp_auto_apply()` 安全契约（重点，取代旧 `auto_heal_once`）**：**永不自动拉起 / 重启 DSH**。
  - CDP 就绪 → 注入 `active_bundle()`；
  - 在跑但没开调试端口 → 返回 `running-without-cdp`（面板提示手动「以注入模式重启」）；
  - 未运行 → 返回 `stopped`（面板提示手动「启动 DSH（调试模式）」）。
  - 真正的重启（`/api/restart-dsh`）由 UI 端 `confirmRestart()` 二次确认后触发，`launch_dsh()` 只拉起不杀进程。
  - 自愈链路（`auto_heal_once`/`heal_loop`/`cdp_auto_launch`/`force_launch`）已按用户要求整体移除。
- **`run_ts()`**：包一层核心函数，捕获 `SystemExit`（CLI 函数可能 `sys.exit`）。
- **`themes_payload()`**：输出含 `stale`/`has_image`/`builtin` 字段。
- **market**：`MARKET_DIR=ROOT/market`，`_FM_RE = re.compile(r'//\s*@(\w+)\s*:\s*(.*)')` 解析 `// @key: value` front-matter（只读前 2000 字节 / 20 行）。
- **启动行为**：frozen 且非 `--no-open` → `threading.Timer(1.2, open_browser)` 自动开浏览器。
- **日志**：`MAX_LOG_ERR_REPEAT=6` 抑制重复错误刷屏。

---

## 5. 关键数据流（时序）

**(A) 安装主题**
```
拖 zip 到 导入主题包.bat（或面板上传）
  → dsh-skin.py install
    → _validate_meta + _check_image_magic + 大小校验
    → 解到 ~/.dsh-skins/<tid>/{skin.css, background.png}，替换 {{IMAGE}}
    → 记入 config（themes 列表）
```

**(B) 切换 + 热生效（核心链路）**
```
面板点「立即注入」 → POST /api/cdp-apply
  → cdp_auto_apply()：CDP 未就绪只返回状态（stopped / running-without-cdp），绝不拉起
  → server 取 active bundle
    = theme_engine(参数→CSS+标记JS) + enhance_engine(模块JS)
  → cdp_skin.CDP（WebSocket, suppress_origin=True）
    → 注入 <style id="dsh-skin-cdp"> + window.DSHSkin 运行时
  → cdp_watcher 守护线程比对 hash/__v，过期即重注入（抗刷新/抗升级，只补注不重启）
```

**(C) 启动 / 重启（仅用户手动）**
```
未运行：面板「启动 DSH（调试模式）」/ 启动注入模式.bat → launch_dsh()
        （packaged=exe+--remote-debugging-port；dev=按 packageManager 走 yarn dev / pnpm start:desktop）
在跑但无 9222：面板「以注入模式重启」 → confirmRestart() 二次确认
        → POST /api/restart-dsh → relaunch_with_debug(force_restart=True)（先 taskkill 再带端口拉起）
DSH++ 侧无任何自动拉起路径（自愈链路已移除）
```

**(D) 插件注入**
```
plugin_manager 注入守护（0.2s 循环）：
  DSH 运行中 且 desktop/cordis.patch.yml 中托管块/文件缺失
    → 写入 - insert: [{id:'dshskin-…', name:'file:///…'}]（仅补缺，不覆盖）
```

**(E) 预览（preview.html）**
```
?theme=<id> 打开 preview.html
  → GET /api/theme-params/<id>（只认「已注入」参数）
  → iframe 加载 assets/snap-{home|conv|settings}.html?theme=<id>
  → postMessage {type:'dshskin:params'|'appearance'|'sidebar'} 同步
  → 快照页内「打标器」复刻 CDP 注入的 data-dsh-skin 逻辑，离线渲染
```

---

## 6. 前端与资源目录

### 6.1 `panel.html`（3309 行，单文件 SPA）
- **无框架、无构建**：纯 JS + 内联 CSS，`server.py` 直接当静态文件服务。
- **设计 token**：`:root` 浅色默认 / `[data-mode="dark"]` 深色；强调色 `#5E6BD1` 锁定；`--rail-w:208px`、`--hdr-h:48px`。
- **导航**：侧栏 2 组——工作区（概览/皮肤管理/会话管理/供应商配置）+ 扩展（DSH增强/插件/脚本市场/诊断/日志）+ 设置；共 10 个 `view-*` div（含 `view-plugins`）。
- **API 基址**：`API_BASE = location.protocol === 'file:' ? 'http://127.0.0.1:8765' : ''`（既支持 file:// 直开，也支持 http 托管）。
- **状态**：`themes/logs/editorTheme/editorParams/schema/activeView='home'/themeFilter/selectedId`。
- **参数 UI**：`PARAM_META` 只暴露 **7 个**显示参数（glow_color…overlay_alpha）；**`font_scale`/`force_dark` 不在 UI 中**（见 §7 观察项）。`COLOR_NAMES`（琥珀橙 #ffa846、天青蓝 #47a9ff…）做颜色语义化。
- **健康徽章逻辑（`loadStatus`）**：bad = 缺 harness_root / `selector_healthy===false`；ok = `cdp.injected` →「CDP 热生效 · N/M 区域」；warn = 在跑未注入 / 未启动。
- **CDP 操作函数**：`cdpApply()`、`probeSelectors()`（实测写覆盖）、`runRepair()`（注入自检/修复）、`maybeShowRestart()`/`confirmRestart()`（重启需二次确认）。
- **心跳**：`scheduleHeartbeat` 在线 15s / 掉线 5s 快速重试，打 `/api/ping`（轻量无磁盘 IO）；后端离线时动态插入 `backend-offline-bar`。
- **键盘**：`Ctrl+K` → 主题搜索框；`Ctrl+1..9` → `VIEW_ORDER=['home','themes','sessions','prov','enh','market','diag','logs','settings']`（**plugins 不在快捷键表**，见 §7）。
- **初始化**：`initMode → loadLogs → loadThemes → loadStatus → ?view= 深链 → scheduleHeartbeat(15000)`。
- **代码卫生**：大量 `data-page-node-id="…"` 属性（Trae-SOLO 代码生成残留），属可清理的冗余，无功能影响。

### 6.2 `preview.html`（665 行，保真预览）
- **定位**：展示主题注入后的**真实渲染效果**——「下方窗口是真实 DSH 面板快照，DOM 结构与 CSS 均取自运行实例」。
- **机制**：`SNAP_PAGES = {home:'snap-home.html', chat:'snap-conv.html', settings:'snap-settings.html'}`，iframe + `postMessage` 驱动；`appliedParams` **只认「已注入」参数**（预览按真实生效值渲染，避免「看起来对但实际没注入」的错觉）。
- **控件**：页面（首页/会话/设置）· 窗口（宽屏/窄窗）· 侧栏（展开/收起）· 外观（浅色/深色）。
- **自带一套与 panel.html 同构的设计 token**（独立 `data-mode`）。
- 同样带 `data-page-node-id` 残留。

### 6.3 `enhance-modules\`（内置增强 JS）
| 文件 | 能力 | 关键选择器 |
|---|---|---|
| `session-tools.js` | 会话导出/复制为 Markdown（Ctrl+Alt+E / C） | `[data-chat-flow-kind]`、`[data-slot="conversation.chat.node"]` |
| `ui-tweaks.js` | 会话宽度/疏密/字号/代码字体/隐藏冗余（Ctrl+Alt+U），注入 `<style id="dsh-skin-tweaks">` | `[data-dsh-skin="chat"]`、`[data-conversation-scroll]` |
| `input-plus.js` | 纯文本粘贴/提示词片段/快捷发送/输入历史（Ctrl+Alt+//Enter、Alt+↑↓） | `[data-composer-card]`、`[data-composer-seat]` |
| `usage-meter.js` | 右下角 token 估算胶囊（CJK≈1/字，其余≈1/4 字符，纯前端统计 DOM） | `[data-chat-flow-kind]`、`[data-chat-turn]` |

### 6.4 `assets\`（真实快照 + 捕获 CSS）
- **`dsh-vendor.css` / `dsh-index.css` / `dsh-inline.css`**：从运行中的 DSH 捕获的**真实 CSS**（外部 + CSS-in-JS）。
- **`inline-00.css … inline-105.css`**：CSS-in-JS 拆分块（共 106 个）。
- **`snap-home.html` / `snap-conv.html` / `snap-settings.html`**：真实面板 DOM 快照预览页。内部含「**打标器**」，复刻 CDP 注入的 `data-dsh-skin` 标记逻辑（用 `.KDVgQq_frame`/`[data-slot]` 定位并打标，1.2s 轮询），使预览窗口与真实应用结构/类名/CSS 完全一致，DSHSkin 仅注入主题变量着色。
- 这些快照由 `tools\capture_dsh_snapshots.py` 生成（见 6.5）。

### 6.5 `tools\`（开发工具，非运行时）
| 文件 | 用途 |
|---|---|
| `capture_dsh_snapshots.py` | 从 9222 的运行实例导出真实 DOM 快照 + CSS 到 `assets/`（预览的素材来源） |
| `probe_live.py` | 在线探针（选择器存活实测的开发版） |
| `e2e_matrix.py` | 端到端测试矩阵 |
| `gen_launchers.py` | 生成 bat 启动器 |
| `snap-inject.js` | 快照注入辅助 |
| `ui_sentinel.py` | UI 哨兵（升级后选择器漂移监测） |

### 6.6 `market\`（脚本市场，7 个 JS）
统一用 `// ==DSHSkin==` … `// ==/DSHSkin==` 包裹 front-matter（`@name/@title/@description/@version/@author/@icon`），`server.py` 的 `_FM_RE` 解析。
`copy-code-button`（代码块复制）、`focus-mode`（专注模式淡出侧栏）、`message-index`（消息序号徽标）、`prompt-library`（提示词库，Ctrl+Alt+L）、`night-schedule`（夜间自动暗色）、`selection-counter`（划词统计）、`wide-screen`（宽屏会话，Ctrl+Alt+W）。

### 6.7 `themes\keus\`（内置主题）
`keus.zip` + `background.png` + `meta.json`（`builtin:true`、appearance light）。`seed_builtin_themes()` 在 server 启动时落地。**当前仓库只带 1 个内置主题**（keus）——`_builtin` 前缀/`shipped` 判定在面板里兼容多主题，但资源目录只放了这一个。

### 6.8 启动器与打包
- **`_ensure_deps.bat`（59 行）**：Python 解析顺序 `DSH_SKIN_PY` env → `py -3.11` → `py -3` → `where python/python3`（**跳过 WindowsApps 商店 stub**，用 `import sys` 验证）；缺 `websocket-client` 自动装（非致命告警）。
- **`启动DeepSeekHarness.bat`**：`_ensure_deps.bat` → `python dsh-skin.py launch`（拉起带 9222 的 DSH）。
- **`启动面板.bat`**：`start /min python server.py --port 8765` → Edge `--app=http://127.0.0.1:8765/?app=1 --window-size=1180,780`（两个 ProgramFiles 路径都试）→ 回退默认浏览器。
- **`导入主题包.bat`**：拖 zip → `dsh-skin.py install "%~1"`。
- **`还原官方样式.bat`**：`dsh-skin.py restore`。
- **`DSHSkin.spec`（77 行）**：PyInstaller onefile，入口 `server.py`，`name='DSHSkin'`、`console=True`、`upx=True`；`datas` 收录 panel.html/preview.html/README/requirements/全部核心 .py/`enhance-modules/*.js`/`market/*.js`/`assets/*`/`tools/*`/`themes/*/*`；`hiddenimports=['websocket','PIL','session_store','zstandard','yaml']`；图标候选 `('dshskin-icon.ico','traeskin-icon.ico')`。
- **`dist\DSHSkin.exe`**：已构建的 onefile 产物。

---

## 7. 观察项（代码卫生 / 潜在改进，非缺陷）

1. **品牌更名不彻底**：`build\TraeSkin`、`traeskin-icon.ico`、遍布两 HTML 的 `data-page-node-id` 都是 TraeSkin→DSHSkin 更名残留。功能无碍，但属于可清理项（删 `data-page-node-id` 可显著瘦身两个 HTML）。
2. **参数 UI 与引擎不对称**：`theme_engine.PARAM_SCHEMA` 有 **9** 参数，`panel.html` 的 `PARAM_META` 只暴露 **7**（`font_scale`/`force_dark` 无 UI 入口）。若为有意隐藏则应在文档说明；否则用户无法调这两项。
3. **快捷键表缺 plugins**：`VIEW_ORDER` 含 9 项，`view-plugins` 不在 `Ctrl+1..9` 内（10 个视图 vs 9 个快捷键），plugins 只能鼠标点。
4. **内置主题仅 1 个**：面板代码支持多内置主题（`_` 前缀/`shipped`/`has_image` HEAD 探测），但 `themes/` 只有 keus。
5. **`__pycache__` 多版本 pyc**：存在 cpython-39 与 cpython-313（多种 Python 跑过）；`_ensure_deps.bat` 优先 3.11。建议固定解释器版本或加 `.gitignore`/清理。
6. **`dsh-skin.py` 连字符命名**：靠 `importlib.util.spec_from_file_location` workaround 被 server 复用。可读性上不如 `dshskin.py`（模块名），但为兼容既有 CLI 调用（bat 里写死 `dsh-skin.py`）而保留，属合理取舍。
7. **预览保真依赖快照新鲜度**：`assets/` 快照是某次构建的产物，DSH 大版本升级后需重跑 `tools\capture_dsh_snapshots.py`，否则预览与真实界面会有漂移（打标器能兜底部分，但 CSS 类名会变）。

---

## 8. 小结

DSHSkin 是一个**架构清晰、边界纪律极强**的本地工具：
- **单一注入通道（CDP）+ 单一用户态根目录（`~/.dsh-skins/`）**，把「改 DSH」收敛到「向渲染进程注入可逆的 style/JS」，真正做到零侵入、可还原。
- **稳定选择器契约**（语义 data 属性 + 运行时打标 + `selectors.json` 覆盖 + `probe` 实测）是它能扛住 DSH 升级的核心设计。
- **server 与 CLI 共享同一份核心逻辑**（importlib workaround），避免两套实现漂移。
- **安全契约明确**：对 DSH 数据只读、永不 kill 运行中的 DSH、跨域 POST 一律 403、包解压带路径穿越防护、图片魔数+大小校验。
- **可观测性好**：`doctor` 六段体检、`/api/ping` 心跳、离线提示条、`logs.json` 日志、`ui_sentinel` 漂移监测。

主要可改进点集中在**更名残留清理**与**UI/引擎参数对齐**上，均不影响当前功能正确性。
