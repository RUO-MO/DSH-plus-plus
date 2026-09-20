# DSH++ 项目审查报告

> 审查日期：2026-09-17 · 审查方式：全量目录扫描 + 核心模块逐段通读 + 实测运行测试套件（Python 3.11）+ 环境探测
> 审查范围：`D:\DSH\DSH++` 全仓库（39 个 Python 文件 ~9.7k 行、17 个 JS 文件 ~1.9k 行、panel.html 3620 行单文件 SPA、4 份大文档、3 份 docs、21 套测试）

---

## 1. 项目概览

**DSH++（原 DSHSkin）** 是给 DeepSeek Harness 桌面版（Electron 壳）做「换肤 + 增强 + 会话/凭证管理 + 插件装配」的本地工作台。核心设计三原则在代码中贯彻一致：

| 原则 | 实现 |
|---|---|
| 零侵入 | 不修改 `deepseek-harness`/`dsh-desktop` 仓库任何文件；写操作只落在 `~/.dsh-skins/` 与 profile 的 `cordis.patch.yml` 受管块 |
| CDP-only 注入 | 唯一注入通道是渲染进程 `--remote-debugging-port=9222`（`cdp_skin.py` WebSocket 注入） |
| 完全可逆 | `.orig` 首写前备份、受管块标记、`restore_managed()` 一键还原、卸载零残留 |

**技术栈**：Windows + Python 3.11+（`ThreadingHTTPServer` 后端 + pywebview/Edge 桌面窗口 + pystray 托盘，PyInstaller onefile 打包）+ Node.js（`dshpp/` 内嵌安装器）+ 纯 JS 增强模块（无框架、无构建）。

**代码规模**：

| 模块 | 行数 | 职责 |
|---|---|---|
| `server.py` | 2206 | :8765 HTTP 后端、~65 路由、token 鉴权、5 个守护线程 |
| `plugin_manager.py` | 1807 | 双轨插件安装（A: pnpm/npm · B: file:// 受管块）、zip-bomb 防护、受管块读写/还原 |
| `dsh_env.py` | 995 | 路径/exe/node/pnpm 解析、dev/packaged 模式、进程探测、带调试端口重启 |
| `dsh-skin.py` | 914 | CLI 主入口（20+ 子命令），被 server 以 importlib 复用 |
| `enhance_engine.py` | 746 | DSHSkin 运行时模板、5 内置模块、用户脚本权限静态分析 |
| `session_store.py` | 580 | zstd-JSONL 会话只读解析、搜索/导出/备份/删除 |
| `theme_engine.py` | 628 | 14 参数主题 CSS 生成、region 诊断、模板版本迁移 |
| `cdp_skin.py` | 610 | CDP 多 target 注入/移除、probe 实测选择器、SkinWatcher |
| `panel.html` | 3620 | 10 分区单文件 SPA（无框架） |
| 其余 | — | `desktop_app.py`/`tray.py`/`updater.py` + `dshpp/` 安装器（Node） |

**数据目录** `~/.dsh-skins/`：config.json、enhance.json、selectors.json、logs.json、server.token、plugins/、enhance/、backups/。

---

## 2. 总体评价

**这是一份工程纪律很强的个人/小团队工具项目**。突出优点是：

1. **文档质量高**：每个模块头部 docstring 讲清职责与设计理由；README 覆盖快速上手/依赖分层/打包/FAQ；`docs/` 有主题作者、脚本作者、CDP FAQ 三篇面向第三方的指南。
2. **安全工程到位**（见 §4 清单）：token 鉴权 + Origin 校验、日志脱敏、zip-bomb/路径穿越防护、插件协议校验、脚本静态权限分析 + 两段式安装确认。
3. **可逆性是一等公民**：`.orig` 备份只在「首写且无受管块」时生成、永不覆盖；`restore_managed()` 有 5 种路径（restored-from-orig / removed-originally-absent / stripped-block-kept-user / removed-empty / already-clean）并有专门测试。
4. **优雅降级**：websocket-client / zstandard+PyYAML / pystray+Pillow 三档依赖缺失时对应功能降级而非崩溃，测试覆盖。
5. **测试意识好**：21 套测试覆盖鉴权、CDP 注入端到端（用迷你 HTTP+裸 WS 服务模拟，不依赖真实 DSH）、zip 安全、脚本安全、热键、配置迁移、插件双轨、漂移检测、快照、主题引擎、卸载/更新等。
6. **CI 已配置**：GitHub Actions windows-latest，Python 3.11/3.13 矩阵。

