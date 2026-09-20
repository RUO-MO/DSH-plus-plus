# DSH++ 全项目审查报告（2026-09-20）

> 审查方式：**逐文件通读源码**（非依赖 README/旧报告）+ 隔离环境实跑测试套件 + 静态扫描。
> 审查范围：`D:\DSH\DSH++` 全仓库 —— 39 个 Python 文件（12,081 行）、17 个 JS/MJS、`panel.html`（4,628 行）、4 份大文档 + 3 份 docs、21 套测试、2 个内置主题。
> 与既有文档的关系：本项目已有 `REVIEW_REPORT.md`（09-17）与 `PROJECT_ANALYSIS.md`（已过期）。**本报告基于当前代码重新核验**，并标出哪些旧结论已修复、哪些仍存在、哪些是新发现。

---

## 1. 结论速览

**总体判断：工程质量依然很高，架构纪律（零侵入 / CDP-only / 完全可逆）在代码中被完整贯彻。** 但与 09-17 那份审查相比，当前代码**引入了 1 处真实回归缺陷**，并且我发现了 **3 个此前未被记录的实质性问题**。

| 类别 | 数量 | 说明 |
|---|---|---|
| 🔴 真实缺陷（需修） | 4 | 测试实红 1 处 + 前端导航错位 + 市场脚本快捷键失控 + 版本号三处不一致 |
| 🟡 架构/维护性问题 | 6 | 大文件、无 Git、文档漂移、依赖声明缺口等 |
| 🟢 可新增功能（按性价比排序） | 9 | 见 §5 |

**先说好消息**：09-17 审查指出的两个高危问题**已经修复**：

1. ✅ `electron.exe` 全局误杀已修 —— `dsh_env.py:74` 的 `COMMON_EXE_IMAGES` 现在只含具名产品 exe（`DSH Desktop.exe` / `DeepSeek Harness.exe`），开发版改走 `_electron_dsh_pids()`（`dsh_env.py:415`），用 PowerShell `Win32_Process.CommandLine` 过滤 `deepseek` / `dsh-desktop` / `--remote-debugging-port` 特征后才按 PID 杀。**不再误杀 VS Code 等其它 Electron 应用。**
2. ✅ 测试硬编码路径已修 —— `test_cdp_inject_endtoend.py` / `test_new_shell_*.py` 现已统一用 `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` 的可移植写法。
3. ✅ 工作区已清理干净 —— 根目录无 `debug.log` / `_exe.err.log` / `_backup/` / `_test_market/` 等残留（旧报告提到的 ~101MB 垃圾产物已不存在），`.gitignore` 也补齐了 `_work/` 等条目。

---

## 2. 实测结果

### 2.1 语法与静态检查

```
py_compile 全量扫描（排除 build/__pycache__/dist）：39 个 .py，0 语法错误
代码中 TODO / FIXME / XXX / HACK：0 处（无技术债标记，注释以设计说明为主）
```

### 2.2 测试套件（隔离 venv，Python 3.13.14 + 完整依赖）

依赖必须装齐才能跑——**这是第一个值得注意的点**（见 §3.1）：

| 环境 | 结果 |
|---|---|
| 仅系统 Python（无依赖） | 通过 **10** / 失败 **11**（全部是 `ModuleNotFoundError: No module named 'yaml'` 连带 `server.py` 无法 import） |
| 隔离 venv 装齐 `websocket-client zstandard PyYAML` | 通过 **20** / 失败 **1**，用时 37.1s |

**唯一失败：`test_packaged_resolution.py` —— 且这是真缺陷，不是环境问题。**

```
1) packaged 插件路径解析: PASS
2) dev 模式回归路径: PASS
3) 组合根注释保留 + 托管块写入: PASS
ACTUAL   {"provider": "localqwen", "model": "qwen", "reasoningEffort": null}
EXPECTED {'provider': 'localqwen', 'model': 'qwen'}
AssertionError: tests/test_packaged_resolution.py:69
```

根因定位（已用最小复现确认）：`session_store.py:1060-1063` 的 `providers_info()` 在组装 `default_model` 时**无条件塞入 `reasoningEffort` 字段**：

