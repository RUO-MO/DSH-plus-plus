# -*- coding: utf-8 -*-
"""DSH++ 动态壁纸适配层（参考 dsh-plugin-wallpaper-engine 的接口契约实现）。

背景（2026-09-20 换肤移除后）：
  DSH++ 不再自带换肤引擎，「动态背景」改为在「动态壁纸」分区内**参考**
  `dsh-wallpaper-engine`（https://github.com/elysia395/dsh-wallpaper-engine）
  的接口契约自行实现一个适配层来对接它。本模块分「读取」与「应用」两侧：

  **读取侧 —— 不依赖 DSH（2026-09-21 架构修正）**
    1. 扫描本机 Wallpaper Engine 目录拿壁纸清单，委托 `we_scanner`
       （纯文件系统：Steam 注册表 → WE 安装目录 → 逐个 project.json）。
       清单本质是本机磁盘信息，不需要 DSH 参与，也不该受调试端口开关影响。
       此前把这一步错接在 CDP 上（借 DSH 页面同源 fetch 插件的 `/inventory`），
       导致 DSH 未开 `--remote-debugging-port` 时清单整个不可用且无降级。

  **应用侧 —— 这才涉及 DSH**
    2. 探测插件是否已装（读 DSH profile 的 node_modules）；
    3. 读 / 写插件的 settings —— 优先走插件 HTTP 路由（GET/PUT
       `/wallpaper-engine/settings`），不可达时降级直改 config.json。
       插件路由挂在 DSH 自己的 webserver 上（随机端口）且受 Host/Origin 栅栏
       保护，外部进程无法直连，故写操作仍保留 CDP 通道与文件降级两条路。

为什么写操作走 PUT 而不是直接改文件（2026-09-20 修）：
  插件在 `enqueueConfigWrite()` 里把「读-改-写三步」串行化，避免并发写吞改动。
  直接改 `~/.dsh-wallpaper-engine/config.json` 会绕过这个队列 —— 用户在插件页面
  拖滑杆的同时本工具也在写，后写者基于旧快照覆盖先写者。走 PUT 路由即复用插件
  自己的队列 + `sanitizeSettings()` 校验，且**响应即已持久化**。
  直接文件写仅作为「插件未运行 / 路由不可达」时的降级路径保留。

契约来源（2026-09-20 逐条核对插件 v0.7.3 源码 lib/index.js，非 README）：
  - `BASE = '/wallpaper-engine'`（:74）
  - 路由全挂 `ctx.webServer`，`inject = ['webServer']`（:1920）
  - `GET  /settings` → `{ settings, betterSidebar }`（:3071）
  - `PUT  /settings` → 非 PUT 一律 405（:3086）；body 为**完整 settings 对象**
    （不是 patch），服务端跑 `sanitizeSettings()` 后整对象落盘（:533 writeSettings）
  - `clampNum(v,lo,hi,fb)` = 越界**回落 fallback 而非夹紧**（:555）
    → 超出区间的值不会被"夹"到边界，而是被重置成默认值！故本模块必须自己先夹紧。
  - `buildInventory()` 返回（:2033）：
    `{installDir, uploadDir, total, portableCount, wallpapers[], playlists[]}`

零侵入：只读写插件自己的配置文件 / 路由，不改 DSH 与插件任何文件。
"""

import json
import os
import re

import dsh_env

# ---------------- 路径常量 ----------------
CONFIG_DIR = os.path.join(os.path.expanduser('~'), '.dsh-wallpaper-engine')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')
UPLOAD_DIR_DEFAULT = os.path.join(CONFIG_DIR, 'uploads')

PLUGIN_NAME = 'dsh-plugin-wallpaper-engine'
BASE_PATH = '/wallpaper-engine'
SETTINGS_ROUTE = BASE_PATH + '/settings'


