# -*- coding: utf-8 -*-
"""DSHSkin 主题引擎：参数化主题、CSS 生成、区域选择器、运行时打标器

设计要点
--------
1. **区域选择器（REGIONS）是唯一真源**：所有 CSS 模板都由 REGIONS 拼装，
   选择器基于 DeepSeek Harness 前端自带的稳定契约：
   - `data-slot="conversation.composer.bar"` 等 slot 标记
   - `data-phase` / `data-conversation-scroll` / `data-composer-card` /
     `data-composer-seat` / `data-rightbar-col` / `data-shell-overlay` 等 data 属性
   - `#root > div[data-slot] > div` 等结构组合（AppFrame 无稳定标记，用结构定位）
2. **运行时打标器（generate_marker_js）**：为结构定位的区域（frame / sidebar）
   以及 React 重渲染可能重建的元素打上 `data-dsh-skin="<region>"` 稳定标记，
   增强模块（界面微调/会话工具）也复用这些标记 —— 前端升级后仍可自愈。
3. **颜色适配走设计系统变量**：覆盖 `--dsw-alias-*` 语义 token
   （文本/边框/背景/按钮/滚动条），前端所有组件自动跟随，无需逐元素覆盖。
4. **可覆盖**：`~/.dsh-skins/selectors.json` 可覆盖任一区域 selector，
   由 `dsh-skin.py probe`（CDP 实测）或用户手工写入。
5. **完全可逆**：皮肤只存在于渲染进程（CDP 注入的 style），不写任何官方文件。

选择器核实记录：2026-09 实机 CDP 直读 DSH 桌面版 0.1.5-rc.2（Electron 44），
类名为 CSS Modules 哈希（不稳定），稳定标记为上述 data-slot / data-* 契约。
"""
import json
import os
import re

import dsh_env

# ---------------- 模板版本 ----------------
# 每当 REGIONS / _build_css 结构发生「会影响所有主题外观」的变更时 +1。
TEMPLATE_VERSION = 7

# 生成 CSS 时写入的标记行（正则捕获其中的版本号）
_TV_MARK_RE = re.compile(r'dsh-skin-template:\s*v?(\d+)')
_TV_MARK = '   dsh-skin-template: v{0}\n'


# ---------------- 参数模板 ----------------
PARAM_SCHEMA = [
    {"key": "glow_color",    "label": "输入框光晕颜色", "type": "color", "default": "#4f8cff", "min": None, "max": None},
    {"key": "glow_strength", "label": "光晕强度",       "type": "range", "default": 60,  "min": 0,   "max": 100},
    {"key": "glass_opacity", "label": "玻璃透明度",     "type": "range", "default": 62,  "min": 20,  "max": 95},
    {"key": "blur_strength", "label": "背景模糊",       "type": "range", "default": 20,  "min": 0,   "max": 40},
    {"key": "radius",        "label": "卡片圆角",       "type": "range", "default": 14,  "min": 6,   "max": 26},
    {"key": "overlay_color", "label": "背景叠层颜色",   "type": "color", "default": "#0a0c10", "min": None, "max": None},
    {"key": "overlay_alpha", "label": "背景叠层强度",   "type": "range", "default": 35,  "min": 0,   "max": 70},
    {"key": "font_scale",    "label": "正文字号",       "type": "range", "default": 100, "min": 85,  "max": 130},
    {"key": "force_dark",    "label": "强制暗色主题",   "type": "bool",  "default": False, "min": None, "max": None},
]

DEFAULT_PARAMS = {p['key']: p['default'] for p in PARAM_SCHEMA}