```python
if isinstance(dm, dict) and dm.get('provider'):
    info['default_model'] = {'provider': dm.get('provider'),
                             'model': dm.get('model') or '',
                             'reasoningEffort': dm.get('reasoningEffort')}   # ← 恒存在（值可能为 None）
```

- 当 `settings.yaml` 的 `agent-default-model` 节**没写** `reasoningEffort`（测试用例与多数真实配置即如此），解析结果的字典就多出一个 `reasoningEffort: None`。
- 这不是 yaml 分支独有：`_parse_llm_pi_ai_mini()`（PyYAML 缺失时的降级文本解析）同样返回 `{provider, model}` 两键 —— **两条解析路径的返回结构不一致**，且都与测试断言不符。
- **影响**：面板「供应商配置 / 模型」页读取 `default_model`；字段结构不稳定会让前端取值逻辑出现 `undefined` 分支（当前前端容错所以没炸，但契约已经破了）。CI（`.github/workflows/tests.yml`）会**稳定红**。
- **修法（二选一，推荐 A）**：
  - A. 只在存在时带上该键：`d = {'provider':…, 'model':…}; if dm.get('reasoningEffort'): d['reasoningEffort'] = …`
  - B. 若前端确实依赖该键恒存在，则改测试断言为 `{…, 'reasoningEffort': None}`，并同步让 `_parse_llm_pi_ai_mini` 也补该键，**两条路径结构对齐**。

### 2.3 依赖声明的实际断层

`requirements.txt` 把 `PyYAML` 归入「缺失时降级」的可选层，但 `plugin_manager.py:41` 是**模块级硬 `import yaml`**：

```python
import yaml          # plugin_manager.py:41 —— 无 try/except
```

而 `server.py:60` `import plugin_manager`、`dsh-skin.py` 亦依赖它。结果是：**没装 PyYAML 时，后端整个起不来**（11 套测试连带失败），而不是 README 承诺的「对应功能降级、不崩溃」。

> 这与 README「依赖分层，缺非核心依赖时对应功能降级、不崩溃」（README:50）和 §依赖表把 PyYAML 列为非核心的描述**直接矛盾**。要么把 PyYAML 提到「核心」并修正文档，要么给 `plugin_manager` 的 yaml 使用加降级分支。**建议后者**（与 `session_store` 已有的 `_parse_llm_pi_ai_mini` 降级思路一致）。

---

## 3. 真实缺陷清单（按严重度）

### 🔴 D1. `panel.html` 分区快捷键顺序与侧栏导航错位

侧栏共 **12** 个导航项，但快捷键表 `VIEW_ORDER` 只有 **10** 项且顺序不含 `tu` / `plugins`：

```js
// panel.html:4588
const VIEW_ORDER = ['home','themes','sessions','prov','models','enh','market','diag','logs','settings'];
//                                        ↑ 跳过了 tu        ↑ 跳过了 plugins
```

实际侧栏顺序是：
`home, themes, sessions, prov, models, tu, enh, plugins, market, diag, logs, settings`

**后果**：从 `Ctrl+6` 起全部按错——
- `Ctrl+6` 用户以为选「令牌用量(tu)」，实际打开「DSH 增强(enh)」
- `Ctrl+7` 以为选 enh，实际打开 market
- 依此类推，`tu` 与 `plugins` 两个分区**完全没有键盘入口**。

而 `switchView()` 自身的视图列表（`panel.html:2481`）是**正确的 12 项**，说明是 `VIEW_ORDER` 漏更新。这是新增 `tu` / `plugins` 分区时的遗留。

**修法**：把 `VIEW_ORDER` 与侧栏顺序对齐（12 项），并把 `n <= VIEW_ORDER.length` 的越界上限同步（若要保留 Ctrl+1..9 只到 9，则需明确取舍并写入提示）。

### 🔴 D2. 脚本市场的 7 个脚本全部硬编码快捷键，绕过了快捷键注册表