**主要短板**：不是 Git 仓库（治理缺失）、CI 在本机环境之外大概率跑不过（硬编码路径）、一个有实际危害的进程强杀逻辑（误杀 electron.exe）、文档与代码漂移。

---

## 3. 实测结果

### 3.1 测试套件（Python 3.11.9，本机实测）

```
共 21 套：通过 20，失败 1，用时 26.3s
[FAIL] test_tray.py  —— 开机自启注册表写入断言
```

- **唯一失败是环境问题，不是代码 bug**：`test_tray.py` 第 15 行断言 HKCU `Software\Microsoft\Windows\CurrentVersion\Run` 写入后可读回；本审查沙箱对注册表写入返回 `PermissionError [WinError 5]`（已单独探测确认）。在作者本机与 GitHub windows-latest runner 上该测试应当通过。
- **但测试本身有一个健壮性缺陷**：它直接断言注册表可写，没有「受限环境则 skip」分支。在 CI 之外任何受限上下文（沙箱、无管理员、部分企业策略）都会红。建议：`set_autostart(True)` 返回 `(False, err)` 时 `print('SKIP')` 并退出 0（或抛 `unittest.SkipTest` 语义）。
- `test_cdp_inject_endtoend.py` 的迷你 CDP 服务（手写 WebSocket 帧编解码）设计精巧，能在无 DSH 环境下验证完整注入链路，值得肯定。

### 3.2 静态检查

- 全部 39 个 Python 文件 `py_compile` 通过，0 语法问题。
- 无 lint 配置（没有 ruff/flake8/mypy 配置文件，CI 也没有 lint 步骤）。

---

## 4. 安全问题逐项审查（结论：做得好的 + 需要修的）

### 4.1 已做对的（值得保留）

| 项 | 位置 | 评价 |
|---|---|---|
| 随机访问令牌 + 常量时间比较 | `server.py: _load_or_create_token / _token_ok`（`secrets.compare_digest`） | 正确实现；token 落 `server.token` 并被 .gitignore 排除 |
| Origin/Referer 本机校验 | `Handler._origin_ok` | 对状态变更请求拦截非本机来源，403 记日志 |
| 敏感端点分级 | `SENSITIVE_GET` 集合 + POST 全量 token | 分级合理（sessions/providers/enhance 等敏感读也需 token） |
| 日志脱敏 | `redact_secrets` 4 条正则（sk-*/bearer/api_key/DEEPSEEK_API_KEY） | 落盘前统一过一遍，测试覆盖 |
| zip-bomb 防护 | `plugin_manager._check_zip_bomb`（200MB 解压 / 50MB 单文件 / 4000 成员 / 压缩比 100） | 有专门测试（test_extract_safety） |
| 路径穿越防护 | `_safe_join`、`validate_protocol` 的 `os.path.normpath` + `startswith` 校验 | 补丁文件/client 入口都校验不出包根 |
| 原子写 | 全部关键写走 `tmp + os.replace` | registry/config/patch 一致 |
| 脚本沙箱 + 权限分析 | `enhance_engine.analyze_script` 静态分析（DOM/cookie/网络特征）→ 面板两段式确认（`need_confirm`） | 市场安装前强制展示风险与权限；`new Function` 包裹隔离异常 |
| 插件协议校验 | `validate_protocol`：npm name 正则、dsh.bundle.patch 存在且为 Loader 行数组、client platform 仅 web | 不合规则拒绝导入 |
| 会话删除先备份 | `session_store.delete_sessions` 物理删除前备份，单次上限 50 | 有测试 |

### 4.2 需要修的问题（按严重度）

**【高】`dsh_env._terminate_app()` 会误杀机器上所有 `electron.exe` 进程**