# ---------------- 区域选择器（适配层） ----------------
# selector 均为「打标后」的稳定标记或直取稳定 data 属性；
# probe 留作历史兼容（CDP 实测用同一 selector 列表）。
REGIONS = {
    "background": {
        "label": "全局背景",
        "selector": "body",
        "reliable": True,
        "note": "背景图与底色挂在 body（AppFrame 需透明以透出）",
    },
    "frame": {
        "label": "主框架",
        "selector": "[data-dsh-skin='frame']",
        "reliable": True,
        "note": "AppFrame 外壳；无稳定 data 属性，由打标器按 #root > div[data-slot] > div 标记",
    },
    "sidebar": {
        "label": "左侧栏",
        "selector": "[data-dsh-skin='sidebar']",
        "reliable": True,
        "note": "工作区/会话导航面板；由打标器标记",
    },
    "chat": {
        "label": "会话区",
        "selector": "[data-dsh-skin='chat'], [data-phase]:not([contenteditable])",
        "reliable": True,
        "note": "会话根（hero / active / settling）；排除输入编辑面（其 contenteditable 也带 data-phase）",
    },
    "scroll": {
        "label": "消息滚动体",
        "selector": "[data-conversation-scroll]",
        "reliable": True,
        "note": "消息列表滚动区",
    },
    "composerbar": {
        "label": "输入区座位",
        "selector": "[data-composer-seat]",
        "reliable": True,
        "note": "输入区整体容器",
    },
    "composer": {
        "label": "输入条卡片",
        "selector": "[data-composer-card='true']",
        "reliable": True,
        "note": "输入卡片本体（自带圆角）",
    },
    "rightbar": {
        "label": "右侧栏",
        "selector": "[data-rightbar-col]",
        "reliable": True,
        "note": "上下文/工具面板列",
    },
    "overlay": {
        "label": "覆盖层",
        "selector": "[data-shell-overlay]",
        "reliable": True,
        "note": "shell 模态宿主层：常驻全窗（absolute + z-index 20），玻璃只能上在其子元素（弹层本体）",
    },
    "sessionhead": {
        "label": "会话头部",
        "selector": "[data-slot='conversation.session.header']",
        "reliable": True,
        "note": "会话标题栏",
    },
}

# selectors.json 只接受这些键，避免写入垃圾
OVERRIDABLE = set(REGIONS)

# ---------------- 选择器覆盖层 ----------------
OVERRIDES_FILE = os.path.join(dsh_env.SKIN_ROOT, 'selectors.json')


def load_overrides():
    """读取用户/自动校准的选择器覆盖；文件不存在或损坏时返回空 dict"""
    if not os.path.exists(OVERRIDES_FILE):
        return {}
    try:
        with open(OVERRIDES_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    regions = data.get('regions') if isinstance(data.get('regions'), dict) else data
    return {k: v for k, v in regions.items()
            if k in OVERRIDABLE and isinstance(v, str) and v.strip()}


def save_overrides(regions):
    """写入选择器覆盖（原子写），返回实际写入的干净结果"""
    os.makedirs(dsh_env.SKIN_ROOT, exist_ok=True)
    clean = {k: v for k, v in (regions or {}).items()
             if k in OVERRIDABLE and isinstance(v, str) and v.strip()}
    payload = {"_comment": "DeepSeek Harness 选择器覆盖；由 dsh-skin.py probe 自动生成或手工维护",
               "regions": clean}
    tmp = OVERRIDES_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, OVERRIDES_FILE)
    return clean


def resolve_regions(overrides=None):
    """合并默认 REGIONS 与覆盖层"""
    if overrides is None:
        overrides = load_overrides()
    out = {}
    for key, reg in REGIONS.items():
        merged = dict(reg)
        if overrides.get(key):
            merged['selector'] = overrides[key]
            merged['overridden'] = True
        out[key] = merged
    return out