`enhance_engine.py:105-111` 建立了统一的 `HOTKEYS` 注册表，并提供 `resolve_hotkeys()` / `detect_hotkey_conflicts()`，面板也有完整的改键 UI（`panel.html:1455`「快捷键」卡片 + `enhSaveHotkeys()`）。内置模块**都正确接入了**：

```
input-plus.js:257   var _hk = (R.config && R.config.hotkeys) || {};
session-tools.js:144 var HK = (R.config && R.config.hotkeys) || {};
ui-tweaks.js:143     var HK_TOGGLE = (R.config.hotkeys['ui-tweaks.toggle']) || 'Ctrl+Alt+U';
```

但 `market/*.js` **7 个脚本无一接入**（`grep -l "hotkeys" market/*.js` 返回空），全部写死：

| 脚本 | 硬编码键 |
|---|---|
| `wide-screen.js` | `Ctrl+Alt+W` |
| `prompt-library.js` | `Ctrl+Alt+L` |
| `focus-mode.js` | `Ctrl+Alt+F` |
| `copy-code-button.js` | `Ctrl+Shift+C` |

**后果**：
1. 用户在面板里改键 → 这些脚本毫无反应（用户会认为是 bug）。
2. `HOTKEYS` 注册表未登记它们 → `detect_hotkey_conflicts()` **检测不到**与用户脚本/市场脚本的冲突。
3. 面板「快捷键」列表不显示它们，用户无从知道这些键已被占用。

**修法**：把 `HOTKEYS` 扩成「内置 + 市场」统一注册源（市场脚本 front-matter 增加 `@hotkey` 字段，`server.py` 的 `_FM_RE` 已具备解析 `// @key: value` 的能力，扩展成本低），市场脚本改从 `R.config.hotkeys` 读取。

### 🔴 D3. 版本号三处不一致，且 `updater` 是死代码

| 位置 | 值 |
|---|---|
| `dsh-skin.py:46` `__version__` | `'1.0.0'` |
| `README.md:7` | 「v2.1」 |
| `updater.py:17` `UPDATER_REPO` | `''`（空 → `check_for_update` 恒返回 `configured=False`） |

- 面板「关于」显示的是 `ts.__version__`（即 `1.0.0`），与 README 自称 v2.1 矛盾。
- `updater.py` 整个模块在未配置 `DSH_SKIN_REPO` 时是死路径；`/api/update-check`（`server.py:1254`）会返回「未配置更新源」。**要么填仓库地址激活它，要么删除模块**（连同测试），不要留半成品。

### 🟡 D4. `default_model` 结构契约不稳定（同 §2.2，已在那里详述）

两条解析路径（yaml / mini 文本）返回的键集不同，是 D1 之外的第二个「前后端契约漂移」。

---

## 4. 工程 / 维护性问题

### 4.1 不是 Git 仓库（最高优先治理项）

`ls -d .git` → **不存在**。但 `.gitignore`（36 行，写得很规范）与 `.github/workflows/tests.yml`（windows-latest + Python 3.11/3.13 矩阵）都在。

**后果**：无历史、无回滚、无 diff、CI 从未真正触发过。而整个项目最引以为豪的「可逆性」（`.orig` 备份 / 受管块 / `restore_managed`）**只保护 DSH 的数据，不保护 DSH++ 自己的源码**——一次误删就没了。

**行动**：`git init` → 提交 → 推远端 → CI 自动激活。注意 `dist/`（含 `DSH++.exe`）与 `build/` 已正确 ignore。

### 4.2 单文件巨型对象

| 文件 | 行数 | 问题 |
|---|---|---|
| `server.py` | 2,379 | ~79 条 `if path == '/api/...'` 平铺路由 + 5 个守护线程 + token/日志/漂移逻辑混排 |
| `plugin_manager.py` | 1,807 | 双轨插件 + 市场状态对齐 + 补丁层手术，认知负荷高 |
| `panel.html` | 4,628 | 12 个分区 + 165 个函数全在一个 `<style>`+`<script>` 里（1 个 `<script>` 块从 1653 行到 4626 行） |