# ---------------- settings 参数表 ----------------
# (key, label, min, max, step, percent)
# **区间必须与插件 sanitizeSettings() 的 clamp 完全一致**，否则本工具放行而插件
# 回落到默认值（clampNum 是越界→fallback 语义），表现为「拖了没反应」。
# 对照 lib/index.js:805 sanitizeSettings。
TUNABLE = (
    ('scrim',                 '遮罩浓度',       0.0,   1.0,   0.05, False),
    ('border',                '玻璃边框',       0.0,   1.0,   0.05, False),
    ('blur',                  '界面背景模糊',   0,     60,    2,    False),
    ('wallpaperBlur',         '壁纸模糊',       0,     60,    1,    False),
    ('wallpaperOpacity',      '壁纸不透明度',   0,     90,    5,    True),
    ('backgroundBrightness',  '亮度',           40,    160,   5,    True),
    ('backgroundContrast',    '对比度',         40,    200,   5,    True),
    ('backgroundSaturate',    '饱和度',         0,     200,   5,    True),
    ('playbackRate',          '播放倍速',       0.5,   2.0,   0.25, False),
    ('glassAlpha',            '玻璃通透度',     0,     60,    2,    True),
    ('sidebarBlur',           '侧栏模糊',       0,     200,   4,    False),
    ('sidebarAlpha',          '侧栏通透度',     0,     200,   5,    True),
    ('sidebarContentAlpha',   '侧栏文字层',     0,     80,    2,    True),
    ('ropeScale',             '吉祥物缩放',     0.5,   2.5,   0.1,  False),
    ('fontWeight',            '字重',           100,   900,   100,  False),
)

# 枚举型（下拉）参数：key → (label, 允许值, 默认值)
ENUMS = (
    ('objectFit',           '壁纸填充',   ['cover', 'contain', 'center', 'fill'], 'cover'),
    ('typeFilter',          '类型筛选',   ['all', 'video', 'web', 'image', 'scene'], 'all'),
    ('contentRatingFilter', '内容分级',   ['all', 'everyone', 'pg13', 'mature', 'unrated'], 'everyone'),
    ('pickerLayout',        '选择器布局', ['fixed', 'classic'], 'fixed'),
    ('ropeForm',            '吉祥物形态', ['maid', 'whale'], 'maid'),
    ('fontFamily',          '字体',       ['inherit', 'Microsoft YaHei', 'KaiTi',
                                           'SimSun', 'SimHei', 'STXingkai', 'monospace'], 'inherit'),
    ('fpsCap',              '解码帧率上限', [0, 60, 48, 30, 24], 0),
)

# 布尔开关：key → (label, 默认值)
TOGGLES = (
    ('rotationEnabled', '轮播',          False),
    ('pauseOnHidden',   '隐藏时暂停',    True),
    ('pauseOnBlur',     '失焦时暂停',    False),
    ('pauseOnBattery',  '电池时暂停',    False),
    ('flip',            '水平翻转',      False),
    ('edgeCompat',      'Edge 兼容',     True),
    ('glassWindow',     '玻璃窗口',      True),
    ('sidebarGlass',    '侧栏玻璃',      True),
    ('ropeShown',       '显示吉祥物',    True),
    ('fontCustom',      '自定义字体',    False),
    ('betaSceneAnim',   '场景动画(β)',   False),
)

# 颜色参数（#rrggbb / 空串）：key → (label, 默认值)
COLORS = (
    ('accent',              '强调色',      '#4f8cff'),
    ('glassColor',          '玻璃底色',    '#0d1524'),
    ('sidebarColor',        '侧栏底色',    '#ffffff'),
    ('sidebarContentColor', '侧栏文字色',  '#000000'),
    ('fontColor',           '文字颜色',    '#000000'),
    ('caretColor',          '光标颜色',    '#000000'),
)

_TUNABLE_MAP = {k: (lo, hi, step, pct) for k, _l, lo, hi, step, pct in TUNABLE}
_ENUM_MAP = {k: (vals, dflt) for k, _l, vals, dflt in ENUMS}
_TOGGLE_MAP = {k: dflt for k, _l, dflt in TOGGLES}
_COLOR_MAP = {k: dflt for k, _l, dflt in COLORS}
_COLOR_RE = re.compile(r'^#[0-9a-f]{6}$', re.I)

# 面板可写的全部键（其余键一律不动 —— 尤其是 rotationGroups / hiddenIds /
# noticeSeen / rotationSeeded 这些插件内部维护的复杂结构）
_WRITABLE = (set(_TUNABLE_MAP) | set(_ENUM_MAP) | set(_TOGGLE_MAP) | set(_COLOR_MAP)
             | {'id'})