# ---------------- 运行时打标器 ----------------
def generate_marker_js(force_dark=True):
    """为结构定位/易重建的区域打上 data-dsh-skin 稳定标记。

    幂等：window.__DSH_SKIN_BOOT__ 防重复初始化；setInterval 兜底 React 重渲染。
    force_dark 为 True 时顺带把页面切到暗色（data-ds-dark-theme），皮肤可逆。
    """
    fd = 'true' if force_dark else 'false'
    return r'''(function(){
  var FORCE_DARK = %FD%;
  function mark(name, fn){ var el=null; try{ el=fn(); }catch(e){}
    if (el && el.nodeType===1 && !el.getAttribute('data-dsh-skin')) el.setAttribute('data-dsh-skin', name);
    return el; }
  function run(){
    if (!document.getElementById('dsh-skin-cdp')) return;  // 皮肤已移除 → 打标器自终止
    if (FORCE_DARK && !document.body.getAttribute('data-ds-dark-theme')) {
      document.body.setAttribute('data-ds-dark-theme','');
      try{ document.documentElement.style.colorScheme='dark'; }catch(e){}
    }
    var overlay = document.querySelector('[data-shell-overlay]');
    var frame = overlay ? overlay.parentElement : null;
    if (!frame) { try{ frame = document.querySelector('#root > div[data-slot] > div'); }catch(e){} }
    mark('frame', function(){ return frame; });
    if (frame) {
      var col = frame.children[0];
      mark('sidebar', function(){
        // 侧栏内容：优先取 slot="sidebar" 下第一个有子元素的容器；
        // 排除 slot="sidebar.settings"（设置面板宿主，内含 fixed 弹层，不能挂侧栏样式）。
        var s = col && col.querySelector('[data-slot="sidebar"] > div');
        if (!s || !s.children || !s.children.length) {
          s = col && col.querySelector('[data-slot] > div');
        }
        return (s && s.children && s.children.length) ? s : col;
      });
      mark('rightbar', function(){ return frame.querySelector('[data-rightbar-col]'); });
      mark('overlay', function(){ return overlay; });
    }
    mark('chat', function(){ return document.querySelector('[data-phase]:not([contenteditable])'); });
    mark('scroll', function(){ return document.querySelector('[data-conversation-scroll]'); });
    mark('composerbar', function(){ return document.querySelector('[data-composer-seat]'); });
    mark('composer', function(){ return document.querySelector('[data-composer-card="true"]'); });
    mark('sessionhead', function(){ return document.querySelector('[data-slot="conversation.session.header"]'); });
  }
  if (window.__DSH_SKIN_BOOT__) { run(); return; }   // 已初始化过：仅补一次（轮询已在跑）
  window.__DSH_SKIN_BOOT__ = 1;
  run();
  if (window.setInterval) setInterval(run, 1200);
  try{ window.addEventListener('load', run); }catch(e){}
})();
'''.replace('%FD%', fd)


# ---------------- CSS 模板装配 ----------------
def _sel(key, suffix='', regions=None):
    """把区域选择器拼成完整选择器组"""
    reg = (regions or REGIONS)[key]
    sels = [s.strip() for s in reg['selector'].split(',') if s.strip()]
    return ',\n'.join(s + suffix for s in sels)


# ---------------- 玻璃色跟随 DSH 明暗 ----------------
def _glass_follow():
    """生成玻璃层配色的 CSS 变量（浅/暗两套），供 _build_css 的玻璃/边框/阴影引用。

    返回片段定义：
      :root { --dsh-glass: 255,255,255; --dsh-edge: 255,255,255; ... }
      body[data-ds-dark-theme] { --dsh-glass: 16,19,25; ... }
    当 force_dark=true（dark 分支）时调用方不会引用本函数，直接单套暗色。
    """
    return ('\n/* 玻璃层配色跟随 DSH 自身明暗 */\n'
            ':root {\n'
            '  --dsh-bgc: #faf7f2;\n'
            '  --dsh-glass: 255, 255, 255;\n'
            '  --dsh-edge: 0, 0, 0;\n'
            '  --dsh-edge-a: 0.10;\n'
            '  --dsh-shadow: 0 4px 22px rgba(120, 110, 90, 0.10);\n'
            '  --dsh-input-shadow: 0 4px 18px rgba(120, 110, 90, 0.12);\n'
            '}\n'
            'body[data-ds-dark-theme] {\n'
            '  --dsh-bgc: #0b0d11;\n'
            '  --dsh-glass: 16, 19, 25;\n'
            '  --dsh-edge: 255, 255, 255;\n'
            '  --dsh-edge-a: 0.14;\n'
            '  --dsh-shadow: 0 4px 22px rgba(0, 0, 0, 0.35);\n'
            '  --dsh-input-shadow: 0 4px 18px rgba(0, 0, 0, 0.38);\n'
            '}\n')