每新增一个分区/路由都要在 2000+ 行文件里改。建议：`server.py` 拆 `routes/` 包（Handler 只做分发）；`panel.html` 至少按分区拆成 `<script type="module">`。

**附带清理项**：`panel.html` 有 **176 处** `data-page-node-id="…"`、`preview.html` 有 **36 处**——这是早期 Trae-SOLO 代码生成器的残留属性，无任何功能作用，删除可显著瘦身两个 HTML。

### 4.3 文档漂移

| 文档 | 状态 |
|---|---|
| `PROJECT_ANALYSIS.md` | **已过期**：描述旧目录 `D:\DSH\DSH-skin`、旧规模（server 1352 行 vs 现 2379）、旧品牌 DSHSkin v2.0。应加「历史快照」标注或重写 |
| `dsh-desktop-architecture-report.md` | 描述上游 dsh-desktop（非本仓库），内容仍准确 |
| `DSHPP_NEW_SHELL_PLAN.md` / `DSHPP_PLUGINIZATION.md` | 方案稿，应标注各 P 阶段完成状态 |
| `README.md` | 维护最好，但 §2.3 的依赖分层描述与代码不符 |

建议在 README 加「文档地图」小节，标明每份文档的时效性（现行 / 历史 / 方案稿）。

### 4.4 前端文档与实现的参数对齐

`theme_engine.PARAM_SCHEMA` 有 9 个参数，`panel.html:1670` 的 `PARAM_META` **也已补齐 9 个**（含 `font_scale` / `force_dark`）——这一项**旧报告提的问题已修复**。✅

### 4.5 其它

- **无 lint / format / type 门禁**：CI 只跑测试。加 `ruff`（lint+format）成本极低、收益明确。
- **`dsh-skin.py` 连字符文件名**：`server.py:72` 用 `importlib.util.spec_from_file_location('dsh_skin_main', …)` 绕过 import。可接受，但打包 hiddenimports 有隐患。
- **测试框架**：纯 `assert` + `print` + 子进程隔离（`tests/run_all.py`），零依赖是优点；但无 per-test 报告 / fixture / 参数化，21 套已到该迁移 pytest 的规模。
- **JS 侧零测试**：`enhance-modules/` / `market/` / `dshpp/bundle/` 无 lint 无测试，行为只能靠手工 e2e 验证。
- **`dshpp/` 的 `snapshots/` 目录**：README 提到会产生快照，`.gitignore` 未排除 `dshpp/snapshots/`，首次 `git init` 前应补上。

---

## 5. 建议新增的功能（按性价比排序）

### P0 —— 低成本、立刻消除痛点

**F1. 配置导入 / 导出（一键迁移换机）**
现在主题、增强开关、热键、市场脚本分散在 `~/.dsh-skins/` 多个文件里。做一个「导出全部设置 → 单个 zip」/「导入恢复」，换机 / 重装零成本。工作量小，复用现成的 zip 安全校验（zip-slip / 炸弹防护）即可。

**F2. 主题可视化编辑器（实时预览调参）**
`preview.html` 已经能做到「真实快照 + postMessage 同步参数」。把「参数滑块 + 实时预览 iframe」做成一个编辑视图，用户拖滑块就能看到真实效果，而不是盲调数字。**这是当前体验最缺失的一环**，且基础设施（preview + `theme_engine`）都已就绪。

**F3. 主题配色方案自动提取**
导入带背景图的主题包时，自动从图片提取主色调，填入 `glow_color` / `overlay_color` 作为默认值。Pillow 已是可选依赖，可行性高。

### P1 —— 明确提升能力边界

**F4. 会话全文搜索（跨会话 / 正则）**
现在只有 `/api/sessions-search`。升级为：跨会话全文搜索 + 高亮命中 + 正则支持 + 搜索结果直接跳转。会话数据已能解析（`session_store`），纯增量功能。

**F5. 会话统计仪表盘**
已有 `/api/usage-stats` 与 token-usage 页。扩展成：按项目 / 时间维度的会话数、消息数、token 趋势图，找出「哪个项目最耗 token」。