# ---------------- 插件探测 ----------------
def _profiles_root():
    """DSH home 下的 profiles 目录（desktop / web 两个 profile 各自装插件）。"""
    home, src = dsh_env.dsh_home()
    if not home:
        return None, src
    p = os.path.join(home, 'profiles')
    return (p if os.path.isdir(p) else None), src


def plugin_status(profile='desktop'):
    """探测 wallpaper-engine 插件安装状态。

    返回 {installed, version, profile, plugin_dir, profiles:[...], config, upload_dir}
    - installed：该 profile 的 node_modules 里存在插件目录
    - version：读插件 package.json 的 version
    - profiles：所有含该插件的 profile 名（通常只有 desktop）
    """
    out = {'installed': False, 'version': None, 'profile': profile,
           'plugin_dir': None, 'profiles': [],
           'config': CONFIG_FILE, 'config_exists': os.path.isfile(CONFIG_FILE),
           'upload_dir': UPLOAD_DIR_DEFAULT}
    root, src = _profiles_root()
    out['dsh_home_source'] = src
    out['profiles_root'] = root
    if not root:
        return out

    found = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        pdir = os.path.join(root, name, 'node_modules', PLUGIN_NAME)
        if not os.path.isdir(pdir):
            continue
        found.append(name)
        if name == profile or out['plugin_dir'] is None:
            out['profile'] = name
            out['plugin_dir'] = pdir
            out['installed'] = True
            out['version'] = _plugin_version(pdir)
    out['profiles'] = found
    if not found:
        out['installed'] = False
    return out


def _plugin_version(plugin_dir):
    try:
        with open(os.path.join(plugin_dir, 'package.json'), encoding='utf-8') as f:
            return json.load(f).get('version')
    except Exception:
        return None


