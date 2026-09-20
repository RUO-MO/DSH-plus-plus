# CDP 注入常见故障 FAQ（Windows）

DSHSkin 的换肤与增强**唯一运行时通道是 CDP（Chrome DevTools Protocol）**：连接 DSH 渲染进程
自带的远程调试端口（默认 `9222`）热注入 CSS/JS，不改 DSH 文件。本文汇总连接失败的各种情况、
根因与处理办法。先跑一次体检：

```bash
python dsh-skin.py doctor      # 环境/端口/依赖总检
python dsh-skin.py cdp-status  # 仅看 CDP 状态
```

面板「概览」也会显示 CDP 状态：`ready`（可注入）/ `no-debug`（DSH 在跑但没开调试端口）/
`stopped`（DSH 没运行）。

---

## Q1. 状态一直是 `no-debug`：DSH 开着，但连不上 9222

**根因**：远程调试端口只在**开发模式**下自动开启。DSHSkin 确认过：
`apps/desktop/scripts/dev.ts`（`pnpm start:desktop` / `start-desktop.cmd`）会给渲染进程加
`--remote-debugging-port=9222`；而**生产打包的 exe（`apps/desktop/src/main.ts`）不带这个参数**，
所以直接跑正式版 exe 没有 9222，属于预期。

**处理**：
1. 用 DSHSkin 提供的 `启动DeepSeekHarness.bat`（或你的 `.lnk → start-desktop.cmd → pnpm start:desktop`）
   以开发模式启动，这是和你桌面快捷方式等价的链路。
2. 或让面板「以调试模式重启」：DSHSkin 会用 `--remote-debugging-port=9222` 透传重启 DSH。

> 注意 Electron 单实例：第二个实例会聚焦已有窗口后退出。因此当一个**没带调试端口**的 DSH
> 已经在跑时，必须先把它完全关闭，再带 flag 启动，否则新参数不生效。

---

## Q2. `pnpm start:desktop` 直接崩 / 起不来（Node 版本坑）

**根因**：全局 pnpm shim 可能命中 **Node v20**，而 pnpm 11 需要 **Node 22+**，版本不够会启动失败。

**处理**：用 corepack 自带的 pnpm 并配合 Node 22+（本机验证可用的是 `D:\program\node.exe` v24 +
corepack pnpm 11.7.0）。`启动DeepSeekHarness.bat` 已内置这套解析，优先用它启动，不要在一个
命中旧 Node 的终端里手动 `pnpm start:desktop`。可用 `node -v`、`pnpm -v` 自查。

---

## Q3. `stopped`：探测不到任何页面 target

- 确认 DSH 窗口确实已打开（不是只起了 dev server）。
- 浏览器访问 `http://127.0.0.1:9222/json`，应能看到一串 target，其中有 `type:"page"` 且带
  `webSocketDebuggerUrl`。打不开就是端口没起来（回到 Q1）。
- 端口被改过时，在面板「设置 → CDP 端口」或环境变量 `DSH_SKIN_CDP_PORT` 指定实际端口。
- 安全软件/企业策略拦截本地调试端口时也会探测失败，检查防火墙是否放行本机回环。

---

## Q4. 开了多个 DSH 窗口，只有第一个窗口被换肤

旧实现只注入第一个匹配 target。现版本 `find_page_targets` 会**枚举全部页面 target** 注入，
并由 `SkinWatcher` 监听新窗口/新 target 自动补注入。若仍有窗口漏注入：
- 点一次「立即注入」强制全量补注入；
- 确认所有窗口都来自同一个带 9222 的 DSH 进程（不同进程/不同端口不会被一起覆盖）。

---

## Q5. 刷新页面后有一瞬间是没换肤的“裸 UI”

CDP 是页面起来之后再连，首次加载/硬刷新存在天然的注入窗口期。现版本对每个 target 使用
`Page.addScriptToEvaluateOnNewDocument` 做**预注入**（下次文档创建前就埋入引导），并带 ready
门控，正常切换几乎无感；手动在 DevTools 里硬刷新的当帧仍可能闪一下，属 CDP 机制本身，重连后即恢复。

---

## Q6. Python 报 `No module named websocket` / CDP 通道不可用

CDP WebSocket 需要 `websocket-client`（注意不是 `websockets`）：

```bash
python -m pip install -r requirements.txt
# 或
python dsh-skin.py deps --install
```

缺它时主题管理、面板仍可用，只是无法热注入，界面会明确提示。要求 **Python 3.11+**
（3.9 及以下不保证可用）。Windows 上命令行敲 `python` 可能命中 Microsoft Store 的占位 stub，
请用真实解释器（如 `py -3.11` 或安装目录下的 `python.exe`），`_ensure_deps.bat` 已自动跳过 Store stub。

---

## Q7. WebSocket 一握手就被断开 / 报 origin 相关错误

连接 DSH（Electron/Chromium）的调试 WebSocket 时必须带 `suppress_origin=True`（等价于不发送
Origin 头），否则会被对端拒绝。DSHSkin 内部已统一这样处理；如果你基于本项目二次开发，自己建
WebSocket 时记得同样设置。

---

## Q8. 官方升级 DSH 后，主题/增强像失效了

- 换肤只依赖稳定的 `data-*` 契约，随机哈希类名变化不影响；但若官方改了插槽结构，可能出现选择器漂移。
- 面板会在启动时做 DOM 指纹/版本对比并角标告警；按提示「一键重测」（probe）查看哪些区域 alive/dead，
  必要时「重建基线」。
- 预览快照也会提示过期，可「一键重抓」（需要 DSH 正以调试模式运行）。

---

## Q9. 如何确认注入是干净、可完全还原的

- 「还原官方样式」会从所有 target 移除注入的 `<style>` 与引导标记，刷新后页面回到官方原样；
  全程没有改 DSH 安装目录里的任何文件。
- `python dsh-skin.py uninstall` 会移除全部注入、还原 `cordis.patch.yml` 托管块；加 `--purge`
  才会连带清理 `~/.dsh-skins` 数据目录（程序文件不自动删）。