**F6. 增强模块商店化（用户可分享自定义模块）**
`enhance_engine.analyze_script` 已具备权限静态分析。加「导出我的脚本为可分享包（含权限声明）」+「从文件/链接安装」，让用户脚本也能流通。注意必须复用现有的两段式风险确认。

**F7. 主题包校验增强 + 主题签名**
现在的 zip 校验覆盖穿越/炸弹/魔数。可加：SHA256 摘要 + 可选签名，防止主题包被中间篡改。

### P2 —— 打磨型

**F8. 深色模式跟随系统**
`panel.html` 目前靠 `localStorage` 手选明暗。加 `prefers-color-scheme` 自动跟随。

**F9. 托盘菜单增强**
`tray.py` 已支持「打开面板 / 立即注入 / 还原 / 自启 / 退出」。可加：当前主题名与注入状态显示、快速切主题子菜单。

---

## 6. 行动清单（按优先级）

| # | 行动 | 影响 | 工作量 |
|---|---|---|---|
| 1 | 修 `session_store.providers_info` 的 `default_model` 结构（或对齐测试）→ CI 转绿 | 阻断 CI | 10 分钟 |
| 2 | 修 `panel.html` `VIEW_ORDER`（对齐 12 项侧栏顺序） | 6 个快捷键按错 | 5 分钟 |
| 3 | `plugin_manager` 的 `import yaml` 加降级分支，或把 PyYAML 提为核心依赖并改文档 | 缺依赖即崩 | 30 分钟 |
| 4 | `git init` + 推远端 → CI 激活 | 治理/回滚 | 1 小时 |
| 5 | 市场脚本接入 `HOTKEYS` 注册表（front-matter 加 `@hotkey`） | 改键失效 / 冲突检测漏报 | 半天 |
| 6 | 统一版本号（单一 `__version__` 源）；决定 `updater` 去留 | 消除 1.0.0 vs v2.1 歧义 | 30 分钟 |
| 7 | 删除 `panel.html`/`preview.html` 的 212 处 `data-page-node-id` | 瘦身 + 可读性 | 20 分钟 |
| 8 | 加 `ruff` 到 CI（lint + format） | 风格门禁 | 半天 |
| 9 | `server.py` 拆 `routes/` 包 | 可维护性 | 1–2 天 |
| 10 | 实现 F1 配置导入导出 / F2 主题可视化编辑器 | 体验质变 | 各 1 天 |
| 11 | `PROJECT_ANALYSIS.md` 加「历史快照」标注 / README 加文档地图 | 文档可信度 | 半小时 |
| 12 | 测试迁移 pytest（`run_all.py` 保 wrapper） | 报告粒度 | 1 天 |

---

## 7. 附录：审查证据

- **目录/规模统计**：`find` + `wc -l` 全量扫描（排除 build/__pycache__/dist/node_modules）
- **代码通读**：`dsh_env.py`（重点核验进程强杀/探测逻辑）、`server.py`（路由表全览 79 条）、`plugin_manager.py`（函数清单）、`session_store.py`（providers 解析全文）、`enhance_engine.py`（MODULES/HOTKEYS/analyze_script）、`theme_engine.py`（PARAM_SCHEMA）、`panel.html`（视图/导航/参数/快捷键/初始化）、`enhance-modules/*.js`、`market/*.js`（front-matter 全量）、`tests/test_packaged_resolution.py`、`.gitignore`、`.github/workflows/tests.yml`
- **实测**：
  - `py_compile` 39 文件 → 0 错
  - 无依赖环境 `tests/run_all.py` → 10 过 11 败（yaml 缺失）
  - 隔离 venv（Python 3.13.14 + websocket-client / zstandard / PyYAML 6.0.3）→ **20 过 1 败**，37.1s
  - 失败用例最小复现：独立脚本确认 `default_model` 实返 `{'provider','model','reasoningEffort'}` vs 断言 `{'provider','model'}`
  - 导航/快捷键错位：脚本比对侧栏 12 项 vs `VIEW_ORDER` 10 项
  - 快捷键注册表接入面：`grep -l "hotkeys" market/*.js` → 空（全部未接入）