def _alias_vars(appearance, glass, edge_alpha, force_dark=False):
    """覆盖 dsh 设计系统的 --dsw-alias-* 语义 token，让前端自动跟随玻璃配色。

    输出「浅色 + 暗色」两套 token：`:root` 定义浅色值，
    `body[data-ds-dark-theme]` 定义暗色值 —— DSH 自身怎么切明暗，皮肤就跟怎么走，
    **不再强制覆盖 DSH 的主题切换**。force_dark=true（用户显式勾选强制暗色）时
    两套都输出暗色值，此时打标器会同步把 body 切到 data-ds-dark-theme，两边一致。
    """
    light = {
        'label': '30, 34, 42', 'label_dim': '105, 114, 128', 'label_weak': '140, 148, 160',
        'border': '0, 0, 0', 'edge': '0.08', 'hover': '0, 0, 0',
        'code_bg': '255, 255, 255', 'scroll': '30, 34, 42', 'accent': '#2f6fe0',
    }
    dark = {
        'label': '235, 240, 247', 'label_dim': '150, 160, 175', 'label_weak': '110, 122, 140',
        'border': '255, 255, 255', 'edge': '0.10', 'hover': '255, 255, 255',
        'code_bg': '10, 12, 16', 'scroll': '255, 255, 255', 'accent': '#4f8cff',
    }
    if appearance == 'dark' or force_dark:
        light = dict(dark)  # 强制暗色时浅色套也换成暗色，杜绝浅底深字白屏

    def block(sel, t):
        return (sel + ' {\n'
                '  --dsw-alias-label-primary: rgba(%L%, .97) !important;\n'
                '  --dsw-alias-label-primary-bluish: rgba(%L%, .97) !important;\n'
                '  --dsw-alias-label-primary-foreground: rgba(%L%, .97) !important;\n'
                '  --dsw-alias-label-primary-inverted: rgba(%L%, .30) !important;\n'
                '  --dsw-alias-label-secondary: rgba(%L%, .78) !important;\n'
                '  --dsw-alias-label-tertiary: rgba(%L%, .60) !important;\n'
                '  --dsw-alias-label-caption: rgba(%L%, .50) !important;\n'
                '  --dsw-alias-label-dimmed: rgba(%LD%, .72) !important;\n'
                '  --dsw-alias-bg-base: transparent !important;\n'
                '  --dsw-alias-bg-layer-1: transparent !important;\n'
                '  --dsw-alias-bg-layer-2: transparent !important;\n'
                '  --dsw-alias-bg-layer-3: transparent !important;\n'
                '  --dsw-alias-border-l1: rgba(%B%, %E%) !important;\n'
                '  --dsw-alias-border-l2: rgba(%B%, %E2%) !important;\n'
                '  --dsw-alias-border-l3: rgba(%B%, %E3%) !important;\n'
                '  --dsw-alias-border-l4: rgba(%B%, %E4%) !important;\n'
                '  --dsw-alias-interactive-bg-hover: rgba(%H%, .07) !important;\n'
                '  --dsw-alias-interactive-bg-hover-solid: rgba(%H%, .10) !important;\n'
                '  --dsw-alias-interactive-bg-active: rgba(%H%, .12) !important;\n'
                '  --dsw-alias-markdown-code-block: rgba(%CB%, .72) !important;\n'
                '  --dsw-alias-markdown-inline-code: rgba(%CB%, .55) !important;\n'
                '  --dsw-alias-scrollbar-bg-l1: rgba(%S%, .12) !important;\n'
                '  --dsw-alias-scrollbar-bg-l2: rgba(%S%, .20) !important;\n'
                '  --dsw-alias-scrollbar-hover-l1: rgba(%S%, .25) !important;\n'
                '  --dsw-alias-scrollbar-hover-l2: rgba(%S%, .35) !important;\n'
                '}\n').replace('%L%', t['label']).replace('%LD%', t['label_dim'])\
                .replace('%B%', t['border']).replace('%E%', str(t['edge']))\
                .replace('%E2%', str(min(0.24, float(t['edge']) + 0.10)))\
                .replace('%E3%', str(min(0.34, float(t['edge']) + 0.20)))\
                .replace('%E4%', str(min(0.44, float(t['edge']) + 0.30)))\
                .replace('%H%', t['hover']).replace('%CB%', t['code_bg'])\
                .replace('%S%', t['scroll'])
    return block(':root', light) + block('body[data-ds-dark-theme]', dark)