```python
COMMON_EXE_IMAGES = ('DSH Desktop.exe', 'DeepSeek Harness.exe', 'electron.exe')
# _terminate_app():
subprocess.run(['taskkill', '/IM', img, '/T', '/F'], ...)   # 对 electron.exe 也执行
```

- `taskkill /IM electron.exe /T /F` 是**按映像名全局强杀**：任何以 `electron.exe` 名义运行的进程（dev 模式的 Electron 应用、其它未改名的 Electron 产品）都会被连带结束。触发路径：面板「重启 DeepSeek Harness」→ `restart_dsh()` → `relaunch_with_debug(force_restart=True)` → 当 DSH 在无调试端口运行时 → `_terminate_app()`。
- 同理 `app_process_running()` 用 `electron.exe` 做存在性探测：只要机器上跑着**任意** Electron dev 应用，DSH++ 的状态面板就会误报「DSH 在运行」。
- 建议：
  1. 用命令行/主窗口标题/可执行路径过滤——例如 PowerShell `Get-CimInstance Win32_Process` 按 `CommandLine like '%deepseek%'` 或 `ExecutablePath` 命中 harness 目录后再 `taskkill /PID`；
  2. 或至少把 `electron.exe` 从「可杀清单」移除，只对带明确产品名的两个 exe 强杀；dev 模式重启改为先 `pnpm --dir <harness> stop` 类温和手段；
  3. 探测侧（`is_running`）同样按路径/命令行判定，避免误报。
- 这是本次审查发现的**唯一可能造成用户数据/工作丢失的缺陷**（强杀 dev 应用 = 未保存工作丢失），建议最高优先级处理。

**【高】CI 在当前状态下必然红：3 个测试硬编码本机绝对路径**

```python
# tests/test_cdp_inject_endtoend.py:13、test_new_shell_reads.py:3、test_new_shell_targets.py:4
sys.path.insert(0, 'D:/DSH/DSH++')
```

- 其余 18 套测试都用了可移植写法 `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))`，唯独这 3 个写死 `D:/DSH/DSH++`。在 GitHub runner（`D:\a\dsh++\dsh++` 之类）上 `import cdp_skin` 直接 ModuleNotFoundError。
- 修复：改成与其它测试一致的可移植写法（3 行改动）。
- 附带：`test_cdp_inject_endtoend.py` 固定占用 19222/19223 端口，CI 并行下有小概率撞端口，可改 SO_REUSEADDR+随机端口（当前已设 REUSEADDR，风险低）。

**【中】`Access-Control-Allow-Origin: *`**

- `Handler._cors()` 对全部响应（含 401/403 错误）回 `*`。配合 token 机制实际风险可控（无 token 拿不到数据），但敏感端点建议：对 `/api/sessions*`、`/api/providers` 等只回显实际请求 Origin（本机来源才回），`*` 仅留给纯静态资源。
- 令牌同时接受 `?token=` query 传参（`_presented_token`）——query 里的 token 会进 access log/代理日志，建议仅保留 `X-DSHSkin-Token` 头，query 仅作浏览器页面内 fetch 的兼容通道并在日志中剔除。

**【中】`taskkill` 之外的子进程面**

- `plugin_manager._run_pnpm` / `install_bundle_npm` 会真实执行 `pnpm add`（超时 900s）拉取 npm registry 包——这是供应链信任点。现有缓解：A 轨要求「spec 能解析且 registry 上确实存在」、`--ignore-scripts`（好）。建议补充：安装后对落盘 bundle 做一次 `validate_protocol` 复验 + 面板展示包名/版本/依赖清单（部分已做），并把 GitHub spec（`install_from_github`）的 tarball 校验（至少记录 commit sha）落日志，便于事后审计。

**【低】托盘自启命令行**

- `tray.autostart_command()` 源码形态写 `pythonw.exe server.py --no-open`：若项目目录日后移动，Run 键里是绝对路径，自启会静默失效（无检测）。可在托盘菜单加「自启状态」刷新时校验路径存在。

---

## 5. 工程/维护性问题清单

### 5.1 版本控制缺失（最高优先治理项）

