# DSH++ · DeepSeek Harness 增强工作台

给 **DeepSeek Harness 桌面版（Electron）** 做「增强 + 会话/凭证管理 + 插件装配」的本地工作台
（对标 codex++）：开发模式（`pnpm start:desktop`）与 **win-x64 打包版**开箱即用——
**完全不修改 `deepseek-harness` 仓库里任何文件**——增强脚本经渲染进程
CDP 调试端口（9222）热注入；会话/凭证等宿主数据**只读解析**（导出/备份写到自己的数据目录）。
v1.0，Windows + Python。

> 设计思路：外部注入、零侵入、模块可单独开关、随时可完全还原。

> **换肤已移除（2026-09-20）**：主题库 / 参数调优 / CSS 模板 / 预览 / 主题包安装全部删除，
> 「动态背景」改由 DSH 侧插件 [`dsh-plugin-wallpaper-engine`](https://github.com/elysia395/dsh-wallpaper-engine)
> 承担 —— 面板「动态壁纸」分区只做该插件的参数读写与壁纸清单展示。
> 本工具保留：CDP 增强注入（含打标器）、会话管理、供应商配置、插件管理、诊断。
> 老用户升级后跑一次 `python dsh-skin.py migrate` 即可清掉旧主题数据。

## 工作台分区

| 分区 | 内容 |
|---|---|
| **概览** | DSH 状态 / 注入状态 / 打标器与选择器健康 / 增强统计 / 快捷操作 / 最近动态 |
| **动态壁纸** | dsh-plugin-wallpaper-engine 的壁纸清单 + 39 项参数（滑杆/下拉/颜色/开关）实时读写 |
| **会话管理** | 列出本机全部会话、导出 Markdown/JSON、整库 zip 备份（只读，绝不写 DSH 数据） |
| **供应商配置** | 凭证状态（Provider 路由 / API Key 打码 / 来源）、凭证与设置备份 |
| **DSH 增强** | 运行时桥 + 4 内置模块（会话工具/界面微调/输入增强/上下文用量）+ 用户脚本/CSS |
| **插件管理** | 本机 cordis 插件（安装/启停/配置/还原托管块），含 DSH 插件市场装配 |
| **脚本市场** | 内置精选脚本（代码复制/专注模式/划词统计/提示词库/夜间暗色/消息序号/宽屏）一键安装 |
| **系统** | 诊断（doctor/probe/选择器/模式解析链）· 日志 · 设置（CDP 端口/Harness 根目录/启动模式/打包 exe）· 帮助与关于 |

桌面窗口：`启动面板.bat` 自动用 **Edge --app 模式**开独立无边框窗口（无 Edge 回退默认浏览器）；
后端亦可 `dist\DSHSkin.exe` 直接跑（PyInstaller 打包，market/模块/资源已随包）。

## 快速上手

| 操作 | 方式 |
|---|---|
| **启动桌面应用** | 双击 `dist\DSH++.exe`（独立窗口 + 系统托盘 + 单实例，不依赖浏览器） |
| **启动桌面版（开发模式）** | 双击 `启动DeepSeekHarness.bat`（自动用 corepack pnpm + Node22+ 启动，自带 CDP 9222） |
| **启动打包版（win-x64）** | 双击 `启动打包版.bat`（先 taskkill 旧实例，再以 `--remote-debugging-port=9222` 拉起打包 exe） |
| **可视化面板** | 双击 `启动面板.bat`，浏览器打开 http://127.0.0.1:8765/ |
| **调整动态壁纸** | 面板「动态壁纸」分区切换壁纸 / 调参（需已装 dsh-plugin-wallpaper-engine 并运行 DSH） |
| **写增强脚本** | 面板「增强器 → 新建用户脚本」，或把 `.js` 丢进 `~/.dsh-skins/enhance/` |
| **应用增强（热生效）** | 面板「应用增强（CDP）」/ `python dsh-skin.py enhance --apply` |
| **还原官方样式** | 双击 `还原官方样式.bat`，或面板「还原官方」 |
| **环境体检** | `python dsh-skin.py doctor` |
| **检查/安装依赖** | `python dsh-skin.py deps [--install]` |

> 桌面版若已用你的快捷方式启动过（`pnpm start:desktop`），面板同样能连上 9222 直接注入。
> 打包版默认不带调试端口，需用 `启动打包版.bat` / 面板「重启 DeepSeek Harness」以调试端口拉起。
> 桌面应用（DSH++.exe）与浏览器面板共用后端与数据，二选一即可。

## 依赖

**运行环境：Windows + Python 3.11+**（更低版本不保证可用；命令行里的 `python` 在 Windows 上
可能是 Microsoft Store 占位 stub，请用真实解释器，启动器会自动跳过 stub）。

依赖分层，缺非核心依赖时对应功能降级、不崩溃：

| 依赖 | 用途 | 缺失影响 |
|---|---|---|
| `websocket-client` | CDP 热注入通道（**核心**） | 无法热注入，增强/会话面板仍可用 |
| `zstandard` / `PyYAML` | 解压 v2 会话、读供应商配置、插件托管块读写 | 会话/凭证面板与插件安装降级提示（后端仍可启动） |
| `pystray` / `Pillow`（可选） | 系统托盘常驻 + 开机自启菜单 | 自动降级为控制台形态 |

```bash
python -m pip install -r requirements.txt
# 或
python dsh-skin.py deps --install
```

`.bat` 启动器首次运行会自动安装依赖。

## 进阶文档

- [`docs/script-authoring.md`](docs/script-authoring.md) — 增强脚本作者指南（`DSHSkin` 运行时 API / `@permissions` 安全声明）
- [`docs/cdp-faq.md`](docs/cdp-faq.md) — CDP 注入常见故障 FAQ（调试模式 / Node 版本 / 多窗口 / 还原）

## 启动器说明（重要）

DeepSeek Harness 桌面版 = Electron 壳包 dsh Web UI，启动链路：
**快捷方式 → 仓库 `scripts/start-desktop.cmd` → pnpm run start:desktop**。

已知坑：**全局 pnpm shim 可能命中 Node v20**（pnpm 11 需要 Node 22+），直接 `pnpm start:desktop`
会启动失败。`启动DeepSeekHarness.bat` 与面板的「启动」按钮都走内部修复逻辑：

1. 优先用 `D:\program\node.exe`（v24）与 corepack 管理的 pnpm（`%LOCALAPPDATA%\node\corepack\...`）；
2. 找不到时退回 `cmd.exe /c <cd 仓库 && 修 PATH && pnpm.cmd run start:desktop>` 模板（与你的快捷方式同构）。

解析结果可用 `python dsh-skin.py detect` 查看；可用环境变量 / config 覆盖（见「路径自适应」）。

## 打包版（win-x64）支持

对 `.desktop-build` 打包产物（`win-unpacked\DeepSeek Harness.exe`）开箱即用，体验与开发模式一致
（启动带 CDP、热注入增强、读打包版会话与凭证）。**唯一硬缺口**是打包版默认不带调试端口，
DSH++ 用 `--remote-debugging-port=9222` 参数拉起即可（不修改打包产物任何文件）。

- **切换模式**：面板「设置 → 启动模式」选 `dev`（开发模式）或 `packaged`（打包版）；
  config.json 的 `launcher_mode` 字段复用现有设置，旧值 `auto`/`cmd`/`direct` 仍视为 dev，向后兼容。
- **打包版 exe 解析**（优先级）：环境变量 `DSH_SKIN_DESKTOP_EXE` > `config.json.desktop_exe` >
  扫描 `<harness_root>/apps/desktop/.desktop-build/targets/*/unsigned-artifacts/win-unpacked/`。
  找不到时面板/CLI 给出明确指引（设变量、填路径或先运行打包脚本），不会静默失败。
- **数据根（home）解析**：`packaged` 模式默认 `~\.dsh`（`%USERPROFILE%\.dsh`），会话管理/供应商配置
  自动切到打包版的数据；`config.json.dsh_home` 或环境变量 `DSH_HOME` 可显式覆盖（换机/自定义时）。
- **启动/重启语义**：dev = 原样（`start:desktop`）；packaged = **先杀旧实例**（`taskkill /T /F`，
  兼容打包版单实例锁）→ `"{exe}" --remote-debugging-port=9222`。「启动」按钮只拉起不杀进程；
  「重启 DeepSeek Harness」（有确认）才会先关后开。
- **诊断**：`detect` / `doctor` / 面板「诊断 → 模式解析链」按当前模式输出解析结果与**来源**
  （启动模式、打包 exe 路径与来源、DSH 数据根与来源），切换模式后立即反映，避免误导。
- **注入通道不变**：仍是 CDP 9222 热注入（见下节），零侵入、随时可还原。

## 桌面应用（DSH++.exe）

DSH++ 可打包成**真正意义上的桌面应用**：独立窗口（WebView2 内核）+ 系统托盘 + 单实例，
双击即用，不依赖命令行与浏览器标签页。

- **构建**：双击 `构建桌面应用.bat`（自动装 PyInstaller/pywebview 并执行
  `python -m PyInstaller build_desktop.spec`），产物 `dist\DSH++.exe`（约 24MB，单文件）。
- **运行形态**：独立窗口（pywebview/WebView2，原生窗口、可调大小、关窗最小化到托盘）；
  缺失 pywebview 时自动降级为 Edge `--app` 独立应用窗口；再缺失才用默认浏览器。
- **单实例**：重复双击只会唤起已有窗口（Windows 命名互斥 + 后端 `/api/desktop-activate` 激活钩子）。
- **数据**：与面板完全共用 `~/.dsh-skins/`，增强/会话/凭证/插件/日志（`desktop.log`）不因打包改变；
  后端仍是 127.0.0.1:8765 + token 鉴权。
- **与浏览器面板的关系**：同一后端、同一数据；`启动面板.bat`（浏览器）与 `DSH++.exe`（窗口）可共存
  （端口探测自动复用），日常用桌面应用即可。
- **托盘**：右键快速「打开面板 / 立即注入增强 / 还原官方样式 / 开机自启 / 退出」。

## 注入通道（唯一：CDP，不改官方文件）

| | CDP 热注入 |
|---|---|
| 原理 | 渲染进程开发模式自带 `--remote-debugging-port=9222`；**打包版**以 `--remote-debugging-port=9222` 参数拉起后同样监听 9222。经 WebSocket 注入可逆 `<style id="dsh-skin-cdp">` + JS 运行时 |
| 能力 | **增强脚本 + 区域打标器** |
| 生效 | **立即**，无需重启 |
| 是否改官方文件 | **完全不改**（开发模式与打包版一致） |

> **为什么不能改文件？** harness 是外部仓库（用户硬约束：不允许改动 `D:\DSH\deepseek-harness` 中任何文件），
> 且前端 CSS Modules 类名带随机前缀（如 `KDVgQq_frame`），注入 CSS 到官方样式文件会被升级/热更新覆盖。
> CDP 是唯一且最干净的通路。

## 选择器策略（升级自愈）

DSH 前端自带**稳定契约**，打标器与增强脚本全部基于它们，类名一概不用：

- `data-slot="conversation.composer.bar"`、`data-slot="conversation.chat.node"`、`data-slot="conversation.session.header"` 等插槽契约
- `data-phase`、`data-conversation-scroll`、`data-composer-seat`、`data-composer-card="true"`、`data-rightbar-col`、`data-shell-overlay`
- 消息级 `data-chat-flow-kind`（`user` / `steering` / `assistant` / `assistant-step` / `turn-process` / `context` / `system-prompt`）
- 暗色机制：`body[data-ds-dark-theme]`；设计系统语义 token `--dsw-alias-*`（增强脚本可覆盖换色）

> **两个易踩的契约细节**（v6 模板已内置处理）：
> 1. `data-phase` 是双义属性——会话根（ConversationRoot）与输入编辑面（contenteditable）都带它，
>    会话样式必须写 `[data-phase]:not([contenteditable])`，否则输入框会吃到整层会话玻璃。
> 2. `[data-shell-overlay]` 是**常驻全窗**的模态宿主层（absolute + z-index 20 + pointer-events:none），
>    不是"弹窗出现时才有"——玻璃只能上在其子元素（`> *`，弹层本体），直接上会整屏蒙尘模糊。

**运行时打标器**：框架层（AppFrame）没有 data 属性，注入的 JS 会按 `#root > div[data-slot] > div`
结构给区域打上 `data-dsh-skin="frame|sidebar|chat|composer|..."` 标记，CSS 针对标记写样式；
前端升级导致结构微调时打标器自动跟随，**升级也自愈**。

DSH 升级后若体检报「选择器失效」：

```bash
python dsh-skin.py probe            # 实测各候选在真实 DOM 中的存活情况
python dsh-skin.py probe --apply    # 只保留实测命中的选择器，写入覆盖
```

覆盖写入 `~/.dsh-skins/selectors.json`，**不改工具代码即可适配新版**。

### 打标器版本与漂移巡检

打标器契约版本记在 `marker_engine.MARKER_VERSION`，用于判断离线快照是否随 DSH 升级而落后：
`assets/snap-manifest.json` 记录抓取当时版本，面板「概览」比对当前版本并给出重抓提示。
区域选择器矩阵发生不兼容变化时，该常量 +1。

## 增强器（v1.0 核心）

DSHSkin 的增强器由三部分组成，全部通过 CDP 注入到渲染进程，**不修改任何官方文件**：

| 模块 | 作用 |
|---|---|
| **运行时桥 `window.DSHSkin`** | 一切增强的地基：提示、日志、存储、UI 工具、DOM 工具（常开） |
| **内置增强模块** | 开箱即用的 4 个增强（见下表），可逐个开关 |
| **用户脚本 / 用户 CSS** | 执行 `~/.dsh-skins/enhance/*.js` 与 `*.css`，任意自定义 |

### 内置增强模块

| 模块 | 能力 | 快捷键 / 入口 |
|---|---|---|
| **会话工具** | 把当前会话导出为 Markdown / 复制为 Markdown（按 `data-chat-flow-kind` 判别角色，转代码块） | `Ctrl+Alt+E` 导出 · `Ctrl+Alt+C` 复制 · 右下角按钮 |
| **界面微调** | 会话区宽度、消息疏密、正文字号、代码字体、隐藏冗余元素；配置持久化，**一键恢复默认** | `Ctrl+Alt+U` · 右下角 ⚙ |
| **输入增强** | 富文本粘贴自动转纯文本；提示词片段面板；快捷发送；输入历史回溯 | `Ctrl+Alt+/` 片段 · `Ctrl+Alt+Enter` 发送 · `Alt+↑/↓` 历史 |
| **上下文用量** | 右下角常驻估算 token 与上下文占比，点击展开明细（中英分别计数、可调窗口） | 右下角胶囊 |

> 所有内置模块都用「稳定契约选择器 + 兜底」实现，DSH 升级改类名也能工作；找不到目标时只提示、不报错。
> 模块源码在 `enhance-modules/*.js`，可按需阅读或改写（改完重注入即生效）。

每个脚本/模块可单独开关；**单个脚本里的语法/运行错误会被隔离**，不影响其它脚本与 DSH 本身。
守护线程每 6 秒比对注入内容指纹，页面刷新 / 模块或脚本改动后自动补注。

> **生效机制**：注入内容带「构建指纹（build）+ 模块内容哈希」。内容没变则整段跳过（幂等，不会重复叠加 UI）；
> 某个模块或脚本改了，只有它自己会重新执行，其它不受影响。

### 用户脚本 API

脚本在渲染进程里执行，可直接使用注入的运行时：

```js
// 提示
DSHSkin.toast('已生效');
// 日志（带前缀，可在浏览器控制台看）
DSHSkin.log('state', { a: 1 });
// 命名空间存储（localStorage，前缀 dsh-skin:）
DSHSkin.storage.set('k', { a: 1 });
const v = DSHSkin.storage.get('k', null);
// 轻量 UI 工具（建元素 / 注入样式 / 绑事件 / 移除）
DSHSkin.ui.style('my-style', 'body{--x:1}');
const el = DSHSkin.ui.el('div', 'position:fixed;right:12px;bottom:12px;', 'hi');
DSHSkin.ui.on(el, 'click', () => DSHSkin.toast('clicked'));
// DOM 就绪
DSHSkin.ready(() => { /* ... */ });
// 等待元素出现 / 监听元素（MutationObserver 封装）
DSHSkin.on('[data-dsh-skin="chat"]', (el) => { /* ... */ });
const el2 = await DSHSkin.wait('[data-chat-flow-kind]', 15000);
// 定时器（页面卸载自动清理）
DSHSkin.interval(() => { /* ... */ }, 3000);
// 估算当前会话用量（内置「上下文用量」模块提供）
const usage = DSHSkin.usage();   // { chars, cjk, latin, tokens, msgs }
// 注册一个幂等模块（内容哈希去重：源码改了才会重跑）
DSHSkin.def('my-module', (R) => { R.log('loaded'); });
```

### 增强器 CLI

```bash
python dsh-skin.py enhance                      # 查看状态
python dsh-skin.py enhance --preset builtin     # 一键开启内置增强（会话工具/界面微调/输入增强/上下文用量）
python dsh-skin.py enhance --preset scripts     # 只开用户脚本
python dsh-skin.py enhance --preset full        # 全开（内置 + 用户脚本 + 用户 CSS）
python dsh-skin.py enhance --enable session-tools
python dsh-skin.py enhance --disable my-tweak.js
python dsh-skin.py enhance --apply              # 通过 CDP 立即生效（无需重启）
```

### 增强器 API

| 方法 | 说明 |
|---|---|
| `GET /api/enhance` | 增强状态摘要（模块 / 脚本 / CSS） |
| `POST /api/enhance` | `{enabled}` / `{modules:{k:bool}}` / `{preset}` / `{script,script_enabled}` |
| `GET /api/enhance-file?name=x.js` | 读取脚本内容 |
| `POST /api/enhance-save` | `{name, content}` 新建/覆盖 |
| `POST /api/enhance-delete` | `{name}` 删除 |
| `POST /api/enhance-apply` | 立即注入（`allow_kill` 才允许重启 DeepSeek Harness） |

> **本地安全**：所有状态变更接口会校验 `Origin`/`Referer`，只接受本机来源，
> 防止任意网页通过 `127.0.0.1` 调用接口（如结束你的 DeepSeek Harness 进程）。

## 动态壁纸（dsh-plugin-wallpaper-engine）

换肤移除后，「动态背景」由 DSH 侧插件 [`dsh-plugin-wallpaper-engine`](https://github.com/elysia395/dsh-wallpaper-engine)
承担。DSH++ 只做**参数读写代理**，不接管渲染：

- 面板「动态壁纸」分区列出插件提供的壁纸清单（video / scene / web / image 四类），
  选中即写回插件，插件热生效；
- 参数分四组暴露，边界**与插件 `sanitizeSettings()` 的 clamp 逐项对齐**，并主动夹紧：
  - **滑杆 15 项**：遮罩浓度 / 玻璃边框 / 界面背景模糊 / 壁纸模糊 / 壁纸不透明度 /
    亮度 / 对比度 / 饱和度 / 播放倍速 / 玻璃通透度 / 侧栏模糊 / 侧栏通透度 /
    侧栏文字层 / 吉祥物缩放 / 字重
  - **下拉 7 项**：壁纸填充 / 类型筛选 / 内容分级 / 选择器布局 / 吉祥物形态 / 字体 / 解码帧率上限
  - **颜色 6 项**：强调色 / 玻璃底色 / 侧栏底色 / 侧栏文字色 / 文字颜色 / 光标颜色
  - **开关 11 项**：轮播 / 隐藏时暂停 / 失焦时暂停 / 电池时暂停 / 水平翻转 / Edge 兼容 /
    玻璃窗口 / 侧栏玻璃 / 显示吉祥物 / 自定义字体 / 场景动画(β)
- Scene 场景壁纸优先用插件抽帧出的 MP4 预览（硬件解码流畅），可一键打开插件 WebGL 播放器实时预览；
- 插件路由挂在 DSH 内部的随机环回口，**外部进程无法直连**，
  故本工具通过 CDP 在页面上下文内同源请求（见 `wallpaper_engine.py`）；

### 写入路径

**优先 `PUT /wallpaper-engine/settings`**，复用插件自己的 `enqueueConfigWrite()`
串行队列与 `sanitizeSettings()` 校验，响应即已持久化。
仅当该路由不可达（DSH 未开调试端口 / 插件未加载）时，才降级为原子直写
`~/.dsh-wallpaper-engine/config.json`，并在面板日志中明确标注「降级写入」。

> ⚠️ 插件用 `clampNum(v,lo,hi,fb)`，越界值是**回落默认值而非夹紧**。
> 故本工具必须先自行夹紧到 `[lo,hi]`，否则用户拖过边界会被插件悄悄重置成默认值
> （表现为「拖了没反应」）。参数区间由 `tests/test_wallpaper_engine.py` 锁定。

> 插件本身由 DSH 的插件市场安装与升级，本工具不代管其文件。


## 安全性与可逆性

- **零侵入**：不改 `deepseek-harness` 任何文件；增强全部经 CDP 注入，`restore` 即完全还原
- **多窗口全覆盖**：枚举全部页面 target 注入并监听新窗口；`addScriptToEvaluateOnNewDocument` 预注入缩短裸 UI 窗口
- **原子写入 + 进程内锁**：config / 日志 / selectors / enhance 均为「临时文件 + 替换」，并发读改写在锁内进行，断电不损坏、快速切换不丢更新
- **本地接口鉴权**：启动生成随机 token（`~/.dsh-skins/server.token`，仅本机），敏感读取与所有写操作须带 token；状态变更同时校验本机 Origin（防 CSRF/跨站）
- **托管块可备份可还原**：写 `cordis.patch.yml` 前先留 `.orig` 备份，一键还原；`uninstall` 移除全部注入并还原托管块，`--purge` 才清数据
- **市场脚本安全审计**：安装前静态分析声明权限/实际能力/未声明项/风险等级，高风险（网络/eval）需二次确认
- **脚本错误隔离与回收**：`new Function` 包裹执行，单脚本异常不扩散；页面 console/异常经 CDP 回收到面板日志
- **日志脱敏与轮转**：密钥/令牌落盘前打码，日志超量自动轮转
- **插件包校验**：zip-slip 穿越、解压炸弹（总量/单文件/成员数/压缩比）、字段正则
- **绝不擅动进程**：DSH++ 永不自动拉起/重启桌面应用；守护线程（CDP 补注/漂移巡检/页面错误回收）只读 CDP 状态，只有面板里点「以注入模式重启」并确认后才会结束进程

## 目录结构

```
dsh-skin.py       CLI 主入口（restore/migrate/uninstall/detect/deps/doctor/probe/
                   selectors/enhance/launch/update）
dsh_env.py        环境解析唯一真源：数据目录 / 配置读写 / CDP 端口 / 进程检测 / 启动器（corepack pnpm / 打包版 exe）
marker_engine.py  运行时打标器 + 区域选择器契约（原 theme_engine 剥离，换肤移除后独立）
cdp_skin.py       CDP 注入引擎：CSS + JS 运行时、内容指纹、target 兜底、守护补注
enhance_engine.py 增强引擎：内置模块装载 / 用户脚本 / 用户 CSS / DSHSkin 运行时生成
wallpaper_engine.py 动态壁纸插件适配层：探测状态 / 同源拉清单 / 经插件 PUT 路由读写参数
plugin_manager.py cordis 插件管理：安装/启停/配置/托管块还原
session_store.py  v2 只读通道：DSH 会话（zstd-JSONL 解析/导出/备份）+ 凭证（读取打码/备份）
enhance-modules/  内置增强模块 JS 源（会话工具 / 界面微调 / 输入增强 / 上下文用量）
market/           脚本市场内置脚本（front-matter 元数据，一键安装到 enhance/）
server.py         本地后端 127.0.0.1:8765（API、CDP 补注守护、漂移巡检、源码自重启）
panel.html        工作台：概览 / 动态壁纸 / 会话 / 供应商 / 模型 / 上下文用量 / 增强 / 插件 / 市场 / 诊断 / 日志 / 设置
assets/           真实快照页与官方 CSS（漂移巡检用）
tools/            生成器与探针（gen_launchers.py / probe_live.py / 快照抓取）
启动DeepSeekHarness.bat / 启动打包版.bat / 启动面板.bat / 还原官方样式.bat / 构建桌面应用.bat / _ensure_deps.bat
build_desktop.spec   桌面应用构建配置（PyInstaller onefile，收集 panel.html/market/assets/enhance-modules/dsh-skin.py/tools）
desktop_app.py       桌面应用入口：单实例锁 + 后端线程 + pywebview/Edge 独立窗口 + 托盘 + 降级链
```

数据目录 `~/.dsh-skins/`：

```
config.json      cdp_port / dsh_root / launcher_mode(dev|packaged，缺省自动探测) / desktop_exe / dsh_home
selectors.json   选择器覆盖（probe 自动生成或手工维护）
enhance.json     增强开关（总开关 / 模块 / 每脚本开关）
enhance/         用户脚本 *.js 与用户 CSS *.css
plugins/         cordis 插件 registry 与托管块状态
backups/         会话整库 zip / 凭证备份（session_store 产出）
exports/         会话导出的 Markdown / JSON
logs.json        操作日志（上限 200 条）
```

## 常驻形态（托盘 / 开机自启 / 单实例）

- **系统托盘**（需可选依赖 pystray）：图标颜色反映注入状态，右键可打开面板 / 立即注入 /
  还原 / 切换开机自启 / 退出；缺 pystray 时自动退回控制台形态。
- **开机自启**：写入当前用户注册表 Run 键（无需管理员、可随时撤销），以 `--no-open` 静默常驻。
- **单实例守卫**：重复启动时若检测到已有面板在跑，直接打开已有实例而不是再起一个。

## 路径自适应

按优先级解析，换盘/换机器无需改代码（环境变量前缀 `DSH_SKIN_*`）：

- **启动模式**：`config.json.launcher_mode`（`dev` / `packaged`；旧值 `auto`/`cmd`/`direct` 视为 dev）
- **Harness 根目录**：`config.json.dsh_root` > 环境变量 `DSH_SKIN_HARNESS_ROOT` > 常见位置扫描 > 默认 `D:\DSH\deepseek-harness`
- **打包版 exe**：`config.json.desktop_exe` > 环境变量 `DSH_SKIN_DESKTOP_EXE` > 打包产出目录扫描（`targets/*/unsigned-artifacts/win-unpacked/`）
- **DSH 数据根**：`config.json.dsh_home` > `DSH_HOME` > 默认（dev=仓库 `development/home`；packaged=`~\.dsh`）
- **CDP 端口**：`config.json.cdp_port` > 环境变量 `DSH_SKIN_CDP_PORT` > 默认 9222
- **Node / pnpm**：环境变量 `DSH_SKIN_NODE` / `DSH_SKIN_PNPM` > corepack 路径 > PATH 扫描（Node 需 ≥22）

```bash
python dsh-skin.py detect    # 查看实际解析结果与来源（两条链路：dev / packaged）
python dsh-skin.py launch    # 用解析到的启动方式拉起桌面版
```

## 常见问题

**改了增强脚本没生效？**
- 确认 DeepSeek Harness 是**开发模式**启动的（`启动DeepSeekHarness.bat` / `pnpm start:desktop`，自带 9222），
  或**打包版**以 `--remote-debugging-port=9222` 拉起（`启动打包版.bat` / 面板「重启」）
- 面板点「应用增强（CDP）」，或等守护线程自动补注（页面刷新后 6 秒内）

**面板显示「CDP 连接：需重启（打包版）」？**
打包版在运行但没开调试端口（默认行为）。点面板「重启 DeepSeek Harness」（有确认）会先关旧实例再带
端口拉起；或双击 `启动打包版.bat`（同样先 taskkill 再启动）。

**打包版 exe 启动失败 / 面板说「打包版 exe 未找到」？**
- 未找到：设 `DSH_SKIN_DESKTOP_EXE` / 面板设置里填路径 / 先运行 `.desktop-build\run-package-win-dir.cmd` 打包。
- 找到了但起不来：`python dsh-skin.py doctor` 会给出诊断而非静默；常见原因——被杀软拦截、
  Electron 用户数据损坏（可先备份后清除该 exe 对应 AppData 缓存再试）、旧实例未退出（单实例锁，先 taskkill）。

**打包版会话/凭证在哪？**
`packaged` 模式默认读 `~\.dsh`（`%USERPROFILE%\.dsh`）；开发模式读仓库 `development/home`。
面板「会话管理 / 供应商配置」自动按当前模式切换数据源，诊断页可核对「DSH 数据根」与来源。

**打包版插件注入到哪？**
插件写入两处补丁层：web 端 `~\.dsh\profiles\web\cordis.patch.yml`（两层模式共用）；
桌面端按启动模式——`dev` 写 `<harness>\.desktop-build\development\project\cordis.patch.yml`，
`packaged` 写 `~\.dsh\profiles\desktop\desktop.cordis.yml`（打包版 cordis 组合根，官方注明
「package transactions own this file」，即插件事务写入处；托管块机制与 web 完全一致、可还原）。
插件装好后**重启打包版**才加载（运行中改补丁不热生效）；面板「插件」页的
desktopProject/desktopPatch 字段会显示当前模式实际写入的目标。

**供应商配置页怎么和打包版对不上？**
打包版/生产的真实 LLM 供应商配置在 `~\.dsh\settings.yaml` 的 `llm-pi-ai.providers`
（如 `localqwen`：baseURL / apiKeyEnv / models）与 `agent-default-model`（默认模型）。
「供应商配置」页顶部「凭证状态」（route / API Key / 来源）描述的是 DeepSeek 官方 API 凭证
（env `DEEPSEEK_API_KEY` 或凭证文件 grant）；下方「已配置的 LLM 供应商」表读 `llm-pi-ai.providers`，
逐项显示 baseURL、Key 来源（`apiKeyEnv` 是否已在环境变量中设置）与模型数——两者语义不同，
别把「API Key（未设置）」误读为供应商配置丢失。

**脚本报错会不会搞坏 DeepSeek Harness？**
不会。脚本用 `new Function` 包裹，异常被捕获并记入 `DSHSkin.logs`；即使语法错误也只会跳过该脚本。

**DeepSeek Harness 升级后界面变回原样？**
跑一次 `python dsh-skin.py doctor`，按提示 `probe --apply` 即可。

**全局 pnpm 启动崩（Node v20）？**
用 `启动DeepSeekHarness.bat` 或面板启动，内部已自动切换到 corepack pnpm + Node 22+。