def _build_css(p, appearance, image_url, regions=None):
    """由参数 + 色板 + 背景图 url(...) 值装配完整 CSS"""
    # force_dark=true（用户显式勾选强制暗色）时无视 appearance，一律走暗色分支——
    # 打标器会同步把 body 切到 data-ds-dark-theme，两边必须一致，否则浅底深字白屏。
    dark = appearance == 'dark' or bool(p.get('force_dark'))
    glow = hex_to_rgb(p['glow_color'])
    overlay = hex_to_rgb(p['overlay_color'])
    strength = p['glow_strength'] / 100.0
    glass = p['glass_opacity'] / 100.0
    blur = p['blur_strength']
    radius = p['radius']
    input_alpha = min(0.92, glass + 0.06)
    panel_alpha = min(0.88, glass + 0.14)
    side_alpha = min(0.84, glass + 0.10)
    card_alpha = min(0.90, glass + 0.12)
    overlay_rgba = 'rgba({0}, {1:.2f})'.format(overlay, p['overlay_alpha'] / 100.0)
    if image_url:
        bg_image = '{0}, {1}'.format(overlay_rgba, image_url) if p['overlay_alpha'] else image_url
        bg_decl = '  background-image: {0};'.format(bg_image)
    else:
        bg_decl = '  background-image: none;'
    if dark:
        # 强制暗色：玻璃/背景用暗色系，:root 也会被 _alias_vars 换成暗色 token
        edge, edge_alpha = '255, 255, 255', '0.10'
        bgc, msg = '#0b0d11', '16, 19, 25'
        shadow = '0 4px 22px rgba(0, 0, 0, 0.35)'
        input_shadow = '0 4px 18px rgba(0, 0, 0, 0.38)'
        glass_sel = ''   # 单套暗色，无需切换
    else:
        # 跟随 DSH 自身明暗：玻璃/边框/阴影/底色全部走 CSS 变量（:root 浅 / body[data-ds-dark-theme] 暗）
        edge, edge_alpha = 'var(--dsh-edge)', 'var(--dsh-edge-a)'
        bgc, msg = 'var(--dsh-bgc)', 'var(--dsh-glass)'
        shadow = 'var(--dsh-shadow)'
        input_shadow = 'var(--dsh-input-shadow)'
        glass_sel = _glass_follow()
    font_scale = p['font_scale'] / 100.0
    title = 'DSHSkin · DeepSeek Harness ' + ('暗色' if dark else '跟随系统') + '主题'

    _css = """/* ============================================================
   {title}（由 theme_engine 生成，请勿手工编辑）
   - 背景图铺满窗口（可叠加提亮/暗化层）
   - 覆盖 --dsw-alias-* 设计系统 token，前端文本/边框/滚动条自动跟随
   - 框架透明，背景透出；侧栏/会话/输入条做雾态玻璃
   - 输入条聚焦光晕（颜色/强度可调）
""" + _TV_MARK.format(TEMPLATE_VERSION) + """   ============================================================ */

/* ---------- 0. 设计系统 token 覆盖（浅/暗两套，跟随 DSH 明暗） ---------- */
{alias_vars}
{glass_vars}
/* ---------- 1. 全局背景 ---------- */
body {{
{bg_decl}
  background-size: cover;
  background-position: center center;
  background-repeat: no-repeat;
  background-attachment: fixed;
  background-color: {bgc};
}}

/* ---------- 2. 框架透明，让背景透出 ---------- */
{sel_frame} {{
  background-color: transparent !important;
  background-image: none !important;
}}

/* ---------- 3. 左侧栏：雾态玻璃条 ----------
   注意：**禁止**在侧栏容器上使用 backdrop-filter！
   DSH 设置面板/权限弹层是挂在侧栏容器里的 fixed 弹层，
   backdrop-filter 会把 fixed 子元素从「相对视口」改为「相对该容器」定位，
   导致 800px 居中弹窗被压进 420px 侧栏（设置面板变窄贴左）。
   只保留半透明玻璃色 + 细边框，弹层不受影响。 */
{sel_sidebar} {{
  background: rgba({msg}, {side_alpha:.2f}) !important;
  border-right: 1px solid rgba({edge}, {edge_alpha}) !important;
}}

/* ---------- 4. 右侧栏：雾态玻璃条 ---------- */
{sel_rightbar} {{
  background: rgba({msg}, {side_alpha:.2f}) !important;
  backdrop-filter: blur({blur}px) saturate(1.15);
  -webkit-backdrop-filter: blur({blur}px) saturate(1.15);
  border-left: 1px solid rgba({edge}, {edge_alpha}) !important;
}}

/* ---------- 5. 会话区：浅玻璃（背景透出 + 可读性；同样避免 backdrop-filter，防弹层约束） ---------- */
{sel_chat} {{
  background: rgba({msg}, {panel_alpha:.2f}) !important;
}}

/* 消息滚动体透明，避免遮挡 */
{sel_scroll} {{
  background-color: transparent !important;
}}

/* ---------- 6. 输入区座位：压平独立背景 ---------- */
{sel_composerbar} {{
  background: transparent !important;
  background-color: transparent !important;
}}

/* 输入条卡片：磨砂玻璃 + 光晕 */
{sel_composer} {{
  background: rgba({msg}, {input_alpha:.2f}) !important;
  backdrop-filter: blur({input_blur}px) saturate(1.2);
  -webkit-backdrop-filter: blur({input_blur}px) saturate(1.2);
  border-radius: {input_radius}px !important;
  border: 1px solid rgba({edge}, {edge_alpha}) !important;
  box-shadow: {input_shadow};
  transition: border-color 0.25s ease, box-shadow 0.25s ease;
}}

/* 输入条聚焦光晕 */
{sel_composer_focus} {{
  border-color: rgba({glow}, 0.55) !important;
  box-shadow:
    0 0 0 3px rgba({glow}, {glow_ring:.2f}),
    0 8px 32px rgba({glow}, {glow_outer:.2f}),
    0 0 22px rgba({glow}, {glow_halo:.2f}) !important;
}}

/* ---------- 7. 会话头部透明 ---------- */
{sel_head} {{
  background-color: transparent !important;
}}

/* ---------- 8. 覆盖层玻璃化（只给弹层本体；宿主层常驻全窗，直接上玻璃=整屏蒙尘） ---------- */
{sel_overlay} > * {{
  background: rgba({msg}, {card_alpha:.2f}) !important;
  backdrop-filter: blur({blur}px);
  -webkit-backdrop-filter: blur({blur}px);
}}

/* ---------- 9. 字号缩放（可选） ---------- */
{font_rule}
""".format(
        title=title, bg_decl=bg_decl, bgc=bgc,
        alias_vars=_alias_vars(appearance, glass, edge_alpha, bool(p.get('force_dark'))),
        glass_vars=glass_sel,
        sel_frame=_sel('frame', regions=regions),
        sel_sidebar=_sel('sidebar', regions=regions),
        sel_rightbar=_sel('rightbar', regions=regions),
        sel_chat=_sel('chat', regions=regions),
        sel_scroll=_sel('scroll', regions=regions),
        sel_composerbar=_sel('composerbar', regions=regions),
        sel_composer=_sel('composer', regions=regions),
        sel_composer_focus=_sel('composer', ':focus-within', regions=regions),
        sel_head=_sel('sessionhead', regions=regions),
        sel_overlay=_sel('overlay', ' > *', regions=regions),
        msg=msg, edge=edge, edge_alpha=edge_alpha,
        shadow=shadow, input_shadow=input_shadow,
        side_alpha=side_alpha, panel_alpha=panel_alpha, glass=glass,
        input_alpha=input_alpha, card_alpha=card_alpha,
        blur=blur, panel_blur=min(40, blur + 6), input_blur=min(40, blur + 4),
        radius=radius, input_radius=radius + 2,
        glow=glow, glow_ring=0.20 * strength, glow_outer=0.22 * strength,
        glow_halo=0.16 * strength,
        font_rule=('body {{ --dsh-content-font-size: {0}px !important; }}'
                   .format(round(14 * font_scale, 1)) if font_scale != 1.0
                   else '/* 字号默认 */'),
    )
    return _css.replace('{title}', title)