# ---------------- config.json 直读（降级路径 + 只读展示）----------------
def _read_root():
    """读 config.json 全量（含 settings / uploadDir 等顶层键）。"""
    try:
        with open(CONFIG_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_settings():
    """读插件 settings（未保存过返回 {}）。"""
    s = _read_root().get('settings')
    return s if isinstance(s, dict) else {}


# ---------------- 经 CDP 同源调插件路由 ----------------
def _cdp_origin():
    """从当前 CDP 页面 URL 解析 DSH 页面 origin（形如 http://127.0.0.1:PORT）。"""
    url = None
    try:
        t = dsh_env.cdp_page_target()
        url = (t or {}).get('url') if isinstance(t, dict) else None
    except Exception:
        url = None
    if not url:
        return None
    m = re.match(r'^(https?://[^/]+)', url)
    return m.group(1) if m else None


def _cdp_request(method, path, body=None, timeout=8.0):
    """借 CDP 在 DSH 页面上下文内发起同源 XHR。

    插件路由挂在 DSH 自己的 webserver 上（随机端口），外部进程端口未知，
    因此必须借页面上下文发起同源请求。

    返回 {'ok':True,'status':int,'data':obj|str} 或 {'ok':False,'err':str}。
    """
    import cdp_skin
    if not cdp_skin.WEBSOCKET_AVAILABLE:
        return {'ok': False, 'err': '缺少 websocket-client（python dsh-skin.py deps --install）'}
    if not cdp_skin.cdp_ready():
        return {'ok': False, 'err': 'DSH 未运行或未开调试端口'}
    target = cdp_skin.find_page_target()
    if not target:
        return {'ok': False, 'err': '未找到 DSH 页面'}

    body_js = 'null' if body is None else json.dumps(json.dumps(body))
    expr = (
        "(function(){try{"
        "var x=new XMLHttpRequest();"
        "x.open(" + json.dumps(method.upper()) + "," + json.dumps(path) + ",false);"
        "var payload=" + body_js + ";"
        "if(payload!==null){x.setRequestHeader('Content-Type','application/json');}"
        "x.send(payload);"
        "return JSON.stringify({__s:x.status,__b:x.responseText});"
        "}catch(e){return JSON.stringify({__err:String(e&&e.message||e)});}})()"
    )
    try:
        cdp = cdp_skin.CDP(target['webSocketDebuggerUrl'], timeout=timeout)
        try:
            r = cdp.eval(expr)
        finally:
            cdp.close()
    except Exception as e:
        return {'ok': False, 'err': 'CDP 调用失败: {0}'.format(e)}
    if not r.get('ok'):
        return {'ok': False, 'err': r.get('err') or '页面求值失败'}
    try:
        raw = json.loads(r['value'])
    except Exception:
        return {'ok': False, 'err': '路由返回非 JSON'}
    if isinstance(raw, dict) and '__err' in raw:
        return {'ok': False, 'err': raw['__err']}
    if not isinstance(raw, dict):
        return {'ok': False, 'err': '路由返回结构异常'}
    status = raw.get('__s')
    text = raw.get('__b') or ''
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = text
    if status != 200:
        detail = ''
        if isinstance(data, dict):
            detail = data.get('error') or data.get('msg') or ''
        return {'ok': False, 'status': status,
                'err': 'HTTP {0}{1}'.format(status, (' ' + str(detail)) if detail else ''),
                'data': data}
    return {'ok': True, 'status': status, 'data': data}


# ---- settings 专用封装 ----
def fetch_settings(timeout=8.0):
    """GET /wallpaper-engine/settings → {'settings': {...}, 'betterSidebar': bool}。

    这是插件的权威 settings（含客户端 localStorage 之外的持久化副本）。
    失败时返回原始 settings 供降级展示，并在 _route_err 里说明原因。
    """
    res = _cdp_request('GET', SETTINGS_ROUTE, timeout=timeout)
    if not res.get('ok'):
        st = load_settings()
        return {'settings': st, 'betterSidebar': None, 'source': 'file',
                'route_err': res.get('err')}
    data = res.get('data') or {}
    s = data.get('settings') if isinstance(data, dict) else None
    if not isinstance(s, dict):
        s = load_settings()
    return {'settings': s, 'betterSidebar': data.get('betterSidebar'),
            'source': 'route'}


def put_settings(settings, timeout=8.0):
    """PUT /wallpaper-engine/settings（body = 完整 settings 对象）。

    插件会跑 sanitizeSettings() 后整对象落盘，响应即已持久化。
    返回 (ok:bool, payload_or_err)。
    """
    return _cdp_request('PUT', SETTINGS_ROUTE, body=settings, timeout=timeout)


# ---------------- 参数校验 ----------------
def _coerce(key, value):
    """按插件 sanitizeSettings 的语义校验单个值。

    关键：插件用 `clampNum(v,lo,hi,fallback)`，**越界回落 fallback 而非夹紧**。
    所以我们先自己夹紧到 [lo,hi]，避免把「越界值」发给插件而被重置成默认。
    返回 (ok, coerced) ；ok=False 表示该键不接受此值（调用方跳过）。
    """
    if key in _TUNABLE_MAP:
        lo, hi, _step, _pct = _TUNABLE_MAP[key]
        try:
            num = float(value)
        except (TypeError, ValueError):
            return False, None
        if num != num:                      # NaN
            return False, None
        num = min(hi, max(lo, num))         # 主动夹紧，别依赖插件的 fallback
        # 保持整数参数为 int（插件的 typeof v === 'number' 两者都收，但
        # 整数键存成 int 更贴近插件自身行为）
        if float(num).is_integer() and float(lo).is_integer() and float(hi).is_integer():
            return True, int(num)
        return True, num

    if key in _ENUM_MAP:
        vals, dflt = _ENUM_MAP[key]
        if value in vals:
            return True, value
        return False, None

    if key in _TOGGLE_MAP:
        if isinstance(value, bool):
            return True, value
        if value in (0, 1):
            return True, bool(value)
        if isinstance(value, str) and value.lower() in ('true', 'false'):
            return True, value.lower() == 'true'
        return False, None

    if key in _COLOR_MAP:
        if isinstance(value, str) and (value == '' or _COLOR_RE.match(value)):
            return True, value
        return False, None

    if key == 'id':
        if isinstance(value, str) and len(value) <= 128:
            return True, value
        return False, None

    return False, None


def save_settings(patch):
    """把 patch 合并进插件 settings 并持久化。

    流程（2026-09-20 改）：
      1. 取**插件权威** settings（GET 路由；失败则降级读 config.json）；
      2. 合并 patch（白名单 + 区间/枚举校验），未列出的键保持不变；
      3. **PUT 完整对象**给插件 —— 复用它的 enqueueConfigWrite 串行队列 +
         sanitizeSettings 校验，响应即已持久化；
      4. PUT 不可达时降级为直接改 config.json（同样原子写）。

    返回 (saved_settings, changed_keys, result)
      result: {'via': 'route'|'file'|'none', 'err': str|None}
    """
    cur = {}
    src = fetch_settings()
    cur.update(src.get('settings') or {})

    changed = []
    for k, v in (patch or {}).items():
        if k not in _WRITABLE:
            continue
        ok, val = _coerce(k, v)
        if not ok:
            continue
        if cur.get(k) != val:
            cur[k] = val
            changed.append(k)

    if not changed:
        return cur, [], {'via': 'none', 'err': None}

    res = put_settings(cur)
    if res.get('ok'):
        data = res.get('data') or {}
        saved = data.get('settings') if isinstance(data, dict) else None
        # 插件可能因校验调整了值，以它回显的为准
        return (saved if isinstance(saved, dict) else cur), changed, {'via': 'route', 'err': None}

    # 降级：插件路由不可达（DSH 没开调试口 / 插件未加载）时直接改文件
    err = res.get('err') or 'PUT 失败'
    root = _read_root()
    root['settings'] = cur
    try:
        _write_root(root)
    except OSError as e:
        return cur, changed, {'via': 'none', 'err': '{0}；文件降级写入也失败: {1}'.format(err, e)}
    return cur, changed, {'via': 'file', 'err': err}


def _write_root(root):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_FILE + '.dshpp.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(root, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONFIG_FILE)


# ---------------- inventory ----------------
def fetch_inventory(timeout=8.0, source='local'):
    """取壁纸清单。

    **读取侧已改为本地磁盘扫描（2026-09-21）**：壁纸清单是纯文件系统信息
    （遍历 Steam 目录 + 解析各 project.json），不需要 DSH 参与。此前经 CDP
    借页面同源请求插件 `/wallpaper-engine/inventory` 路由，导致 DSH 未开启
    `--remote-debugging-port` 时清单整个不可用且**没有任何降级**（settings 有、
    inventory 没有）。现由 `we_scanner` 直接读盘，与 DSH 是否运行无关。

    :param timeout: 仅 `source='plugin'` 时使用（CDP 请求超时）
    :param source: `'local'`（默认，本地扫描）或 `'plugin'`（经 CDP 拉插件
        路由 —— 保留用于与插件结果逐项对照排查，非默认路径）
    :returns: `{'ok': True, 'inventory': {...}, 'via': 'local'|'plugin'}`
        或 `{'ok': False, 'err': str}`
    """
    if source == 'plugin':
        return _fetch_inventory_via_plugin(timeout=timeout)
    try:
        import we_scanner
        inventory = we_scanner.scan_inventory()
    except Exception as exc:                     # 扫描失败也要给可读原因
        return {'ok': False, 'err': '本地扫描壁纸目录失败：{0}'.format(exc)}
    return {'ok': True, 'inventory': inventory, 'via': 'local'}


def _fetch_inventory_via_plugin(timeout=8.0):
    """经 CDP 同源请求插件的 inventory 路由（保留路径，用于结果对照）。

    插件路由挂在 DSH 自己的 webserver 上（端口随机），且受 Host/Origin
    反 DNS-rebinding 栅栏保护，外部进程无法直连 —— 只能借页面上下文发起。
    """
    res = _cdp_request('GET', BASE_PATH + '/inventory', timeout=timeout)
    if not res.get('ok'):
        return {'ok': False, 'err': res.get('err')}
    data = res.get('data')
    if not isinstance(data, dict):
        return {'ok': False, 'err': 'inventory 结构异常'}
    return {'ok': True, 'inventory': data, 'via': 'plugin'}


def _media_url(rel):
    """把插件给的相对 URL（/wallpaper-engine/media/xxx）拼成绝对同源 URL。"""
    if not rel:
        return None
    if rel.startswith('http://') or rel.startswith('https://'):
        return rel
    origin = _cdp_origin()
    if not origin:
        return rel
    return origin + (rel if rel.startswith('/') else '/' + rel)


def inventory_payload(fetch=True):
    """面板用：插件状态 + settings + （可选）本机壁纸清单。

    注意 `inventory` **不以插件是否安装为前提** —— 壁纸清单来自本机 Steam 上的
    Wallpaper Engine，读取与插件无关（2026-09-21 架构修正）。`plugin.installed`
    只表示 DSH 侧是否装有该插件，影响的是「应用」而非「读取」。
    """
    st = plugin_status()
    src = fetch_settings() if fetch else {'settings': load_settings(), 'source': 'file'}
    settings = src.get('settings') or {}
    out = {
        'plugin': st,
        'settings': settings,
        'settings_source': src.get('source'),
        'betterSidebar': src.get('betterSidebar'),
        'tunables': [{'key': k, 'label': l, 'min': lo, 'max': hi,
                      'step': step, 'percent': pct}
                     for k, l, lo, hi, step, pct in TUNABLE],
        'enums': [{'key': k, 'label': l, 'values': vals, 'default': dflt}
                  for k, l, vals, dflt in ENUMS],
        'toggles': [{'key': k, 'label': l, 'default': dflt}
                    for k, l, dflt in TOGGLES],
        'colors': [{'key': k, 'label': l, 'default': dflt}
                   for k, l, dflt in COLORS],
        'note': '壁纸清单直接扫描本机 Wallpaper Engine 目录；参数读写对接 {0}'.format(PLUGIN_NAME),
    }
    if src.get('route_err'):
        out['settings_route_err'] = src['route_err']
    if fetch:
        res = fetch_inventory()
        if not res.get('ok'):
            out['inventory'] = None
            out['inventory_err'] = res.get('err')
        else:
            out['inventory'] = _slim_inventory(res['inventory'])
            out['inventory_via'] = res.get('via')
    else:
        out['inventory'] = None
    return out


def _slim_inventory(inv):
    """瘦身 inventory：只留面板需要的字段。

    媒体 URL 的处理取决于清单来源：
      - **本地扫描**（`source == 'local'`）：已是本工具的
        `/api/wallpaper/asset/<kind>/<id>` 相对路径，与后端同源，保持原样；
      - **插件路由**：是 `/wallpaper-engine/<seg>/<token>`，token 只存在于插件
        进程内存里，必须借 CDP 页面 origin 绝对化才能被面板加载。
    """
    if not isinstance(inv, dict):
        return None
    local = inv.get('source') == 'local'
    resolve = (lambda value: value) if local else _media_url
    walls = []
    for w in (inv.get('wallpapers') or []):
        if not isinstance(w, dict):
            continue
        walls.append({
            'id': w.get('id'),
            'title': w.get('title') or w.get('id'),
            'type': w.get('type'),
            'contentrating': w.get('contentrating'),
            'playable': bool(w.get('playable')),
            'preview': resolve(w.get('preview')),
            'media': resolve(w.get('media')),
            'frameUrl': resolve(w.get('frameUrl')),
            # Scene 类型专属：实时 WebGL 播放器 + 抽帧出来的 MP4
            'sceneUrl': resolve(w.get('sceneUrl')),
            'sceneVideo': resolve(w.get('sceneVideo')),
        })
    return {
        'installDir': inv.get('installDir'),
        'uploadDir': inv.get('uploadDir'),
        'total': inv.get('total'),
        'portableCount': inv.get('portableCount'),
        'wallpapers': walls,
        'playlists': inv.get('playlists') or [],
        # 本地扫描会带上来源与诊断信息，透传给面板便于排查
        'source': inv.get('source'),
        'details': inv.get('details'),
    }


# ---------------- 激活状态 ----------------
def active_wallpaper():
    """当前激活的壁纸条目（按 settings.id 在 inventory 里查）。无则返回 None。

    注意：settings.id 就是 wallpapers[].id（插件 buildInventory 的 w.id），
    上传壁纸的 id 形如 'up-xxxx'。
    """
    sid = (fetch_settings().get('settings') or {}).get('id') \
        or load_settings().get('id')
    if not sid:
        return None
    res = fetch_inventory()
    if not res.get('ok'):
        return {'id': sid, 'title': None, 'preview': None, 'resolved': False,
                'err': res.get('err')}
    for w in (res['inventory'].get('wallpapers') or []):
        if isinstance(w, dict) and w.get('id') == sid:
            w = dict(w)
            # 本地扫描给的已是同源相对路径，不要再拼 CDP origin
            if res.get('via') != 'local':
                w['preview'] = _media_url(w.get('preview'))
            w['resolved'] = True
            return w
    return {'id': sid, 'title': None, 'resolved': False, 'preview': None}
