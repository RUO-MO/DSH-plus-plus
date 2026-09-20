# -*- coding: utf-8 -*-
"""DSHSkin 增强引擎（Enhance Engine）

把 TraeSkin 从「换肤面板」推向「DeepSeek Harness 增强器」的核心地基。

思路对齐 codex++：**不改官方文件**，通过 CDP 向渲染进程注入
`window.DSHSkin` 运行时 + 用户脚本 + 用户 CSS，每个增强模块可单独开关，
状态持久化在 `~/.dsh-skins/enhance.json`。

目录约定
--------
  ~/.dsh-skins/enhance/*.js     用户脚本（注入渲染进程执行）
  ~/.dsh-skins/enhance/*.css    用户 CSS（叠加在主题 CSS 之后）
  ~/.dsh-skins/enhance.json     开关状态

运行时 API（脚本里可直接用 window.DSHSkin）
--------
  DSHSkin.toast(msg, ms)          轻提示
  DSHSkin.log(...)                带前缀日志（面板「增强」页可见）
  DSHSkin.storage.get/set/remove  命名空间 localStorage（dsh-skin: 前缀）
  DSHSkin.ready(cb)               DOM 就绪后回调
  DSHSkin.on(selector, cb, opts)  监听元素出现（MutationObserver）
  DSHSkin.interval(fn, ms)        定时器（页面卸载自动清理）
  DSHSkin.wait(selector, ms)      等待元素出现，返回 Promise
  DSHSkin.def(id, fn)             注册模块（幂等，重复注入只跑一次）

设计约束
--------
- 用户脚本用 `new Function(code)` 包裹执行：脚本自身的**语法错误**也能被
  try/catch 捕获，不会拖垮整个运行时。
- 每个脚本只执行一次（以脚本名去重），重复注入只补未加载的脚本。
- 本模块不 depend on websocket；只负责「生成要注入的东西」，与 CDP 解耦。
"""
import hashlib
import json
import os
import re
import threading
import time

import dsh_env

ENHANCE_DIR = os.path.join(dsh_env.SKIN_ROOT, 'enhance')
STATE_FILE = os.path.join(dsh_env.SKIN_ROOT, 'enhance.json')
RUNTIME_VERSION = '1.0.0'

# 进程内状态锁：多线程 API 并发改 enhance.json 时保证读-改-写不丢更新
_STATE_LOCK = threading.RLock()

# 内置增强模块的 JS 源目录（随 exe 一起打包）
def _modules_dir():
    """内置模块目录：打包后位于 _MEIPASS，源码运行时位于脚本同级"""
    import sys
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(base, 'enhance-modules'),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), 'enhance-modules')):
        if os.path.isdir(cand):
            return cand
    return os.path.join(base, 'enhance-modules')


MODULES_DIR = _modules_dir()

# 内置增强模块（可单独开关）。file 指向 enhance-modules/ 下的 JS 源。
MODULES = [
    {"key": "bridge", "label": "运行时桥 DSHSkin",
     "desc": "向页面注入 window.DSHSkin（toast / log / storage / ui / dom 工具），一切增强的基础",
     "reliable": True, "always": True},
    {"key": "session-tools", "label": "会话工具",
     "desc": "导出 / 复制当前会话为 Markdown（Ctrl+Alt+E / Ctrl+Alt+C），纯前端提取，不依赖官方接口",
     "file": "session-tools.js", "reliable": True},
    {"key": "ui-tweaks", "label": "界面微调",
     "desc": "会话区宽度 / 消息疏密 / 正文字号 / 代码字体 / 隐藏冗余元素，改动全可逆（⚙ 或 Ctrl+Alt+U）",
     "file": "ui-tweaks.js", "reliable": True},
    {"key": "input-plus", "label": "输入增强",
     "desc": "纯文本粘贴 / 提示词片段面板（Ctrl+Alt+/）/ 快捷发送（Ctrl+Alt+Enter）/ 输入历史（Alt+↑↓）",
     "file": "input-plus.js", "reliable": True},
    {"key": "usage-meter", "label": "上下文用量",
     "desc": "右下角常驻估算 token 用量与上下文占比，点击展开明细，纯本地计算",
     "file": "usage-meter.js", "reliable": True},
    {"key": "thinking-zh", "label": "思维链中文",
     "desc": "发送时在消息末尾附加隐形指令，让 DeepSeek Harness 的思考过程全程使用中文（纯前端注入，可单独开关）",
     "file": "thinking-zh.js", "reliable": True},
    {"key": "user-scripts", "label": "用户脚本",
     "desc": "执行 ~/.dsh-skins/enhance/*.js（语法错误会被隔离，不影响其它脚本）",
     "reliable": True},
    {"key": "custom-css", "label": "用户 CSS",
     "desc": "把 ~/.dsh-skins/enhance/*.css 叠加在主题样式之后，做主题之外的微调",
     "reliable": True},
]
_MODULE_KEYS = {m['key'] for m in MODULES}
_BUILTIN = [m for m in MODULES if m.get('file')]