_IMG_CACHE = {'key': None, 'uri': ''}


def image_data_uri(image_path):
    """把背景图转成 data URI（按 mtime+size 缓存）。

    路径为空或文件不存在时返回 ''，调用方退化为无图主题。
    """
    if not image_path or not os.path.isfile(image_path):
        return ''
    try:
        st = os.stat(image_path)
        key = (os.path.abspath(image_path), st.st_mtime_ns, st.st_size)
    except OSError:
        return ''
    if _IMG_CACHE['key'] == key:
        return _IMG_CACHE['uri']
    try:
        import base64 as _b64
        with open(image_path, 'rb') as f:
            b64 = _b64.b64encode(f.read()).decode('ascii')
        ext = os.path.splitext(image_path)[1].lstrip('.').lower()
        mime = 'image/jpeg' if ext in ('jpg', 'jpeg') else ('image/webp' if ext == 'webp' else 'image/png')
        uri = 'data:{0};base64,{1}'.format(mime, b64)
    except Exception:
        return ''
    _IMG_CACHE['key'], _IMG_CACHE['uri'] = key, uri
    return uri


def generate_css(params, image_name, appearance='light', overrides=None):
    """文件注入用：背景图按文件名引用"""
    p = norm_params(params)
    url = 'url("{0}")'.format(image_name) if image_name else ''
    return _build_css(p, appearance, url, resolve_regions(overrides))


