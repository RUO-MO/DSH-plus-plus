# -*- coding: utf-8 -*-
"""DSH++ 运行时打标器（原 theme_engine.generate_marker_js，换肤功能移除后独立）。

职责单一：为「结构定位 / 易重建」的区域打上 `data-dsh-skin="<name>"` 稳定标记，
供增强模块与市场脚本用属性选择器定位（不再依赖 CSS Modules 随机哈希类名）。

为什么独立成模块：
  打标器原本内嵌在 theme_engine（换肤引擎）里，被 `ui-tweaks.js` / `wide-screen.js` /
  `focus-mode.js` / `copy-code-button.js` 等增强共用。换肤功能移除后打标器仍需保留，
  故抽到本模块，注入链（dsh-skin.enhance_bundle / server.active_bundle）只依赖它。

标记契约（对增强脚本是稳定 API）：
  frame / sidebar / rightbar / overlay / chat / scroll / composerbar / composer / sessionhead

自终止语义：打标器每 1.2s 复查 `<style id="dsh-skin-cdp">` 是否存在；宿主样式被移除
（还原官方样式 / 用户关闭增强）后自动停止打标，不留残留属性。
"""
import json
import os

import dsh_env

# 注入宿主 style 的 id（与 cdp_skin.STYLE_ID 一致）：打标器据此判断是否仍应工作
STYLE_ID = 'dsh-skin-cdp'

# 打标器产出的全部标记名（供增强脚本与文档引用，避免拼写漂移）
MARKER_NAMES = ('frame', 'sidebar', 'rightbar', 'overlay', 'chat',
                'scroll', 'composerbar', 'composer', 'sessionhead')

# 打标器契约版本：区域选择器矩阵发生不兼容变化时 +1。
# 消费方：server.snapshots_status() 判断快照是否落后；drift 指纹记录当时版本。
# 原为 theme_engine.TEMPLATE_VERSION（换肤模板版本），换肤移除后改为打标器版本，
# 起点沿用 v1 以兼容已存在的 assets/snap-manifest.json。
MARKER_VERSION = 1

# ---------------- 区域契约（原 theme_engine.REGIONS，换肤移除后仍用于漂移巡检） ----------------
# selector 为「打标后」的稳定标记或直取稳定 data 属性。
# 用途：① 增强脚本按这些选择器定位；② probe 实测存活 → 漂移告警（DSH 升级后自愈）。
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



def generate_marker_js(force_dark=False):
    """生成打标器 JS。

    force_dark 保留参数以兼容旧调用点；换肤移除后默认 False
    （不再由 DSH++ 主动切换 DSH 明暗，交给 DSH 自身与壁纸插件）。
    """
    fd = 'true' if force_dark else 'false'
    return r'''(function(){
  var FORCE_DARK = %FD%;
  function mark(name, fn){ var el=null; try{ el=fn(); }catch(e){}
    if (el && el.nodeType===1 && !el.getAttribute('data-dsh-skin')) el.setAttribute('data-dsh-skin', name);
    return el; }
  function run(){
    if (!document.getElementById('dsh-skin-cdp')) return;  // 宿主样式已移除 → 打标器自终止
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


if __name__ == '__main__':
    # 便于人工核对生成结果：python marker_engine.py
    js = generate_marker_js()
    print('标记名: {0}'.format(', '.join(MARKER_NAMES)))
    print('长度: {0} 字符'.format(len(js)))
    print(js)
