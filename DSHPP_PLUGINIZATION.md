# DSH++ 插件化计划（基于 Cordis 框架源码分析）

> 状态：**P0 完成 → 设计修正已落地（2026-09-17 14:52）**。直接 profile 安装被用户否决
> 并被桌面 boot 期 pnpm 协调实证证伪（§9.5）；dshpp 已改为**自带内嵌安装器**
> （dshskin file:// 模式，`dshpp/install.mjs` + `uninstall.mjs`），引导安装已执行，
> **待用户重启 DSH Desktop 后按 §9.6 验收**。
> 本文档由对 `D:\DSH\DSH-Plugin\cordis` 框架源码与
> `D:\DSH\dsh-desktop` 宿主装配链的完整阅读推导而来（2025 会话，读取范围见附录 A）。
> 语言：中文。硬约束：DSH++ 永不自动拉起/自愈 DSH Desktop，只允许用户发起的启动/重启。

---

## 1. 目标

1. **一切 DSH 皆插件**：把 DSH++（面板 + 插件管理 + 皮肤）改造为一个标准
   DSH Cordis 插件包（下称 **dshpp bundle**），由 **dshpp 自带的内嵌安装器**装入
   桌面（dshskin file:// 模式：受管 patch 块 + 插件树副本 + registry 条目；
   **不是**裸 profile 依赖——§9.5 证伪记录），随宿主同生命周期，可一键干净卸载。
2. **消灭 Python 运行时**：8765 端口、token、panel.html 独立服务、CDP/9222
   注入全部退役；面板成为 GUI 内的一个页面，管理操作走宿主内部通道。
3. **保持礼貌外部行为**：对 market 文件的写入保持"最小 schema 保留编辑"，
   优先改走 dshmarket 自己的 API（它有快照/回滚/日志）。
4. **硬约束结构化保证**：in-host 插件在结构上不可能启动宿主（宿主进程是它
   的祖先，插件只能 requestRestart，不能 spawn）。

---

## 2. Cordis 框架速览（底层是什么）

Cordis 自称 "Meta-Framework of Spatiotemporal Composability"（论文 arXiv 2608.25512），
yarn 4.14.1 monorepo，**API 明确声明不稳定（may change without notice）**。

### 2.1 核心对象（`packages/core`）

| 对象 | 职责 |
|---|---|
| `Context` | Proxy 包裹的服务容器。`ctx.<name>` 取服务；`isolate(name)`/`intercept(name, cfg)`/`extend(meta)`；内置 events/logger/reflect/registry/fiber |
| `Service` | 抽象服务类，`provide` 通过 `ctx.reflect.provide(name, self, check)`；config 沿 intercept 原型链 `resolveConfig` |
| `Fiber` | 一个插件实例的生命周期单元（active/pending/failed/disposed）；dispose 时按注册顺序回卷所有 effect |
| `Plugin` | 模块 → fiber。`ctx.plugin(mod, config)` 挂载；`ctx.registry.plugin(plugin, config)` |

### 2.2 Loader（`packages/loader`，内置 `loader`）

- `EntryOptions = { id, name, config?, group?, disabled?, inject? }`
  - `name` = 模块说明符（裸包名按 config 文件目录解析；`js:` YAML 标量 = 表达式）
  - `inject` = 硬依赖服务列表，Loader 等这些服务激活后才挂载该行
  - `disabled` 沿祖先链判断（group 的 disabled 覆盖全子树）
- `Entry.update(options)`:
  - `disabled: true` → `fiber.dispose()`（卸载）
  - config 有 diff → **不重新 import 模块**，走 `loader/partial-dispose` +
    `_patchContext` 瀑布：重新求值 config 后 `fiber.update(config, true)`
    —— **config 变更热更新插件，无需重启**
  - 模块名变更等结构性变化 → 重建 fiber
- 自禁用持久化：插件 dispose 自身时，Loader 把 `entry.options.disabled = true`
  写回树（市场"停用"行的框架级来源）。
- `ctx.on('internal/update')`：运行期树变更提交回 `entry.options.config`。

### 2.3 Include（`packages/include`，内置 `include`）—— 配置树 ⇄ YAML 文件

- `Include extends EntryTree`；`Config = { path, initial, patches, enableLogs }`
- **HMR 内建**：`ctx.hmr.watch(this.filename, () => this.refresh())`
  —— Include 自己监视自己的配置文件（前提是 HMR 服务在场）。
- `PatchOptions = { id, insert[], name, config, group, disabled, inject, ... }`；
  `applyEntryPatches(base, patches)`：
  - `insert` 无 id → 追加根；有 id → 进目标 group 的 config（目标必须 group）
  - 非 insert 补丁按 id 覆盖行（name 不匹配 → warn+skip）
  - 每个键有 `EntryOwner`（file/patch/insert 索引）
  - `ensureIds`/`ensureInsertIds`：无 id 行补随机 id（运行期变更按 id 路由回）
  - **`routeJournal`：运行期树变更路由回"属主"**（文件 or 补丁）；journal 落盘
    时冲突策略 **"file wins"**；原子写（rename + 10 次重试 + Windows EACCES/EPERM 容错）
  - YAML 方言 = JSON_SCHEMA + `tag:yaml.org,2002:js` 标量（`!!js` 表达式）