def generate_inject_css(params, image_path, appearance='light', overrides=None, extra_css=''):
    """CDP 运行时注入用：背景图内嵌 data URI（规避 file:///CSP 限制）。

    image_path 为空 → 生成无图主题；extra_css → 追加用户 CSS（custom-css 模块）。
    """
    p = norm_params(params)
    uri = image_data_uri(image_path)
    css = _build_css(p, appearance, 'url("{0}")'.format(uri) if uri else '',
                     resolve_regions(overrides))
    if extra_css and extra_css.strip():
        css += '\n\n/* ================= DSHSkin 用户 CSS ================= */\n' + extra_css.strip() + '\n'
    return css


# ---------------- 工具函数 ----------------
def hex_to_rgb(hex_color):
    """#rrggbb -> 'r, g, b'"""
    h = (hex_color or '').lstrip('#')
    if len(h) != 6:
        h = '4f8cff'
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        r, g, b = 79, 140, 255
    return '{0}, {1}, {2}'.format(r, g, b)


def norm_params(params):
    """合并用户参数与默认值，并对 range/bool/color 类型做钳制"""
    out = dict(DEFAULT_PARAMS)
    if not isinstance(params, dict):
        return out
    by_key = {p['key']: p for p in PARAM_SCHEMA}
    for k, v in params.items():
        if k not in by_key:
            continue
        p = by_key[k]
        if p['type'] == 'color':
            s = str(v).lstrip('#')
            out[k] = '#{0}'.format(s) if len(s) == 6 and re.fullmatch(r'[0-9a-fA-F]{6}', s) else p['default']
        elif p['type'] == 'bool':
            out[k] = bool(v)
        else:
            try:
                out[k] = int(round(float(v)))
            except (TypeError, ValueError):
                out[k] = p['default']
            out[k] = max(p['min'], min(p['max'], out[k]))
    return out


# ---------------- 区域覆盖诊断 ----------------
def diagnose_regions(css_text, overrides=None):
    """检查注入的 css 是否覆盖了各已知区域"""
    regions = resolve_regions(overrides)
    out = []
    for key, reg in regions.items():
        item = {"key": key, "label": reg["label"], "reliable": reg.get("reliable", False),
                "overridden": bool(reg.get("overridden"))}
        if not reg.get("selector"):
            item["covered"] = False
            item["note"] = reg.get("note", "")
        else:
            selectors = [s.strip() for s in reg["selector"].split(',') if s.strip()]
            item["covered"] = any(s in css_text for s in selectors)
        out.append(item)
    return out


def theme_staleness(css_text):
    """扫描主题 CSS 是否引用已知失效的类名（trae 旧主题迁移用）"""
    tokens = set(re.findall(r'\.([A-Za-z_][\w-]*)', css_text or ''))
    dead = {'solo-lite', 'trae-skin', 'chat-input-v2', 'turn__', 'messageInput',
            'virtualized-message-list', 'icube'}
    return sorted(t for t in tokens if any(d in t for d in dead))


def css_template_version(css_text):
    """读取主题 CSS 里记录的模板版本号；无标记（旧版生成）返回 0"""
    m = _TV_MARK_RE.search(css_text or '')
    return int(m.group(1)) if m else 0


def css_needs_rebuild(css_text):
    """判断已安装主题的 skin_css 是否需要用当前模板重建。

    三种情况都算「过期」：无模板版本标记 / 标记版本 < TEMPLATE_VERSION / 含旧类名。
    """
    if not css_text or not css_text.strip():
        return True
    if css_template_version(css_text) < TEMPLATE_VERSION:
        return True
    if theme_staleness(css_text):
        return True
    return False


if __name__ == '__main__':
    css = generate_css(DEFAULT_PARAMS, 'bg-dsh.png', 'dark')
    print('[OK] 生成 {0} 字符的 skin.css（默认参数）'.format(len(css)))
    print('--- 区域诊断 ---')
    for r in diagnose_regions(css):
        print('  {0}: {1}'.format(r["label"], "已覆盖" if r["covered"] else "未覆盖"))
    print('--- 打标器 ---')
    print(generate_marker_js(True)[:200] + '...')


# 供 /api/template 预览用的默认渲染（无图，放在函数定义之后）
INJECT_TEMPLATE = generate_inject_css(DEFAULT_PARAMS, '', 'light')
DARK_INJECT_TEMPLATE = generate_inject_css(DEFAULT_PARAMS, '', 'dark')