- **目录里有 `.gitignore`（36 行，写得规范）和 `.github/workflows/tests.yml`，但没有 `.git`**——不是 Git 仓库。
- 后果：无历史、无回滚、无 diff、CI 永远没跑过（workflow 存在但无触发源）。`_backup/`、`probe/rollback-snapshot-*`、`dshpp/snapshots/` 这些手工快照目录的存在，侧面说明团队目前靠手工备份代替版本控制。
- 建议：`git init` → 用现有 `.gitignore` 直接提交 → 推送远端 → CI 自然激活。注意首次提交前清理 §5.3 的垃圾产物，避免把 100MB+ 构建产物/日志推进历史。

### 5.2 代码结构

| 问题 | 说明 | 建议 |
|---|---|---|
| `server.py` 2206 行单文件 | `Handler` 类内 ~65 条 `if path == ...` 路由链 + 5 个守护线程 + token/日志/漂移逻辑混排 | 拆 `routes/` 包（themes/sessions/plugins/enhance/diag 各一模块），`Handler` 只做分发 |
| `panel.html` 3620 行单文件 SPA | 10 分区全在一个文件，无构建、无组件边界 | 可接受（本地工具），但建议至少按分区拆 `<script type="module">` 文件，降低单文件 diff 成本 |
| `dsh-skin.py` 连字符文件名 | server 用 `importlib.util.spec_from_file_location` 加载（`server.py:72`），绕过了正常 import | 建议改名为 `dshskin.py`（保留 `dsh-skin.py` 做 3 行 shim 保兼容），消除 importlib workaround 与打包 hiddenimports 隐患 |
| 模块级 import 顺序 | `dsh-skin.py` 在 server.py import 期就执行 `ts.ensure_dirs()`，副作用发生在 import 时 | 可接受但需在文档中声明「import server 即初始化数据目录」 |
| 无 lint/format/type 门禁 | 代码风格靠自觉（实际风格相当一致：`.format()` 而非 f-string，统一 4 空格） | 加 `ruff`（lint+format）到 CI；`mypy --strict` 至少覆盖核心引擎 |
| 版本号漂移 | `__version__='1.0.0'` vs README 自称 v2.1；`updater.py` 用 `UPDATER_REPO=''`（未配置，更新检查是死代码） | 统一单一版本源（如 `dsh_env.__version__`）；发布前填 `UPDATER_REPO` 或删除模块 |

### 5.3 工作区卫生（当前树内 ~101MB 非源码产物）

| 目录/文件 | 大小 | 性质 |
|---|---|---|
| `dist/` + `build/build_desktop{,_onedir}/` | ~55MB×2 | PyInstaller 中间产物 + 已构建 exe（.gitignore 已排除，但物理存在） |
| `probe/rollback-snapshot-*/`、`dshpp/snapshots/`、`_test_market/`、`_backup/` | 若干 | 手工回滚快照/测试残留 |
| 根目录 `debug.log`、`_exe.err.log`、`_srv.err.log`、`_panel_regtest{,2}.log`、`tools/proc_trace.log`(39KB) 等 | ~45KB | 调试日志（.gitignore 排除 `*.log`） |
| `assets/inline-00..105.css`（109 个文件） | ~1.7MB | 捕获的 DSH 真实 CSS 快照，供漂移/选择器基线用——**应保留但建议在 README 说明用途** |
| `__pycache__/`、`C__Users_*` | — | 缓存（已 ignore） |

- 另注意 `.gitignore` 未排除 `probe/`、`dshpp/snapshots/`、`_test_market/`、`tools/*.log`（`*.log` 可覆盖）、`assets/`。首次 `git init` 前建议：删除 `build/`、`_backup/`、`probe/rollback-snapshot-*`、`_test_market/`，并在 .gitignore 补上 `dshpp/snapshots/`（快照应视为运行时产物）。

### 5.4 文档漂移