### 2.4 HMR（`packages/hmr`）

- 文件监视（chokidar）**始终可用**：`ctx.hmr.watch(path, cb)`
- 模块级 HMR（重 import 陈旧模块、三阶段全有或全无换 fiber、可回滚）
  需要 Node 内部 ModuleLoader（`--expose-internals` 或 addon）——
  **打包 Electron 环境拿不到 → 模块 HMR 失效，但文件 watch 仍工作**
- externals（框架模块树）变更 → 全量重载

### 2.5 `packages/create`

模板脚手架（从 registry 拉模板 tar 包）——插件包的标准形态不在这里，
而在 `dsh.client`/`dsh.bundle` manifest 约定（见 §4）。

---

## 3. DSH Desktop 如何装配 Cordis（宿主链路）

### 3.1 boot 链

```
dsh-plugin-desktop (Electron main)
  └─ prepareDesktopProfile()              # src/profile.ts
       ├─ profileDir = ~/.dsh/profiles/desktop
       ├─ 重写根 cordis.yml 为 '[]'        # 每次启动都重写！
       ├─ 组合 patch 层（顺序见下）
       └─ bareModuleBaseUrl = profile/package.json
  └─ boot(BIN, rootConfig, patches, prepare, bareModuleBaseUrl)   # @deepseek-ai/dsh-app-boot
       ├─ Context + Loader + 根 Include(base=[] + patches)
       ├─ prepare(ctx): 注入 desktopRuntime/desktopPnpmBootstrap/DesktopPluginsService...
       └─ await 停稳 → auditStartupEntries（必需行失败→启动失败；可选行→warn）
```

### 3.2 生效树的分层顺序（`prepareDesktopProfile` 实读）

1. **bundle 层**：`dsh.profile.bundles` 按 manifest 顺序，各 bundle 的
   `dsh.bundle.patch`（如 `@deepseek-ai/dsh-base` → `dsh-web-app` → 用户插件）
2. **desktop 补丁**：`dsh-plugin-desktop/cordis.patch.yml`，插在 `dsh-web-app` 之后
   （插入 desktop-shell/terminal/diagnostics/notifications/pnpm/profiles/updates 七行，
   并改写 web-runtime 行）
3. **market provider 层**：dshmarket bundle 层（当前 profile 为 dshmarket 1.38.1，宿主自动注入，见 §9.1#3）
   或 community-market 单行（按 provider 二选一，冲突即 marketFailure）
4. **profile 用户层**：`profiles/desktop/cordis.patch.yml`
5. **home 机器层**：`~/.dsh/cordis.patch.yml`
6. AA 层（若启用）
7. **settings / web-runtime 覆盖行**（dshHome、port 等宿主注入 config）

同一行"后层优先"；patch 替换整行 `config`（非深合并）；insert 追加。

### 3.3 三套"停用"机制（重要——此前 DSH++ 只知其一）

| 机制 | 存储 | 生效时机 | 属主 |
|---|---|---|---|
| market 停用 | `.dsh-market/state.json` `disabled[]` + 用户 patch `- id: X`/`disabled:` 行 + `hot-*.yml` 临时挂载 | dshmarket 内 HMR 通道 | **dshmarket 插件** |
| Desktop 私享状态 | `<userData>/plugin-management/state.json` `{version:1, profiles:[{profileName, disabledBundles:[]}]}`（文件锁+原子写+上限） | 下次启动（layer 过滤） | dsh-plugin-desktop `DesktopPluginsService`（仅 community-market provider 时读取） |
| bundles 列表 | profile `package.json` `dsh.profile.bundles` | 下次启动 | profile manifest |

当前 profile 的 effective provider = **dshmarket**，故 Desktop 私享状态不参与，
dshmarket 自己的 state.json + patch 行是唯一停用通道（与 DSH++ 现状对齐的口径一致）。

### 3.4 HMR 结论（解决悬而未决的问题）

- **CLI**（`dsh --profile X`）：`patchReload: 'live'`（web/desktop profile 默认值）时，
  CLI 挂载 `timer` + `hmr(root: [])` 并调用两次 `watchUserPatches`（profile 层 + home 层）：
  文件变更 → 重读 patch → 重组全层列表 → `Include.update` → 树 diff →
  **新插件行挂载 / 旧行卸载 / config diff 热更新**，全部活体进行。