DEFAULT_STATE = {
    "enabled": False,
    "modules": {m['key']: True for m in MODULES},
    "scripts": {},
    "hotkeys": {},       # 用户改键覆盖 {action: combo}
    "scripts_meta": {},  # 市场脚本来源/权限留档
}


# ---------------- 快捷键注册表（P1-6） ----------------
# 统一登记所有内置模块的快捷键，模块运行时从 R.config.hotkeys 读取，不再各自硬编码。
HOTKEYS = {
    'ui-tweaks.toggle': {'default': 'Ctrl+Alt+U', 'label': '打开界面微调面板', 'module': 'ui-tweaks'},
    'session.export':   {'default': 'Ctrl+Alt+E', 'label': '导出当前会话为 Markdown', 'module': 'session-tools'},
    'session.copy':     {'default': 'Ctrl+Alt+C', 'label': '复制当前会话 Markdown', 'module': 'session-tools'},
    'input.snippets':  {'default': 'Ctrl+Alt+/', 'label': '打开提示词片段面板', 'module': 'input-plus'},
    'input.send':      {'default': 'Ctrl+Alt+Enter', 'label': '快捷发送', 'module': 'input-plus'},
}
_MOD_ORDER = ('ctrl', 'alt', 'shift', 'meta')


def normalize_combo(combo):
    """把 'ctrl+alt+u' 归一化成 'Ctrl+Alt+U'；非法/空返回 ''。"""
    if not combo or not isinstance(combo, str):
        return ''
    parts = [p.strip().lower() for p in combo.split('+') if p.strip()]
    mods, key = [], ''
    for p in parts:
        if p in ('ctrl', 'control', 'ctl'):
            mods.append('ctrl')
        elif p in ('alt', 'option'):
            mods.append('alt')
        elif p == 'shift':
            mods.append('shift')
        elif p in ('meta', 'cmd', 'win', 'command'):
            mods.append('meta')
        else:
            key = p
    if not key:
        return ''
    mods = [m for m in _MOD_ORDER if m in set(mods)]
    pretty = {' ': 'Space', 'arrowup': 'ArrowUp', 'arrowdown': 'ArrowDown',
              'arrowleft': 'ArrowLeft', 'arrowright': 'ArrowRight', '/': '/',
              'enter': 'Enter', 'escape': 'Escape', 'tab': 'Tab'}
    key = pretty.get(key, key.upper() if len(key) == 1 else key.capitalize())
    return '+'.join(m.capitalize() for m in mods) + ('+' if mods else '') + key


def resolve_hotkeys(custom=None):
    """合并默认快捷键与用户覆盖，返回 {action: combo}。"""
    custom = custom if isinstance(custom, dict) else {}
    out = {}
    for action, spec in HOTKEYS.items():
        combo = custom.get(action)
        out[action] = normalize_combo(combo) if combo else spec['default']
    return out


