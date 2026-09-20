# DSHSkin 增强脚本作者指南

本指南面向给 DSHSkin「脚本市场 / 用户脚本」写增强脚本的作者。读完你能：写出符合规范、
可被安全审计、能在面板一键安装的 `.js` 增强脚本，并正确使用 `window.DSHSkin` 运行时 API。

> 模型：脚本在 DSH 渲染进程页面上下文运行（经 CDP 注入），**不修改** DSH 任何文件，
> 关闭增强/退出后页面恢复原样。脚本以页面全权限执行，因此 DSHSkin 对市场脚本做静态
> 权限分析与安装前确认——请如实声明权限，这是对用户负责。

---

## 1. 脚本长什么样

一个增强脚本就是一个 `.js` 文件，**头部用 `// @key: value` 写 frontmatter（前 20 行内）**，
正文用 `DSHSkin.def(...)` 注册一个幂等模块：

```js
// @name: 消息序号
// @version: 1.0.0
// @author: your-name
// @description: 给每条助手消息加上递增序号
// @source: https://github.com/your/repo
// @permissions: dom, storage

DSHSkin.def('msg-index', function (R) {
  var n = 0;
  R.on('[data-conversation-scroll] [data-message-role="assistant"]', function (el) {
    if (el.dataset.indexed) return;
    el.dataset.indexed = '1';
    n += 1;
    var tag = R.ui.el('span', 'margin-right:6px;opacity:.6', '#' + n);
    el.prepend(tag);
  }, { once: false });
});
```

### frontmatter 字段

| 字段 | 说明 |
|---|---|
| `@name` | 脚本名（市场列表展示） |
| `@version` | 版本号 |
| `@author` | 作者 |
| `@description` | 一句话说明 |
| `@source` | 源码/仓库地址，便于用户追溯来源 |
| `@permissions` | 权限声明，逗号分隔，见第 3 节；缺省视为 `dom` |

---

## 2. 运行时 API（`window.DSHSkin`，回调里形参常写作 `R`）

### 模块与生命周期
- **`R.def(id, setup)`**：注册模块。以「id + 内容哈希」去重，重复注入只跑一次；脚本改动后
  哈希变化会重新执行。`setup(R)` 内抛错会被捕获并上报到页面错误日志，不会炸掉其它模块。
- **`R.ready(cb)`**：DOM 就绪后回调（已就绪则立即执行）。
- **`R.wait(selector, ms=15000)`**：返回 Promise，用 MutationObserver 等元素出现，超时 resolve null。
- **`R.on(selector, cb, opts)`**：等到元素后回调；`opts.once=false` 时持续监听后续新出现的元素
  （列表/流式消息场景必备）。
- **`R.interval(fn, ms)`**：定时器，页面卸载时自动清理。

### UI / DOM 工具（`R.ui`）
- **`R.ui.style(id, text)`**：按 id 幂等注入/更新一个 `<style>`。
- **`R.ui.el(tag, cssText, html)`**：快速建元素。
- **`R.ui.on(el, ev, fn)`**：安全绑定事件（el 为空不报错）。
- **`R.ui.remove(id)`**：按 id 移除元素。

### 反馈与存储
- **`R.toast(msg, ms)`**：页面轻提示。
- **`R.log(...args)`**（info 级）/ **`R.error(mod, ...args)`**（error 级）：写运行日志，
  面板「日志 / 页面错误」会回收展示；catch 里请用 `R.error`，便于用户定位是哪个模块出错。
- **`R.storage.get(k, dflt)` / `set(k, v)` / `remove(k)`**：带 `dsh-skin:` 命名空间前缀的
  localStorage，自动 JSON 序列化，互不污染站点存储。

### 快捷键（统一注册表，勿硬编码）
- **`R.isHotkey(e, action)`**：判断键盘事件是否等于某 action 当前绑定的组合键。
- **`R.config.hotkeys`**：`{ action: 'Ctrl+Alt+...' }`，由用户在设置里改键，脚本读取它做提示文案。

```js
document.addEventListener('keydown', function (e) {
  if (R.isHotkey(e, 'ui-tweaks.toggle')) { /* 切换 */ }
});
```

可用 action 见 `enhance_engine.HOTKEYS`。**不要**在脚本里写死 `Ctrl+Alt+x`，否则会和其它
模块/用户改键冲突。

---

## 3. 权限声明与安全审计（市场脚本必看）

安装市场脚本前，面板会静态扫描源码、展示「声明权限 / 实际使用能力 / 用了但没声明 / 风险等级」，
用户确认后才落盘。请按实际用到的能力**如实声明** `@permissions`：

| 权限 | 触发它的源码特征 | 风险 |
|---|---|---|
| `dom` | `document.` / `querySelector` / `addEventListener` | 基础能力，默认即有，无需特意声明 |
| `storage` | `localStorage` / `sessionStorage` / `indexedDB` | 低 |
| `network` | `fetch(` / `XMLHttpRequest` / `WebSocket(` / `sendBeacon` / `EventSource(` | **高** |
| `eval` | `eval(` / `new Function(` | **高** |
| `cookie` | `document.cookie` | 中 |
| `clipboard` | `navigator.clipboard` | 中 |
| `navigation` | `window.open(` / 改 `location.href/assign/replace` | 低 |

规则：
- 实际用了某能力却没在 `@permissions` 声明（dom 除外），会被标为 **undeclared（未声明）** 提示用户；
- 用到 `network`/`eval` 即判**高风险**，`cookie`/`clipboard` 为中风险；
- 扫描宁可误报也不漏报（纯文本里出现 `fetch(` 字样也算），如确属误报可在描述里说明；
- **不要**读取/外发用户凭证、会话内容到外部域名；高风险网络行为会被用户直接看到并拒绝。

最小权限原则：纯界面增强通常只需要 `dom`（可省略不写）；要记住用户偏好再加 `storage`。

---

## 4. 选择器纪律

- 只用 DSH 稳定契约：`[data-slot="..."]`、`[data-composer-card]`、`[data-conversation-scroll]`、
  `[data-message-role]` 等，以及 DSHSkin 打的 `[data-dsh-skin="<region>"]`。
- **不要**依赖 `KDVgQq_`、`UyxHeG_` 这类 CSS Modules 随机哈希类名——官方一升级就失效。
- 流式/异步渲染的内容用 `R.on(sel, cb, {once:false})` 或 `R.wait`，不要假设一次就能查到。

---

## 5. 本地放置与调试

- 放到用户数据目录 `~/.dsh-skins/enhance/`（文件名即脚本），或在面板「DSH 增强 → 新建用户脚本」编辑。
- 面板「应用增强（CDP）」热注入，改完重新应用即可；`R.log/R.error` 输出在面板日志里看。
- 模块幂等：刷新页面或重复注入不会重复绑定（`R.def` 去重）；需要在元素级防重复时自行打标记
  （如 `el.dataset.xxx = 1`）。

## 6. 发布自检清单

- [ ] 前 20 行内写全 `@name/@version/@description/@permissions`，建议含 `@author/@source`
- [ ] `@permissions` 与实际能力一致，无 undeclared
- [ ] 只用稳定 `data-*` 选择器，无随机哈希类名
- [ ] 用 `R.def` 注册、异步内容用 `R.on(...,{once:false})`，无重复绑定/泄漏定时器
- [ ] 快捷键走 `R.isHotkey` / `R.config.hotkeys`，不硬编码
- [ ] catch 中用 `R.error` 上报；不外发用户数据
- [ ] 在浅色/深色主题下都验证过显示