- **打包 Desktop**：`host-bootstrap.ts` 只调 `boot()`，**不调 `watchUserPatches`、
  不挂 hmr 服务** → 用户 patch / bundles / market state 变更**下次启动生效**。
  这是框架能力存在但宿主没接线，不是框架限制。
- 含义：in-host DSH++ 面板里"改完立即生效"不能指望宿主 HMR；
  变更后的生效路径 = **`DesktopActionsService.requestRestart()`（用户确认式重启）**
  或 dshmarket 自己的 hot 通道（`/dsh-market/restart`、hot-*.yml）。
  P0 需实测确认 desktop 进程内 hmr 服务是否真的缺席。

### 3.5 dshmarket 的 HTTP API（P2 的调用面）

dshmarket 注册在 `/dsh-market/*`（同源，浏览器与 in-host 均可达）：

- `GET /dsh-market/installed`、`/status`、`/logs`、`/check`
- `POST /dsh-market/install`、`/uninstall`、`/toggle`、`/restart`、`/cancel`、`/rollback`
- `POST /dsh-market/snapshots`、`/restore-snapshot`、`/delete-snapshot`、`/backup`、`/restore`
- `POST /dsh-market/use-skin`（市场自带皮肤接口）
- `GET /dsh-market/api/v1/capabilities|operations|updates|rollback|restart`（v1 结构化 API）

**快照+回滚+日志是 market 内建的**——DSH++ Python 侧自造的"操作前备份"逻辑
在 P2 可以整体退场，改为调用 market 事务。

---

## 4. DSH 插件的标准形态（以 dsh-plugin-wallpaper-engine 0.7.2 为参照）

一个 DSH 插件 = 一个 npm 包：

```
dsh-plugin-xxx/
├─ package.json
│   ├─ main: lib/index.js              # 宿主入口
│   ├─ exports: { ".": host, "./client": client, "./cordis.patch.yml", "./package.json" }
│   ├─ dsh.bundle.patch: ./cordis.patch.yml
│   └─ dsh.client: { inject: [客户端依赖], platform: "web", immediately: true }
├─ cordis.patch.yml                     # 宿主行：- insert: [{ id, name: '<pkg>' }]
├─ lib/index.js                         # 宿主入口（Node 侧）
└─ lib/client.js                        # 客户端入口（浏览器侧，预构建 IIFE）
```

**宿主入口契约**（wallpaper-engine 实读）：

```js
export const inject = ['webServer'];        // 硬依赖：Loader 等服务激活后挂载
export function apply(ctx) {
  const webServer = ctx.webServer;
  const disposers = [];
  disposers.push(webServer.register({ method: 'GET', path: '/xxx/inventory', handler: async (req,res) => {...} }));
  // ... 文件读写、child_process、worker 都行（宿主是完整 Node）
  return () => { for (const d of disposers) d(); };   // fiber dispose 时回卷
}
```

**客户端入口契约**（预构建 bundle，`tsdown.client.ts` 生成器产出）：

```js
window.__ModuleLoader__.load({
  id: "dsh-plugin-xxx",
  factory: (require) => {
    const React = require("react");        // 客户端模块系统里已注册
    const ReactDOM = require("react-dom");
    // 渲染、fetch('/xxx/...') 同源打宿主路由、Portal 到 <body> ...
  }
});
```

**客户端装载链**：宿主装配 client roster（扫描 bundle 层里每个启用行的包的
`dsh.client` 声明，`platform==='web'` 入选）→ 向 `<head>` 注入 boot graph →
`@deepseek-ai/dsh-client-modules` 的 ModuleLoader 逐个装载 → **roster 在启动时
计算 → 新客户端 bundle 下次启动才出现**（与宿主 HMR 缺席叠加：装插件必重启）。

**持久化约定**：插件自己的数据文件放用户目录（如 `~/.dsh-xxx/config.json`），
**port 无关**（Desktop 每次随机 `--port 0`，localStorage 会丢）。

---

## 5. API 边界清单（DSH++ 将依赖的稳定面）

**依赖（稳定，已验证语义）：**

| 面 | 来源 | 用途 |
|---|---|---|
| `ctx.webServer.register({method,path,handler})` | dsh-host-webserver | DSH++ API 路由 |
| `ctx.get(name)` / `ctx.<service>` / `inject` 导出 | core | 服务发现（软/硬依赖） |
| `ctx.loader.entries()` / `ctx.loader.resolve(id)` | loader | **免费插件清单**（每行 id/name/config/disabled/fiber.state） |
| `ctx.logger` / `ctx.effect(fn, label)` / `ctx.on(event, cb)` / `ctx.emit` | core | 日志、生命周期回卷、事件 |
| `ctx.hmr.watch(path, cb)`（若 hmr 在场） | hmr | 文件监视（面板"刷新"的主动通道，可选） |
| `DesktopActionsService.requestRestart()` | dsh-plugin-desktop 注入 | 用户确认式重启（硬约束合规的唯一重启路径） |
| `DesktopPluginsService.list()/preview*/execute*` | dsh-plugin-desktop（community-market 时） | 备用管理面 |
| `/dsh-market/*` HTTP API | dshmarket | install/uninstall/toggle/snapshot/rollback/logs |
| profile `package.json`（dependencies + dsh.profile） | 文件 | 安装口径 |
| `~/.dsh-skins/**` 用户数据 | 文件 | 皮肤 + 迁移期 registry |