def detect_hotkey_conflicts(custom=None):
    """检测同一组合绑定多个动作；返回 [{combo, actions:[...]}]。"""
    resolved = resolve_hotkeys(custom)
    seen = {}
    for action, combo in resolved.items():
        if not combo:
            continue
        seen.setdefault(combo.lower(), []).append(action)
    return [{'combo': resolved[acts[0]], 'actions': acts}
            for acts in seen.values() if len(acts) > 1]


def builtin_module_js():
    """读取内置模块 JS 源；返回 [(key, filename, text, md5)]，缺失的跳过。"""
    out = []
    for m in _BUILTIN:
        p = os.path.join(MODULES_DIR, m['file'])
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding='utf-8') as f:
                txt = f.read()
        except OSError:
            continue
        if txt.strip():
            out.append((m['key'], m['file'], txt, hashlib.md5(txt.encode('utf-8')).hexdigest()[:12]))
    return out


def _indent(text, n):
    """给多行文本统一加缩进（内置模块源码嵌入运行时用）"""
    pad = ' ' * n
    return '\n'.join((pad + line) if line.strip() else line for line in text.split('\n'))

_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.(js|css)$')


# ---------------- 目录 / 状态 ----------------
def ensure_dirs():
    os.makedirs(dsh_env.SKIN_ROOT, exist_ok=True)
    os.makedirs(ENHANCE_DIR, exist_ok=True)


def load_state():
    """读取增强开关；损坏时回落默认（工具要能自愈）"""
    with _STATE_LOCK:
        return _load_state_locked()


def _load_state_locked():
    if not os.path.exists(STATE_FILE):
        return json.loads(json.dumps(DEFAULT_STATE))
    try:
        with open(STATE_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return json.loads(json.dumps(DEFAULT_STATE))
    if not isinstance(data, dict):
        return json.loads(json.dumps(DEFAULT_STATE))
    out = json.loads(json.dumps(DEFAULT_STATE))
    out['enabled'] = bool(data.get('enabled'))
    mods = data.get('modules') if isinstance(data.get('modules'), dict) else {}
    for k in _MODULE_KEYS:
        if k in mods:
            out['modules'][k] = bool(mods[k])
    out['modules']['bridge'] = True                       # 桥永远开（其它模块都依赖它）
    scripts = data.get('scripts') if isinstance(data.get('scripts'), dict) else {}
    out['scripts'] = {k: bool(v) for k, v in scripts.items() if _NAME_RE.match(k) and k.endswith('.js')}
    # 用户改键：只接受注册表里的 action 与合法 combo
    hk = data.get('hotkeys') if isinstance(data.get('hotkeys'), dict) else {}
    out['hotkeys'] = {a: normalize_combo(c) for a, c in hk.items()
                      if a in HOTKEYS and normalize_combo(c)}
    # 脚本来源/权限留档：键须为合法脚本文件名
    sm = data.get('scripts_meta') if isinstance(data.get('scripts_meta'), dict) else {}
    out['scripts_meta'] = {k: v for k, v in sm.items() if _NAME_RE.match(k) and isinstance(v, dict)}
    return out


def save_state(state):
    """原子写入增强开关（持锁）"""
    with _STATE_LOCK:
        return _save_state_locked(state)


def update_state(mutator):
    """原子读-改-写 enhance.json：mutator 在锁内修改 state 并可返回值。"""
    with _STATE_LOCK:
        st = _load_state_locked()
        ret = mutator(st)
        _save_state_locked(st)
        return ret if ret is not None else st


def _save_state_locked(state):
    ensure_dirs()
    clean = {
        "enabled": bool(state.get('enabled')),
        "modules": {k: True for k in ('bridge',)} |
                   {k: bool(state.get('modules', {}).get(k, True)) for k in _MODULE_KEYS if k != 'bridge'},
        "scripts": {k: bool(v) for k, v in (state.get('scripts') or {}).items()
                    if _NAME_RE.match(k) and k.endswith('.js')},
        "hotkeys": {a: normalize_combo(c) for a, c in (state.get('hotkeys') or {}).items()
                    if a in HOTKEYS and normalize_combo(c)},
        "scripts_meta": {k: v for k, v in (state.get('scripts_meta') or {}).items()
                         if _NAME_RE.match(k) and isinstance(v, dict)},
    }
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)
    return clean