| 文档 | 状态 |
|---|---|
| `PROJECT_ANALYSIS.md` | 描述的是旧目录 `D:\DSH\DSH-skin`、旧规模（server 1352 行 vs 现 2206 行）、旧品牌（DSHSkin v2.0）。**已过期**，头部应加「历史快照」标注或重写 |
| `dsh-desktop-architecture-report.md` | 对 dsh-desktop monorepo 的只读调研，650 行，质量很高，仍准确（描述的是上游而非本仓库） |
| `DSHPP_NEW_SHELL_PLAN.md` / `DSHPP_PLUGINIZATION.md` | 新壳适配方案（P0 已完成、P1 进行中），`dshpp/` 目录是其落地产物；两者应标注各自 P 阶段完成状态 |
| `README.md` | 当前准确且详尽，是四份中维护最好的 |

- 建议：四份大文档在 README 加一个「文档地图」小节，标明 各自时效性（现行 / 历史 / 方案稿）。

### 5.5 测试体系

- **框架**：无 unittest/pytest，纯 `assert` + `print` + 子进程隔离（`tests/run_all.py`）。优点是零依赖、隔离干净；缺点是无 per-test 粒度报告、无 fixture、无参数化。规模已 21 套，建议迁移 pytest（`run_all.py` 可保留为 wrapper 兼容 CI）。
- **输出编码**：测试 print 中文在 PowerShell 下显示为 GBK 乱码（CI 上会正常，本地观感差）。建议测试输出统一 `sys.stdout.reconfigure(encoding='utf-8')`。
- **环境耦合**：除 §4.2 的路径问题外，`test_packaged_resolution.py`/`test_config.py` 会真实读写 `~/.dsh-skins`（test_config 耗时 12.6s，含真实 CDP 探测）。CI 上无 DSH 环境，依赖这些模块的降级分支——目前通过，但建议在 CI job 里显式声明「无 DSH 依赖」以防未来误加真实依赖。
- **JS 侧零测试**：`enhance-modules/`、`market/`、`dshpp/bundle/` 的 JS 无 lint 无测试。`enhance_engine.analyze_script` 的 Python 侧静态分析有部分覆盖，但浏览器端行为（hotkey、DOM 注入）只能靠 e2e 手工验证。

---

## 6. 依赖与构建

| 依赖 | 版本策略 | 评价 |
|---|---|---|
| websocket-client ≥1.6.0 | 核心 | 缺失时仅降级注入，合理 |
| zstandard ≥0.22.0 / PyYAML ≥6.0 | 会话/凭证解析 | 降级合理 |
| pystray ≥0.19.5 / Pillow ≥10.0.0 | 托盘（可选） | 降级为控制台形态，合理 |
| pywebview | **未列入 requirements.txt**，靠 `find_spec('webview')` 探测 | `desktop_app.py` 首选窗口后端，但用户按 README 装依赖后仍没有 pywebview → 桌面 exe 实际走 Edge --app 兜底。应明确：要么列入（分可选段），要么在 README「桌面应用」节说明默认走 Edge 窗口 |
| Node ≥22 / pnpm 11 | 拉起 DSH / A 轨插件安装 | `dsh_env.resolve_node/resolve_pnpm` 有完整解析链 + 修复逻辑（corepack），README 已写明 Node20 坑，好 |

- **打包**：三份 spec（`build_desktop.spec` onefile、`build_desktop_onedir.spec`、遗留 `DSHSkin.spec`）。onefile 产物 `DSH++.exe` 与源码共用 `~/.dsh-skins` 数据，设计合理；但 `build_desktop.spec` 的 `datas` 把 `tools/`（含 proc_trace.log 等调试物）一起打进了 exe——建议 datas 白名单化。
- **PyInstaller + onefile + 中文 exe 名（DSH++.exe）**：`+` 号在文件名中合法但部分老脚本/批处理会踩坑，现有 .bat 都带引号处理，可接受。

---

## 7. 架构亮点与风险点小结

**亮点**