**不稳定面（Cordis 官方声明 may change，需 pin + 探测）：**

- `Entry.update` 的 partial-dispose 瀑布细节、`routeJournal` "file wins" 语义
- `applyEntryPatches` 的 group insert 规则
- `window.__ModuleLoader__` facade（client-modules 契约 C6）
- dshmarket 路由名/参数（1.38.1 口径，升级可能变）

**pin 策略**：dshpp bundle 的 `peerDependencies` 声明
`@deepseek-ai/cordis ^4`、`@deepseek-ai/dsh-host-webserver >=0.1.0-rc` 等；
启动时做 **capability probe**（探测服务在场性、API 形状），probe 失败 → 面板显示
"宿主版本不兼容"而非崩溃（mirror wallpaper-engine 的 `ctx.get` 软守卫风格）。

**红线（in-host DSH++ 不得做的事）：**

1. 不经 market/宿主事务直接改 `cordis.patch.yml` 的 market 属主行
   （journal "file wins" + EntryOwner 会把运行期树变更路由回属主，双写必冲突）
2. 不 spawn/拉起宿主进程（硬约束；in-host 结构上也不能）
3. 不改写宿主拥有的文件（`cordis.yml` 根每次启动被重写为 `[]`、
   `hot-*.yml` 是 market 临时件）
4. 不用 `ctx.loader.create/update/remove` 做管理写操作
   （走 journal 且属主是 bundle 层 → 不可靠）；写操作一律
   market HTTP API 或 Python 期已验证的文件事务（P2 前过渡）

---

## 6. 分阶段计划

### P0 — 探针 bundle：验证 API 边界（✅ 完成，2026-09-17）

**目标**：一个最小 dshpp bundle 装进 live profile，实测 §5 边界清单。

产出 `D:\DSH\DSH++\probe\`：

```
probe/dsh-plugin-dshpp/
├─ package.json            # dsh.bundle.patch + dsh.client{platform:web, immediately:false}
├─ cordis.patch.yml        # - insert: [{ id: dshpp, name: 'dsh-plugin-dshpp' }]
├─ lib/index.js            # 宿主半：
│   #   - 无 inject（软守卫一切）
│   #   - GET /dshpp/capabilities → { services: 在场列表, hmr: bool,
│   #       loaderEntries: [{id,name,disabled,fiberState}], marketRoutes: probe 结果 }
│   #   - GET /dshpp/tree → ctx.loader.entries() 全量快照
│   #   - GET /dshpp/health → { ok: true, pid, uptime, nodeVersion }
└─ lib/client.js           # 客户端半：
    #   - 在 GUI 设置区/侧栏注入一个 "DSH++" 入口（最小 DOM，Portal 到 body）
    #   - 点击开新路由页 /dshpp（fetch /dshpp/capabilities 渲染）
    #   - 页面顶部显示 hmr 在场性（回答 §3.4 的实测问题）