# ---------------- 市场/用户脚本安全分析（P1-5） ----------------
# 脚本以页面全权限运行，安装前必须让用户看到「它声明了什么权限、实际用了什么能力、风险多高」。
SCRIPT_PERMISSIONS = ('dom', 'storage', 'network', 'clipboard', 'eval', 'navigation', 'cookie')
# 能力 → 源码特征（静态扫描，宁可误报也不漏报）
_CAPABILITY_PATTERNS = {
    'network':    [r'\bfetch\s*\(', r'XMLHttpRequest', r'\bWebSocket\s*\(',
                   r'sendBeacon', r'EventSource\s*\('],
    'eval':       [r'\beval\s*\(', r'new\s+Function\s*\('],
    'cookie':     [r'document\.cookie'],
    'clipboard':  [r'navigator\.clipboard'],
    'storage':    [r'localStorage', r'sessionStorage', r'indexedDB'],
    'navigation': [r'window\.open\s*\(', r'\.location\.(href|assign|replace)\s*='],
    'dom':        [r'document\.', r'querySelector', r'addEventListener', r'\bDOM\b'],
}
_HIGH_CAP = {'network', 'eval'}
_MED_CAP = {'cookie', 'clipboard'}
_FM_LINE = re.compile(r'//\s*@(\w+)\s*:\s*(.*)')


def parse_frontmatter(content):
    """从脚本头部最多 20 行解析 // @key: value 形式的 frontmatter。"""
    meta = {}
    for line in (content or '').splitlines()[:20]:
        m = _FM_LINE.match(line.strip())
        if m:
            meta[m.group(1).lower()] = m.group(2).strip()
    return meta


def parse_permissions(meta):
    """把 @permissions: a, b 解析成合法权限集合（非法值丢弃）；缺省给 ['dom']。"""
    raw = meta.get('permissions', '')
    perms = [p.strip().lower() for p in re.split(r'[,，\s]+', raw) if p.strip()]
    perms = [p for p in perms if p in SCRIPT_PERMISSIONS]
    return perms or ['dom']


def analyze_script(content, declared=None):
    """静态分析脚本：声明权限 / 实际能力 / 未声明却使用 / 风险等级。"""
    content = content or ''
    meta = parse_frontmatter(content)
    perms = declared if declared is not None else parse_permissions(meta)
    uses = {}
    for cap, pats in _CAPABILITY_PATTERNS.items():
        uses[cap] = any(re.search(p, content) for p in pats)
    used = [c for c in SCRIPT_PERMISSIONS if uses.get(c)]
    # dom 是脚本的默认基础能力，不算「未声明」；其余实际使用但未声明的要提示
    undeclared = [c for c in used if c not in perms and c != 'dom']
    if any(c in _HIGH_CAP for c in used):
        risk = 'high'
    elif any(c in _MED_CAP for c in used):
        risk = 'medium'
    elif used:
        risk = 'low'
    else:
        risk = 'minimal'
    return {
        'name': meta.get('name', ''), 'title': meta.get('title', ''),
        'version': meta.get('version', ''), 'author': meta.get('author', ''),
        'source': meta.get('source', meta.get('from', '')),
        'description': meta.get('description', ''),
        'permissions': perms, 'uses': used, 'undeclared': undeclared,
        'risk': risk, 'size': len(content.encode('utf-8')),
    }


# ---------------- 脚本 / 样式文件 ----------------
def _valid_name(name):
    return bool(name and _NAME_RE.match(os.path.basename(name)))


def list_files(ext=None):
    """列出 enhance 目录下的脚本/CSS；返回 [{name, size, mtime, enabled}]"""
    ensure_dirs()
    state = load_state()
    out = []
    try:
        names = sorted(os.listdir(ENHANCE_DIR))
    except OSError:
        return out
    for name in names:
        if not _valid_name(name):
            continue
        if ext and not name.endswith(ext):
            continue
        p = os.path.join(ENHANCE_DIR, name)
        if not os.path.isfile(p):
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({
            "name": name, "size": st.st_size,
            "mtime": time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime)),
            "enabled": state['scripts'].get(name, True),
        })
    return out


