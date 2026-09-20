# DSHSkin 主题作者指南

本指南说明如何制作一个可分发的 DSHSkin 主题包（`.zip`）。读完你能：从零做一个主题、
正确声明明暗形态、使用可调参数与背景图，并通过校验、随项目分发或分享给他人。

> 运行机制：DSHSkin **不修改** DeepSeek Harness 任何文件。主题 CSS 在运行时经 CDP 注入，
> 选择器只使用 DSH 稳定的 `data-*` 契约（不使用 CSS Modules 随机类名），因此官方升级后
> 主题通常仍能自愈生效。

---

## 1. 主题包结构

一个主题包是一个 zip，至少包含 `meta.json` 与 `skin.css`，背景图可选：

```
my-theme.zip
├── meta.json        # 主题元信息（必需）
├── skin.css         # 主题样式模板（必需，支持参数占位）
└── background.png   # 背景图（可选，jpg/png/webp 均可，约定文件名 background.*）
```

- 三个文件放在 zip **根目录**（不要再套一层同名文件夹）。
- `skin.css` 由 `theme_engine` 结合参数渲染成最终 CSS；你也可以直接写死一份纯 CSS，
  不使用任何占位符。

### meta.json 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 主题唯一 id，小写字母/数字/`-`/`_`，与 zip 文件名一致最稳妥 |
| `name` | 是 | 展示名，如 `"墨夜 Inkwell"` |
| `version` | 是 | 语义化版本，如 `"1.0.0"`；内置主题版本升高时面板会静默升级 |
| `appearance` | 是 | 明暗形态：`light`（仅浅色）/ `dark`（仅暗色）/ `both`（明暗双变体，跟随系统） |
| `description` | 否 | 一句话简介 |
| `params` | 否 | 默认参数对象，键见第 3 节；缺省用引擎默认值 |
| `builtin` | 否 | 随程序内置才标 `true`，第三方主题不要写 |

最小示例（无背景图的纯暗色主题）：

```json
{
  "id": "inkwell",
  "name": "墨夜 Inkwell",
  "version": "1.0.0",
  "appearance": "dark",
  "description": "无背景图的纯暗色玻璃主题",
  "params": { "glow_color": "#3ee0c4", "glass_opacity": 68, "blur_strength": 18 }
}
```

---

## 2. 明暗形态 `appearance`（重要）

DSH 自身通过 `<body data-ds-dark-theme>` 标记明暗。主题有三种形态：

- **`both`（推荐）**：模板同时输出 `:root`（亮）与 `body[data-ds-dark-theme]`（暗）两套
  token，**自动跟随 DSH 当前明暗**切换，用户在 DSH 里切深浅色主题会一起变。内置 keus 即此形态。
- **`dark`**：固定暗色观感（适合无图深色主题，如 inkwell）。
- **`light`**：固定浅色观感。

> `params.force_dark=true` 会强制走暗色单轨，优先级高于 `appearance=both`，一般不需要设置。

---

## 3. 可调参数（PARAM_SCHEMA）

`meta.params` 提供默认值，用户可在面板微调。引擎会对越界值自动钳制（norm_params），
所以作者不必担心用户填出非法值。共 9 项：

| 参数 | 类型 | 范围/默认 | 作用 |
|---|---|---|---|
| `glow_color` | 颜色 hex | 默认青蓝 | 输入条聚焦光晕颜色 |
| `glow_strength` | int | 0–100 | 光晕强度 |
| `glass_opacity` | int | 0–100 | 玻璃面板不透明度 |
| `blur_strength` | int | 0–40 | 背景模糊半径 px |
| `radius` | int | 0–28 | 圆角半径 px |
| `overlay_color` | 颜色 hex | — | 背景图上的叠层颜色（提亮/压暗） |
| `overlay_alpha` | int | 0–100 | 叠层不透明度 |
| `font_scale` | int | 85–130 | 正文字号百分比，100=默认 |
| `force_dark` | bool | false | 强制暗色 |

颜色支持 `#rgb`/`#rrggbb`，非法颜色回退默认，不报错。

---

## 4. 在 skin.css 里使用占位

`skin.css` 是“模板”。两种用法可以混用：

1. **完全交给引擎**：不写任何选择器，引擎用内置玻璃拟态模板结合你的 `params` 生成最终 CSS
   （把 `skin.css` 留空或只写注释即可，引擎会补全模板）。
2. **追加自定义 CSS**：在模板之外写你自己的覆盖规则，针对稳定的 `data-*` 区域。

### 背景图占位 `{{IMAGE}}`

若带背景图，在需要引用图片的地方写 `{{IMAGE}}`，安装时引擎会替换为 `url("background.png")`，
CDP 注入时替换为内嵌 data URI（规避 `file://` 的 CSP 限制）：

```css
body { background-image: {{IMAGE}}; background-size: cover; }
```

不带背景图时 `{{IMAGE}}` 会被替换为 `none`，主题退化为纯色，不会出现坏链。

### 应该针对哪些选择器

只使用 DSH 稳定契约，**不要**用 `KDVgQq_frame` 这类随机哈希类名：

- 区域由注入脚本统一打上 `data-dsh-skin="<region>"` 标记，主题针对这些标记写样式；
- 也可直接用 DSH 原生插槽：`[data-slot="..."]`、`[data-composer-card]`、`[data-shell-overlay]` 等。

> 注意：不要给左侧栏容器本身加 `backdrop-filter`——DSH 的设置/权限弹层挂在侧栏内部且为
> `fixed` 定位，backdrop-filter 会改变其包含块，导致居中弹窗被压成窄侧栏。玻璃模糊应作用在
> 具体面板/卡片上，而不是侧栏根容器。

---

## 5. 本地校验与安装

```bash
# 只校验不安装（强烈建议发布前跑一次）
python dsh-skin.py install my-theme.zip --dry-run

# 安装并启用
python dsh-skin.py install my-theme.zip

# 查看已装主题 / 重建当前主题 CSS（模板升级后用）
python dsh-skin.py list
python dsh-skin.py rebuild <theme-id>
```

校验内容：zip 合法性与大小、zip-slip 穿越、解压炸弹（总量/单文件/成员数/压缩比）、
meta 字段、`appearance` 合法性、必需文件是否齐全。

### 模板版本与自动重建

引擎带有 `TEMPLATE_VERSION`。当 DSHSkin 升级了内置模板，旧主题会被识别为“需要重建”，
面板/`rebuild` 会用新模板 + 主题原有参数重新生成，作者无需重新打包即可获得模板修复。

---

## 6. 作为内置主题随项目分发

把 `<id>/<id>.zip` 放进仓库 `themes/` 目录，并在 `meta.json` 标 `"builtin": true`：

- 全新用户首次启动时自动种子安装；
- 老用户启动时若仓库内 zip 版本号更高，会**静默升级**并保持其当前激活主题不变；
- 内置主题不应包含私有/版权素材（keus、inkwell 使用可分发素材）。

---

## 7. 自检清单

- [ ] zip 根目录直接是 `meta.json` / `skin.css`（无多余顶层文件夹）
- [ ] `id` 与文件名一致、`version` 合法、`appearance` 三选一
- [ ] 只使用 `data-*` 稳定选择器，无随机哈希类名
- [ ] 背景图通过 `{{IMAGE}}` 引用，且验证过“无图退化”不报坏链
- [ ] `--dry-run` 校验通过
- [ ] 在 DSH 浅色与深色下分别看过效果（`both` 主题尤其要两边都看）
- [ ] 不在侧栏根容器上用 `backdrop-filter`