1. 「受管块 + .orig + 幂等重写」的插件装配层设计（`plugin_manager.py`）：对 `cordis.patch.yml` 的写入幂等、可还原、保留用户自有行、YAML 不合法拒写——这是全仓库设计最完整的部分，且有 12 个专门测试。
2. CDP 多 target 会话（`cdp_skin.TargetSessions`）：主页面/对话框/devtools 正确区分，`addScriptToEvaluateOnNewDocument` 自举脚本保证刷新后皮肤常驻。
3. 漂移检测（`server.py: evaluate_drift` + 基线指纹）：DSH 升级后选择器失配能被 120s 巡检发现并提示重建，把「上游升级打脸」变成可运维事件。
4. 双轨插件安装把「官方 pnpm 事务」与「file:// 持久装配」的边界、冲突（运行时禁装/卸载防 profile 锁）都处理了，注释里保留了 P0 实证结论（boot 期 reconcile 会清依赖），决策可追溯。

**风险点**

1. **进程强杀**（§4.2 高）——唯一可能伤害用户其它工作的缺陷。
2. **无 Git**——所有「可逆」设计都建立在文件层面；一旦误删源码（无备份根目录外副本），`.orig`/snapshot 机制救不了仓库本身。
3. **单文件大对象**（server.py / panel.html）——后续每加一个分区/路由都是 2000+ 行文件内改动，review 成本上升。
4. **Windows-only 深度耦合**（winreg、taskkill、tasklist、.bat、PowerShell 探测链）——设计如此，但 `is_autostart` 等已有 `sys.platform` 守卫，迁移/测试到其它环境时这些分支覆盖不到。

---

## 8. 建议行动清单（按优先级）

| # | 行动 | 工作量 | 收益 |
|---|---|---|---|
| 1 | 修 `_terminate_app`/`is_running`：按 ExecutablePath/命令行过滤，不再按 `electron.exe` 映像名全局 taskkill | 半天 | 消除误杀用户其它 Electron 应用的风险 |
| 2 | `git init` + 清理工作区产物 + 推送远端 | 1-2 小时 | 获得版本控制/回滚/CI 激活 |
| 3 | 修 3 个测试的硬编码 `D:/DSH/DSH++` → 可移植路径 | 5 分钟 | CI 真正可跑 |
| 4 | `test_tray.py` 加注册表不可写时的 SKIP 分支 | 10 分钟 | 受限环境不再假红 |
| 5 | 加 ruff 到 CI（lint + format），逐步接入 mypy | 半天 | 风格/类型门禁 |
| 6 | pywebview 列入 requirements（可选段）或 README 说明桌面窗口默认形态 | 10 分钟 | 用户预期一致 |
| 7 | 统一版本号（单一 `__version__` 源）；决定 updater 去留 | 30 分钟 | 消除 1.0.0 vs v2.1 歧义 |
| 8 | `server.py` 路由拆分（routes/ 包） | 1-2 天 | 可维护性 |
| 9 | 四份大文档加时效标注 / PROJECT_ANALYSIS 重写或归档 | 半天 | 文档可信度 |
| 10 | `dsh-skin.py` → `dshskin.py`（shim 保兼容）+ build spec datas 白名单 | 半天 | 消除 importlib workaround 与打包杂质 |
| 11 | 测试迁移 pytest（可选，`run_all.py` 保留 wrapper） | 1 天 | 报告粒度/fixture |
| 12 | 敏感端点 CORS 收紧为回显 Origin；query token 从日志剔除 | 2 小时 | 纵深防御再加固 |

---

## 9. 附录：审查方法与证据

- 目录/文件规模：`Get-ChildItem -Recurse` 全量统计（排除 node_modules/__pycache__/build 中间层）
- 代码通读：server.py（全部 2206 行分段）、plugin_manager.py、dsh_env.py、cdp_skin.py、enhance_engine.py、session_store.py、theme_engine.py、desktop_app.py、tray.py、updater.py、dsh-skin.py、dshpp/install.mjs + bundle/lib/index.js（头部）、panel.html（结构）、3 份 docs、4 份大文档头部
- 实测：`python tests/run_all.py`（Python 3.11.9）21 套 20 过 1 败；`py_compile` 39 文件 0 错；注册表写探测（PermissionError 确认 test_tray 失败为环境性）
- 交叉验证：`.gitignore` / `.github/workflows/tests.yml` / 三份 spec / requirements.txt / 测试文件 `sys.path` 写法比对