def read_file(name):
    p = os.path.join(ENHANCE_DIR, os.path.basename(name))
    if not _valid_name(name) or not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf-8', errors='ignore') as f:
            return f.read()
    except OSError:
        return None


def write_file(name, content):
    """新建/覆盖一个脚本或 CSS（名称为空或非法时抛 ValueError）"""
    base = os.path.basename(name or '')
    if not _valid_name(base):
        raise ValueError('文件名只能为 字母/数字/._- 且以 .js 或 .css 结尾')
    ensure_dirs()
    p = os.path.join(ENHANCE_DIR, base)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content or '')
    if base.endswith('.js'):
        st = load_state()
        st['scripts'].setdefault(base, True)
        save_state(st)
    return base


def delete_file(name):
    base = os.path.basename(name or '')
    if not _valid_name(base):
        return False
    p = os.path.join(ENHANCE_DIR, base)
    try:
        if os.path.isfile(p):
            os.remove(p)
    except OSError:
        return False
    st = load_state()
    st['scripts'].pop(base, None)
    save_state(st)
    return True


def set_script_enabled(name, enabled):
    base = os.path.basename(name or '')
    if not _valid_name(base) or not base.endswith('.js'):
        return None
    st = load_state()
    st['scripts'][base] = bool(enabled)
    return save_state(st)['scripts']


def custom_css_text():
    """拼接所有启用的用户 CSS（custom-css 模块开启时）"""
    st = load_state()
    if not st['enabled'] or not st['modules'].get('custom-css'):
        return ''
    parts = []
    for item in list_files('.css'):
        txt = read_file(item['name'])
        if txt:
            parts.append('/* ---- DSHSkin 用户样式: {0} ---- */\n{1}'.format(item['name'], txt))
    return '\n\n'.join(parts)