```

**验收**（用户重启 Desktop 后逐项核对）：
1. 插件行激活（`/dshpp/health` 200；GUI 出现入口）
2. `ctx.loader.entries()` 返回完整树（含 dshmarket、desktop-* 行、fiber 状态）
3. hmr 服务在场性实测（预期：缺席 → 确认"桌面端变更下次启动生效"）
4. market 路由可达性（`/dsh-market/installed` 等）
5. 停用/卸载该插件：`/dsh-market/toggle`、`/dsh-market/uninstall` 行为确认
   （停用走哪条存储？卸载是否清理 node_modules + dependencies + bundles？）
6. 客户端 bundle 下次启动才出现（装→重启→出现；卸载→重启→消失）

**风险**：装探针 = 动 live profile。缓解：安装前快照
（`/dsh-market/snapshots` 或手动 copy profile 目录到 `D:\DSH\DSHdata\.dsh-skins\backup-probe-<ts>\`），
失败走 market rollback / 手动还原。

### P1 — 面板 in-host 只读版（+ 内嵌安装器 GUI 化）

- dshpp bundle 0.1.0 已按新安装模型就位（`dshpp/bundle/`，file:// 行 + client 半）；
  客户端半升级为完整面板（panel.html 3870 行的 JS 逻辑移植为 client bundle；
  宿主半提供 API：`/dshpp/plugins`（= entries × package.json 元数据 × origin 判定，
  复用 Python 期算法）、`/dshpp/market`（代理 market 读接口）、`/dshpp/skins`）
- **安装/卸载生命周期 GUI 化（P1a）**：宿主半注册模型 Tool + `/dshpp/installer/*`
  路由，spawn 本目录的 `install.mjs` / `uninstall.mjs`（子进程不受插件 fs 沙箱限制，
  与 dshmarket spawn pnpm 同模式）；按钮 = 用户显式同意，脚本自带快照 + 幂等
- 只读：面板不直接写任何文件；"重启生效"提示走 `DesktopActionsService.requestRestart()`
- 验收：面板与 Python 版信息一致（diff 校验）；GUI 一键 install→重启→uninstall→重启
  全循环；零 pnpm 触碰（lockfile/node_modules 前后 diff 为空）

### P2 — 写操作走 market 事务 + 一次性迁移

- toggle/install/uninstall 全部改调 `/dsh-market/toggle|install|uninstall`
  （操作前 market 自动快照；失败 rollback）
- 一次性迁移工具（面板按钮 + CLI 双入口）：
  - A-track registry.json 记录 → 并入 profile dependencies/bundles（已在场则幂等跳过）
  - 迁移完成 → registry.json 标记 `migrated: <ts>`，Python 版降级为只读并提示
- 双跑期（P2 完成前）：Python 版保持现状（市场感知已对齐），两版只读口径必须一致
- 验收：面板完成 install/toggle/uninstall 全循环；market log.ndjson 记录完整；
  旧 registry 迁移后 Python 版不再报"未跟踪 bundle"

### P3 — 皮肤插件化

- 皮肤 = 纯客户端 bundle（`dsh.client` 半）：
  - fetch `/dshpp/skin.css`（宿主半把 `~/.dsh-skins/active/*.css` 读出来服务）
  - `<style id="dshpp-skin">` 注入/移除（含 `data-dshpp-skin` 标记，幂等）
  - 皮肤元数据（启用/停用/版本）仍存 `~/.dsh-skins/skin.json`（用户数据，非宿主文件）
- CDP/9222 路径整体退役；`dsh-skin.py` 保留为 legacy CLI（读同一 skin.json）
- 验收：皮肤在打包桌面生效（无 debug 端口）；停用/切换无需重启（客户端纯 CSS 注入）

### P4 — 发布 + 退役 Python 运行时

- `dsh-plugin-dshpp` 发布到市场（npm 或市场 channel；版本号从 P0 探针延续）
- Python 运行时（server.py 8765 + token + panel.html 服务）退役为
  **legacy 只读诊断 CLI**（保留 1–2 个版本周期）
- `构建桌面应用.bat` 产物冻结镜像不再需要更新（bundle 随市场分发）
- 验收：全新机器上 `dshmarket install dsh-plugin-dshpp` → 重启 → 面板+皮肤全功能；
  不装 Python 也能用

---

## 7. 硬约束符合性（无自动拉起）

| 阶段 | 拉起动力的来源 | 合规性 |
|---|---|---|
| P0–P4 in-host | 插件运行于宿主进程内，唯一重启路径是 `requestRestart()`（宿主发起、用户可见） | 结构上不可能 spawn 宿主 |
| 迁移工具 | 用户点按钮/跑 CLI | 合规 |
| Python legacy | 保留期不再有任何 watch/自启逻辑（现状即无） | 合规 |

"面板提示'重启后生效'"从 UX 文案升级为**唯一机制**——与 §3.4 的宿主事实一致。

---

## 8. 开放问题（P0 实测回答）

1. **desktop 进程内 hmr 服务是否真的缺席？** → **已回答**：`ctx.get('hmr')` 缺席；
   根树 `hmr` 行 `disabled: true`（dsh-base 声明，config watching 走 launcher 的
   watch-only fallback，不依赖该行）。模块热更前提不成立，重启是唯一机制。
2. **`ctx.loader.entries()` 的可见范围？** → **已回答**：动态插件 ctx 即可枚举完整
   根树（125 行），含 client-only 行、dshmarket 行、file:// skin 行、desktop-* 行。
   静态 bundle 运行于 root ctx，是动态 ctx 的超集（§9.2 边界表）。
3. **market `/toggle` 对 origin=dshpp 包的行为？** → **已回答（代码实证）**：
   toggle 对 bundle 层包 = 写 profile `cordis.patch.yml` 的 `disabled:` 行 +
   market `state.json` 的 disabled 列表 + 会话内 hotMount/hotUnmount（client 面）。
   与包的安装来源无关（dshmarket 不读 registry.json；P2 前 origin=dshpp 的包
   只能由 DSH++ 自己经 profile API/pnpm 卸载，market 卸载按钮不适用）。
4. **`/dsh-market/uninstall` 的清理边界？** → **已回答**：移除 hot-mount（hotUnmount）、
   从 profile bundles/dependencies 移除（pnpm）、删 hot-*.yml、更新 state.json。
   用户 patch 行（如 disabled 行）**不在 market 清理范围** → P2 仍需文件级兜底清理
   用户 patch 与 dshskin 管理块。
5. **client roster 是否含 `immediately: false` 的懒 bundle？** → **已回答**：
   `clientModules.graph()` 返回完整 60 项 web 启动图（含 dshmarket #53、
   skins #44–47、dsh-plugin-desktop #56、bridge #58），懒 bundle 亦在图内，
   仅装载时机不同。新增 bundle 重启后必现于该图（清单第 6 项的观测点）。
6. **`webServer.register` 路由 vs SPA fallback？** → **已回答（比预期更严格）**：
   exact 路由注册成功且生效，但 **web 端口全部路由**（含插件路由）都在
   browser-auth cookie 门之后：无 cookie 请求（外部或进程内 fetch）一律 403
   `forbidden`；GUI 浏览器（持 cookie）可直达一切路由。验收必须从 GUI 浏览器进行。

---

## 9. P0 执行记录（2026-09-17，宿主在线适配）

> 本节为在**运行中的 desktop 会话内**执行 P0 的实证记录，替代原计划"用户先退出"的
> 离线窗口流程。依据：打包宿主无文件监听（host-bootstrap 仅调用 boot()，patchReload
> 无接线）+ Include file-wins ⇒ 磁盘 profile 变更不影响运行进程，可安全在线安装，
> 于用户下次手动重启统一生效。动态插件数据不跨重启——所有事实已落盘。

### 9.1 事实修正（实测覆盖计划假设）

| # | 计划原假设 | 实测事实 |
|---|---|---|
| 1 | dshmarket 1.47.0 | **1.38.1**（`app/node_modules/dshmarket`） |
| 2 | market 路由浏览器+宿主内可达 | **web 端口全部路由在 browser-auth cookie 门后**：无 cookie（外部/进程内 fetch）一律 403；仅 GUI 浏览器可达 |
| 3 | market 是 profile 依赖 | **宿主自动注入**：`src/profile.ts` 依 provider state（`%APPDATA%\DSH Desktop\desktop-market\state.json` = `requested:"dsh-market"`；provider 默认 disabled，本部署显式启用）把 dsh-market 行+bundle 加进组成；profile dependencies 本无 dshmarket |
| 4 | （新）插件 fs 服务 workspace = 会话 workspace | **workspace 根 = 宿主进程 cwd**（`dist\win-unpacked`）：绝对路径写根外被 workspace-write 拒绝；相对路径解析到 app 目录。P1 面板持久化须走 `desktopProfiles` 服务 API 或显式 `sandboxPolicy`（审批门）写 |
| 5 | （新）动态 ctx = root ctx | **不等价**：动态（会话域）ctx 缺席 `hmr/dshmarket/modules/registry/reflect/logger/events/cmdline/desktopPlugins`；拥有 `loader/webServer/desktopRuntime/desktopPnpmBootstrap/desktopProfiles/desktopActions/clientModules/connection/shell/sandbox/sessions/jobs/subprocess/workspaceRegistry/spillStore/web/timer/settings/systemPrompt`。静态 bundle 在 root ctx（超集） |
| 6 | （新）hot-*.yml 持久 | **ephemeral**：dshmarket 每次启动 `cleanHotDir` 删除全部 `hot-\d+\.yml`（`lib/hot.js`）。持久态在 profile bundles/deps；重启后 wallpaper 行走 bundle 层、hindsight 行被用户 patch 禁用 |
| 7 | （新）live 树 = 当前磁盘 patch 状态 | **live 树 = 本次 boot 时磁盘状态**。本次 boot 时 desktop 管理块含 4 条 dshskin 行（.bak 文件证明中间态 3 行），当前磁盘已裁剪为 2 行 ⇒ **重启后树中 dshskin 行将剩 2 条**（dsh-deepseek-web、dsh-plugin-token-usage） |

### 9.2 动态探针结果（dshpp-1，pkg-1..4，run-1..4）

- **服务存在性**（28 探针，19 存在 / 9 缺席）：存在 = webServer, loader, timer,
  settings, desktopRuntime, desktopPnpmBootstrap, desktopProfiles, desktopActions,
  connection, systemPrompt, clientModules, shell, sandbox, sessions, jobs, subprocess,
  workspaceRegistry, spillStore, web；缺席 = hmr, logger, reflect, registry,
  desktopPlugins, dshmarket, modules, cmdline, events。
- **loader 树**：125 行；dsh-market 行 enabled（name `dshmarket`）；4 条 file://
  dshskin 行（boot 态，见 §9.1#7）；基础 `webserver` 行 disabled——活动 web 服务器是
  `dsh-plugin-desktop/webserver`（id `desktop-webserver`）。
- **clientModules.graph()**：60 项（rev …-058）；新增 `dsh-plugin-dshpp` 重启后应现（清单第 6 项）。
- **webServer.register**：exact 路由注册成功（`/dshpp-live/data`）。
- **数据通道实证**：`spillStore.saveText` → 会话 spill 临时文件（agent 可读）；
  `harness.defineTool` → 动态 Tool 次一模型步可用。P1 调试可用。
- **客户端半**：`shell.overlay` slot 注册成功（"DSH++ live" 药丸，run-4）——
  P1 slot 机制在打包 Electron 内验证通过。
- 完整报告：`probe/p0-dynamic-probe.json`（spill 同源副本）。

### 9.3 安装状态（live profile，待重启生效）

- 回滚快照：`probe/rollback-snapshot-20260917-113813/`（profile 清单 ×5 含
  cordis.patch.yml.bak、.dsh-market ×4、registry.json）。
- profile `package.json`：bundles + `"dsh-plugin-dshpp"`；deps +
  `"dsh-plugin-dshpp": "file:D:/DSH/DSH++/probe/dsh-plugin-dshpp"`。
- `pnpm install`（v11.8.0，+1 包，lockfile 通过 supply-chain 策略）：
  `file:` 依赖 = **目录链接**（改源码即时反映，无需重装，直到 pnpm 剪枝/重链）。
- `registry.json`（DSH++ A 轨）：`dsh-plugin-dshpp` 条目已写
  （origin `dshpp`，hasClient true，dir 指向 profile node_modules）。
- 未触碰：`.dsh-market/*` 内容、dshskin 管理块、hindsight disabled 行、market 层行。

### 9.4 重启后验收（对应 §6 六项）

1. `/dshpp/health` 200 + GUI 入口（静态 bundle 客户端按钮）——GUI 浏览器内验证。
2. `/dshpp/tree`：125± 行，含 `dshpp` 行（enabled）+ 各 fiber 状态；
   预期 dshskin 行降为 2 条（§9.1#7）、hindsight 行 disabled、wallpaper 行 enabled。
3. `/dshpp/capabilities`：root ctx 超集（hmr 缺席、dshmarket 应在）。
4. `/dsh-market/installed` 等：`/dshpp` 控制台页浏览器侧探测。
5. `/dsh-market/toggle` + `/uninstall` 行为（§8#3/#4 已给预期：patch 行+state.json+
   持久性；uninstall 不碰用户 patch 行）。
6. client boot graph 出现 `dsh-plugin-dshpp` 条目（`/dshpp/tree` 无法直接看 graph——
   在 GUI 浏览器 DevTools 查 `window.__DSH_BOOT__`，或经 P1 面板）。

### 9.5 设计修正：内嵌安装器（用户决定，2026-09-17 13:52–14:52）

**用户原话**："安装后有问题，虽然它成为插件但是不直接安装到dsh桌面，而是它自己
内嵌安装程序方便出问题时卸载"。直接 profile 安装被否决。随后的取证证实这个
决定是对的——直接安装路径存在**两个独立故障**：

**故障 A（探针自身 bug）：webServer 竞态丢路由。** 用户 13:52 重启后，boot 日志
两次记录 `dshpp-probe: webServer service absent; probe routes unavailable`
（13:52:03 / 13:53:49 两次 boot）——探针行无 `inject`，apply() 早于 webServer
服务就绪执行，soft-guard 判缺席后**永久跳过**全部路由注册。参照
wallpaper-engine 的正确答案：`export const inject = ['webServer']`（硬依赖，
Loader 等服务就绪才调 apply）。0.1.0 已改。

**故障 B（桌面自身行为）：boot 期 pnpm 协调清掉 profile 依赖。** 13:55:01
（boot #3 后 63 秒、live 会话中）profile 的 `package.json` + `pnpm-lock.yaml` +
`node_modules` **同一秒整体重写**：dshpp 与 hindsight 的 bundles 条目、deps 条目、
lockfile 条目、node_modules 目录全部消失；market 日志无任何 uninstall 事件
（仅 boot 期对 hindsight 的 `toggle: no loader entry matched` ×3）。即桌面自己的
boot 流程（`reconcileProfilePnpmWorkspace` / 依赖迁移路径）把 12:05 的直接安装
剪掉了——**profile 依赖不是稳定的第三方安装面**。佐证：market `install.js`
注释自述会对"下次 boot 会炸"的包做 on-the-spot 移除；market 对非自己安装的包
toggle 直接报 `no loader entry matched`。

**幸存面（安装机制的正确选择）**：同一时刻 `cordis.patch.yml` **未被触碰**
（mtime 09:58:33 保持），4 条 dshskin file:// 行长期跨 boot + 跨清理存活。
file:// 行的完整机制（读 dsh-deepseek-web 0.1.0 实证）：
- host 半：Loader 按 file:// URL 加载模块（ESM，最近 package.json 定模块域）；
- client 半：clientModules 扫描器从 file:// URL 反查 package.json 的
  `dsh.client` 声明 → 进入浏览器 boot graph（重启前 graph 中 skins #44–47 实证）；
- 行 options 只需 `{id, name: 'file:///...'}`；`inject` 同样生效（模块级导出）。

**新安装模型（已实现）**：`D:\DSH\DSH++\dshpp\` = `bundle/`（插件本体）+
`install.mjs` + `uninstall.mjs` + `installer-core.mjs` + `snapshots/` + `README.md`。
安装 = ① bundle 复制到 `.dsh-skins\plugins\dsh-plugin-dshpp\<version>\`
② 桌面 profile `cordis.patch.yml` 写入受管块 `# >>> dshpp:plugins >>>`（file:// 行，
幂等替换/追加）③ `registry.json` A-track 条目。卸载 = 三步逆操作，只动这三处，
是 GUI 坏掉时的应急终端路径。零 pnpm / lockfile / node_modules / market 账本。
两脚本均幂等、自带操作前快照、**从不**启动/重启 DSH Desktop。

**live patch reload 证伪**：14:52:03 安装写入后 live tree 保持 169 行（无新行）、
host 日志无任何 reload 事件——打包版桌面**不监听** profile patch 文件
（`patchReload: live` 无 watch 接线，与 §3.4 结论一致；13:55:05 的
`patch: entry hindsight not found` 是 boot 期 patch 应用，非 watch）。
⇒ 安装/卸载后必须重启生效。

**残留状态说明**：hindsight 的 `disabled: true` patch 行现为孤儿（其 bundle 被
故障 B 清掉，行目标不存在），每次 boot 产生一条无害 warning；该行属 market/用户
状态（state.json 仍记 disabled），**不动**——待用户决定 hindsight 去留。

### 9.6 重启后验收（新安装模型，取代 §9.4）

重启 DSH Desktop 后（GUI 浏览器内验证——web 端口有 403 cookie 栅栏）：

1. **宿主半**：`/dshpp/health` 200（HOST ALIVE）；`/dshpp/tree` 含
   `dsh-plugin-dshpp` 行（fiber active，file:// name）；`/dshpp/capabilities`
   services 在场列表。
2. **客户端半**：GUI 出现浮动「DSH++」按钮（client graph 含
   `dsh-plugin-dshpp`，immediately）；点开 → `/dshpp` 控制台渲染三块。
3. **稳定性**：market 插件面板**不应**出现 dshpp 卡片（非 profile 依赖）；
   dshskin 两条 file:// 行、hindsight 行、wallpaper 行不受影响。
4. **卸载循环**：`node dshpp\uninstall.mjs` → 重启 → tree 无该行、浮动按钮消失、
   控制台 404；再 `node dshpp\install.mjs` → 重启 → 恢复（幂等性实证）。
5. **硬约束**：全程 DSH Desktop 未被任何 dshpp 脚本启动/重启（日志时间线核对）。

---

## 附录 A：本计划的源码依据（已读文件）

- cordis：`package.json`、`packages/core/{README.md,src/index.ts,src/service.ts,src/context.ts}`、
  `packages/include/{src/patch.ts,src/index.ts}`、`packages/loader/{src/index.ts,src/config/entry.ts,src/config/tree.ts,src/config/utils.ts}`、
  `packages/hmr/src/index.ts`、`packages/create/src/index.ts`
- harness：`packages/boot/app-boot/src/{index.ts,watch-config.ts,profile.ts(节选)}`、
  `apps/cli/src/profile-boot.ts`
- 桌面宿主：`dsh-plugin-desktop/{package.json,cordis.patch.yml,src/host-bootstrap.ts,src/profile.ts(节选),src/desktop-plugins.ts(节选)}`
- 参照插件：`dsh-plugin-wallpaper-engine/{package.json,cordis.patch.yml,lib/index.js(节选),lib/client.js(节选)}`
- market：`dshmarket/lib/routes.js`（路由面）、`.dsh-market/{state.json,log.ndjson}`（实态）
- live profile：`~/.dsh/profiles/desktop/{package.json,cordis.patch.yml,hot-1.yml}`

## 附录 B：与 Python 现状的映射

| Python 现状 | 插件化后的归宿 |
|---|---|
| `plugin_manager.py` adopt（dependencies 扫描 + origin 判定） | P1 `/dshpp/plugins`（同算法移植 JS，数据源改 entries × manifest） |
| market state.json 同步 / patch 行清理 | P2 退场 → market 自己管（toggle 走 API） |
| registry.json（A-track） | P2 一次性迁移 → 退场 |
| server.py 8765 + token | 退场（宿主 webServer 同源，无需 token） |
| panel.html | P1/P3 客户端 bundle 化（逻辑移植，UI 保留） |
| dsh-skin.py + CDP 注入 | P3 客户端 CSS 注入；dsh-skin.py 保留 legacy |
| 重启提示（UX） | P1 起 = `requestRestart()`（机制而非提示） |