# ---------------- 运行时 JS 生成 ----------------
_RUNTIME_TMPL = r"""(function () {
  var V = "__VERSION__";
  var R = window.DSHSkin = window.DSHSkin || {};
  __RELOAD__
  if (R.__build === "__BUILD__") { return { ok: true, cached: true, v: V, build: "__BUILD__" }; }
  R.__build = "__BUILD__";
  R.__v = V;
  R.modules = R.modules || {};
  R.__def = R.__def || {};
  R.__loaded = R.__loaded || {};
  R.logs = R.logs || [];
  R.config = R.config || {};
  R.config.hotkeys = __HOTKEYS__;
  // 统一快捷键判断：模块只声明 action，组合由后端注册表/用户改键决定，避免各处硬编码
  R.isHotkey = function (e, action) {
    try {
      var combo = (R.config.hotkeys || {})[action];
      if (!combo) return false;
      var parts = combo.toLowerCase().split('+').map(function (x) { return x.trim(); });
      var needKey = parts[parts.length - 1];
      var has = { ctrl: parts.indexOf('ctrl') >= 0, alt: parts.indexOf('alt') >= 0,
                  shift: parts.indexOf('shift') >= 0, meta: parts.indexOf('meta') >= 0 };
      var ek = String(e.key || '').toLowerCase();
      // 兼容物理键位（不同布局 e.key 可能异常）：Slash/Enter 等用 e.code 兜底
      var codeMap = { slash: '/', enter: 'enter', space: ' ', escape: 'escape',
                      arrowup: 'arrowup', arrowdown: 'arrowdown',
                      arrowleft: 'arrowleft', arrowright: 'arrowright' };
      var ck = codeMap[String(e.code || '').toLowerCase()];
      var keyMatch = ek === needKey || (ck === needKey);
      return !!e.ctrlKey === has.ctrl && !!e.altKey === has.alt &&
             !!e.shiftKey === has.shift && !!e.metaKey === has.meta && keyMatch;
    } catch (_) { return false; }
  };
  R.__hash = '';

  R.log = function () {
    try {
      var a = Array.prototype.slice.call(arguments).map(function (x) {
        try { return (typeof x === 'string') ? x : JSON.stringify(x); } catch (e) { return String(x); }
      });
      var s = a.join(' ');
      R.logs.push({ t: Date.now(), lv: 'info', m: s });
      if (R.logs.length > 300) R.logs.splice(0, R.logs.length - 300);
      console.log('%c[DSHSkin]', 'color:#3c5a4a;font-weight:700', s);
      return s;
    } catch (e) { return ''; }
  };
  // 模块/用户脚本错误专用：带级别与来源模块，供后端 CDP 回收后在面板日志定位「谁报了什么错」
  R.error = function (mod) {
    try {
      var rest = Array.prototype.slice.call(arguments, 1).map(function (x) {
        try { return (typeof x === 'string') ? x : JSON.stringify(x); } catch (e) { return String(x); }
      });
      var entry = { t: Date.now(), lv: 'error', mod: String(mod || ''), m: rest.join(' ') };
      R.logs.push(entry);
      if (R.logs.length > 300) R.logs.splice(0, R.logs.length - 300);
      console.error('%c[DSHSkin:错误]', 'color:#c0392b;font-weight:700', mod, entry.m);
      return entry;
    } catch (e) { return null; }
  };

  R.toast = function (msg, ms) {
    try {
      var d = document.getElementById('dsh-skin-toast');
      if (!d) {
        d = document.createElement('div');
        d.id = 'dsh-skin-toast';
        d.style.cssText = 'position:fixed;z-index:2147483647;left:50%;bottom:38px;transform:translateX(-50%);' +
          'padding:9px 16px;border-radius:10px;font:13px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;' +
          'background:rgba(28,32,40,.88);color:#fff;box-shadow:0 8px 28px rgba(0,0,0,.28);' +
          'opacity:0;transition:opacity .22s ease;pointer-events:none;max-width:70vw;';
        (document.body || document.documentElement).appendChild(d);
      }
      d.textContent = String(msg);
      d.style.opacity = '1';
      clearTimeout(d.__t);
      d.__t = setTimeout(function () { d.style.opacity = '0'; }, ms || 2200);
      return true;
    } catch (e) { R.log('toast 失败', String(e)); return false; }
  };

  R.storage = {
    _k: function (k) { return 'dsh-skin:' + k; },
    get: function (k, dflt) {
      try { var v = localStorage.getItem(R.storage._k(k)); return v == null ? dflt : JSON.parse(v); }
      catch (e) { return dflt; }
    },
    set: function (k, v) {
      try { localStorage.setItem(R.storage._k(k), JSON.stringify(v)); return true; } catch (e) { return false; }
    },
    remove: function (k) { try { localStorage.removeItem(R.storage._k(k)); } catch (e) { } }
  };

  /* ---- 轻量 UI 工具箱（模块与用户脚本共用） ---- */
  R.ui = {
    style: function (id, text) {
      try {
        var s = document.getElementById(id);
        if (!s) { s = document.createElement('style'); s.id = id; document.head.appendChild(s); }
        s.textContent = text || '';
        return s;
      } catch (e) { return null; }
    },
    el: function (tag, css, html) {
      try {
        var e = document.createElement(tag);
        if (css) e.style.cssText = css;
        if (html != null) e.innerHTML = html;
        return e;
      } catch (err) { return null; }
    },
    on: function (el, ev, fn) { try { if (el) el.addEventListener(ev, fn); } catch (e) { } return el; },
    remove: function (id) {
      try { var e = document.getElementById(id); if (e && e.parentNode) e.parentNode.removeChild(e); } catch (e) { }
    }
  };

  R.ready = function (cb) {
    try {
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', cb, { once: true });
      else cb();
    } catch (e) { R.log('ready 失败', String(e)); }
  };

  R.wait = function (selector, ms) {
    return new Promise(function (resolve) {
      var hit = null;
      try { hit = document.querySelector(selector); } catch (e) { }
      if (hit) return resolve(hit);
      var done = false, ob = null;
      var finish = function (el) { if (done) return; done = true; if (ob) ob.disconnect(); resolve(el || null); };
      try {
        ob = new MutationObserver(function () {
          var el = null;
          try { el = document.querySelector(selector); } catch (e) { }
          if (el) finish(el);
        });
        ob.observe(document.documentElement, { childList: true, subtree: true });
      } catch (e) { }
      setTimeout(function () { finish(null); }, ms || 15000);
    });
  };

  R.on = function (selector, cb, opts) {
    opts = opts || {};
    var run = function (el) { try { cb(el); } catch (e) { R.log('on 回调异常', String(e)); } };
    R.wait(selector, opts.timeout || 15000).then(function (el) {
      if (el) run(el);
      if (opts.once === false) {
        try {
          var ob = new MutationObserver(function () {
            try { var cur = document.querySelector(selector); if (cur && cur !== el) { el = cur; run(cur); } } catch (e) { }
          });
          ob.observe(document.documentElement, { childList: true, subtree: true });
        } catch (e) { }
      }
    });
    return R;
  };

  R.interval = function (fn, ms) {
    try {
      var id = setInterval(function () { try { fn(); } catch (e) { R.log('interval 异常', String(e)); } }, ms || 1000);
      window.addEventListener('beforeunload', function () { clearInterval(id); }, { once: true });
      return id;
    } catch (e) { return null; }
  };

  /* 注册模块：以「id + 内容哈希」去重，脚本/模块改动后会重新执行 */
  R.def = function (id, setup) {
    var key = R.__hash ? (id + '#' + R.__hash) : id;
    if (R.__def[key]) return false;
    try { setup(R); } catch (e) { R.error(id, '模块失败:', String(e)); return false; }
    R.__def[key] = true;
    R.modules[id] = true;
    R.log('模块已加载:', id);
    return true;
  };

  /* ---- 内置：桥自身 ---- */
  R.def('bridge', function () { R.log('运行时桥就绪 v' + V); });

  /* ---- 注入的模块与用户脚本（由 DSHSkin 后端生成） ---- */
__BODY__

  return { ok: true, v: V, build: R.__build, modules: Object.keys(R.modules) };
})()"""


def _js_str(s):
    """把 Python 字符串安全嵌入 JS 字面量（含 </ 转义，避免意外闭合）"""
    return json.dumps(s, ensure_ascii=False).replace('</', '<\\/')


def build_runtime_js(force=False):
    """生成注入用的 JS 运行时（含已启用的内置模块与用户脚本）。

    force=True 时忽略「已加载」去重并绕过缓存（用于强制重跑）。
    返回 (js_text, meta)；增强总开关关闭时返回 ('', meta)。

    去重策略（关键）：
      - 整个运行时按「版本 + 全部注入体」的 md5 作为构建指纹；指纹不变才走缓存。
      - 内置模块按「模块名 + 源码哈希」去重：改了模块源码，重注入就会重新执行。
      - 用户脚本按「文件名 + 源码哈希」去重：编辑脚本后重注入同样会重新执行。
    """
    st = load_state()
    meta = {"enabled": st['enabled'], "modules": dict(st['modules']),
            "scripts": [], "builtin": [], "custom_css": st['modules'].get('custom-css', False) and st['enabled']}
    if not st['enabled']:
        return '', meta

    body = []

    # 1) 内置增强模块（每个模块一个内容哈希，改动即生效）
    for key, fname, text, h in builtin_module_js():
        if not st['modules'].get(key, True):
            continue
        meta['builtin'].append({'key': key, 'file': fname, 'hash': h})
        body.append(
            "  /* ---------- 内置模块 {0} #{1} ---------- */\n"
            "  try {{\n"
            "    R.__hash = {2};\n"
            "{3}\n"
            "    R.__hash = '';\n"
            "  }} catch (e) {{ R.error({4}, '内置模块异常:', String(e)); }}\n".format(
                key, h, _js_str(h), _indent(text, 4), _js_str(key))
        )

    # 2) 用户脚本（按内容哈希去重）
    if st['modules'].get('user-scripts'):
        for item in list_files('.js'):
            if not item['enabled']:
                continue
            code = read_file(item['name'])
            if not code or not code.strip():
                continue
            meta['scripts'].append(item['name'])
            name_lit = _js_str(item['name'])
            ch = hashlib.md5(code.encode('utf-8')).hexdigest()[:12]
            if force:
                guard_open, guard_close = '', ''
            else:
                guard_open = 'if(R.__loaded[%s]===%s){}else{' % (name_lit, _js_str(ch))
                guard_close = '}'
            mark = 'R.__loaded[%s]=%s;' % (name_lit, _js_str(ch))
            body.append(
                "  try {\n"
                "    %s\n"
                "    (new Function('DSHSkin', %s))(R);\n"
                "    R.log('用户脚本已执行:', %s);\n"
                "    %s\n"
                "  } catch (e) { R.error(%s, '用户脚本异常:', String(e)); %s }\n"
                % (guard_open, _js_str(code), name_lit, guard_close, name_lit, mark)
            )

    build_seed = RUNTIME_VERSION + '\x00' + '\x00'.join(body)
    build_id = hashlib.md5(build_seed.encode('utf-8', 'ignore')).hexdigest()[:16]
    meta['build'] = build_id
    reload_expr = "R.__build = null; R.__def = {}; R.__loaded = {};" if force else ""
    js = _RUNTIME_TMPL.replace('__VERSION__', RUNTIME_VERSION) \
                       .replace('__BUILD__', build_id) \
                       .replace('__RELOAD__', reload_expr) \
                       .replace('__HOTKEYS__', json.dumps(resolve_hotkeys(st.get('hotkeys')), ensure_ascii=False)) \
                       .replace('__BODY__', '\n'.join(body) or '  /* 无内置模块 / 无用户脚本 */')
    return js, meta


def summary():
    """给面板/CLI 用的增强状态摘要"""
    st = load_state()
    have = {m['key'] for m in _BUILTIN if os.path.isfile(os.path.join(MODULES_DIR, m['file']))}
    return {
        "enabled": st['enabled'],
        "runtime_version": RUNTIME_VERSION,
        "dir": ENHANCE_DIR,
        "modules_dir": MODULES_DIR,
        "state_file": STATE_FILE,
        "modules": [dict(m, enabled=st['modules'].get(m['key'], False),
                         builtin=bool(m.get('file')),
                         present=(m['key'] in have) if m.get('file') else True)
                    for m in MODULES],
        "scripts": list_files('.js'),
        "styles": list_files('.css'),
        "hotkeys": {
            "registry": [dict(action=a, label=spec['label'], module=spec['module'],
                              default=spec['default'],
                              current=resolve_hotkeys(st.get('hotkeys'))[a])
                         for a, spec in HOTKEYS.items()],
            "custom": st.get('hotkeys', {}),
            "conflicts": detect_hotkey_conflicts(st.get('hotkeys')),
        },
    }


def apply_preset(preset):
    """一键切换预设：off / builtin / scripts / full"""
    def mods(**kw):
        return {k: bool(kw.get(k, False)) for k in _MODULE_KEYS if k != 'bridge'} | {'bridge': True}

    st = load_state()
    if preset == 'off':
        st['enabled'] = False
    elif preset == 'builtin':
        st['enabled'] = True
        st['modules'] = mods(**{m['key']: True for m in _BUILTIN})
    elif preset == 'scripts':
        st['enabled'] = True
        st['modules'] = mods(**{'user-scripts': True})
    elif preset == 'full':
        st['enabled'] = True
        st['modules'] = mods(**{k: True for k in _MODULE_KEYS if k != 'bridge'})
    else:
        raise ValueError('未知预设: {0}'.format(preset))
    return save_state(st)
