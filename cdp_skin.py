# -*- coding: utf-8 -*-
"""
CDP 注入引擎 —— 通过 Chrome DevTools Protocol 把皮肤 CSS / 增强 JS 注入运行中的 DSH。

设计原则：
- 不碰 DSH 任何文件，只通过 CDP 向渲染进程注入 <style> / 运行时。
- 连接走 127.0.0.1 本地端口；WebSocket 必须 suppress_origin（CDP 不接受 Origin 头）。
- 多窗口覆盖：枚举所有匹配 DSH 的 page target，全部注入；新窗口由守护线程自动补。
- 防裸 UI 闪烁：对每个 target 建立持久会话并用
  Page.addScriptToEvaluateOnNewDocument 注册「自举脚本」，配合 localStorage 缓存，
  让页面刷新/首绘最早期就恢复皮肤，而不是等轮询补注。
"""
import json
import socket
import subprocess
import threading
import time
import urllib.request

try:
    from websocket import create_connection
    WEBSOCKET_AVAILABLE = True
except Exception:  # pragma: no cover
    WEBSOCKET_AVAILABLE = False

DEFAULT_PORT = 9222
# DSH 桌面渲染页标识（dsh-app 协议 / 本地 dev 端口）
PAGE_MARK = 'dsh-app'
# localStorage 中缓存「最近一次注入包」的键，供新文档自举读取，消除刷新裸窗
BOOT_CACHE_KEY = '__DSHSKIN_BOOT__'
STYLE_ID = 'dsh-skin-cdp'
RUNTIME_FLAG = 'data-dsh-skin-cdp'

# 新文档最早期执行的静态自举脚本：从 localStorage 读上次注入包，立即落 <style> + 跑运行时。
# 该脚本本身不含主题内容（内容在 localStorage 缓存里），因此只需对每个 target 注册一次。
BOOTSTRAP_JS = r"""
(function(){
  try{
    // 全局错误收集：即便 DSHSkin 运行时尚未注入，未捕获异常也先存起来，供后端回收
    window.__DSHSKIN_ERR__ = window.__DSHSKIN_ERR__ || [];
    if(!window.__DSHSKIN_ERR_HOOK__){
      window.__DSHSKIN_ERR_HOOK__ = 1;
      var pushErr = function(kind, msg, src, line, col){
        try{
          var arr = window.__DSHSKIN_ERR__;
          arr.push({t:Date.now(), lv:'error', mod:'page', m:kind+': '+(msg||'')+
            (src?(' @'+String(src).split('/').pop()+':'+line):'')});
          if(arr.length>100) arr.splice(0, arr.length-100);
        }catch(e){}
      };
      window.addEventListener('error', function(e){ pushErr('未捕获异常', e.message, e.filename, e.lineno, e.colno); });
      window.addEventListener('unhandledrejection', function(e){
        var r = e.reason; pushErr('未处理Promise', r && (r.stack || r.message || String(r)));
      });
    }
    var KEY='__DSHSKIN_BOOT__';
    function applyNow(){
      var raw=null;
      try{ raw=localStorage.getItem(KEY); }catch(e){ return; }
      if(!raw) return;
      var B; try{ B=JSON.parse(raw); }catch(e){ return; }
      var root=document.documentElement; if(!root) return;
      var s=document.getElementById('dsh-skin-cdp');
      if(!s && B.css){
        s=document.createElement('style'); s.id='dsh-skin-cdp';
        s.setAttribute('data-dsh-skin-cdp', B.hash||'boot'); s.textContent=B.css;
        (document.head||root).appendChild(s);
      }
      if(B.js){ try{ (window.eval)(B.js); }catch(e){} }
    }
    applyNow();
    // head 在自举时刻可能尚未形成；DOMContentLoaded 时把 style 归位到 head
    document.addEventListener('DOMContentLoaded', function(){
      var s=document.getElementById('dsh-skin-cdp');
      if(s && document.head && s.parentNode!==document.head){ document.head.appendChild(s); }
    });
  }catch(e){}
})();
""".strip()


# ---------------- 端口 / 进程检测 ----------------
_PORT_CACHE = {'ts': 0.0, 'port': None, 'value': None}
_PORT_TTL = 3.0


def port_open(port=DEFAULT_PORT, timeout=0.2):
    now = time.time()
    if (_PORT_CACHE['port'] == port and now - _PORT_CACHE['ts'] < _PORT_TTL
            and _PORT_CACHE['value'] is not None):
        return _PORT_CACHE['value']
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(('127.0.0.1', int(port)))
        value = True
    except OSError:
        value = False
    finally:
        s.close()
    _PORT_CACHE['ts'] = now
    _PORT_CACHE['port'] = port
    _PORT_CACHE['value'] = value
    return value


def http_json(url, timeout=0.8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


# target 列表探测短缓存：一次状态计算会多次调用 find_page_target/cdp_ready，
# 端口不可用时每次 HTTP 探测都要等超时；1.5s 内复用结果，把 N 次等待合并成 1 次。
_TARGETS_CACHE = {'ts': 0.0, 'port': None, 'value': None}
_TARGETS_TTL = 3.0


def invalidate_probe_cache():
    """注入/重启等状态变更后调用，强制下一次重新探测。"""
    _TARGETS_CACHE['ts'] = 0.0
    _TARGETS_CACHE['port'] = None
    _TARGETS_CACHE['value'] = None
    _PORT_CACHE['ts'] = 0.0
    _PORT_CACHE['port'] = None
    _PORT_CACHE['value'] = None


def _targets(port=DEFAULT_PORT):
    now = time.time()
    if (_TARGETS_CACHE['port'] == port and _TARGETS_CACHE['value'] is not None
            and now - _TARGETS_CACHE['ts'] < _TARGETS_TTL):
        return _TARGETS_CACHE['value']
    # 端口快速闸门：未监听时 connect 会被 DROP，直接 HTTP 要硬等超时，先 0.4s 判活
    if not port_open(port):
        value = []
        _TARGETS_CACHE['ts'] = now
        _TARGETS_CACHE['port'] = port
        _TARGETS_CACHE['value'] = value
        return value
    try:
        data = http_json('http://127.0.0.1:%d/json' % int(port))
        value = [t for t in data if t.get('type') == 'page' and t.get('webSocketDebuggerUrl')]
    except Exception:
        value = []
    _TARGETS_CACHE['ts'] = now
    _TARGETS_CACHE['port'] = port
    _TARGETS_CACHE['value'] = value
    return value


def find_page_target(port=DEFAULT_PORT):
    """返回第一个匹配 DSH 的 page target（供单目标状态探测用）。"""
    ts = find_page_targets(port)
    return ts[0] if ts else None


def find_page_targets(port=DEFAULT_PORT):
    """返回所有匹配 DSH 的 page target（多窗口全覆盖）。

    识别规则（适配官方与新的 dsh-plugin-desktop 社区壳）：
    - 优先 dsh-app 协议标记（旧开发/老打包版）；
    - file://native-ui/… 是新壳的原生对话框/启动窗口，不作为注入目标；
    - http://127.0.0.1 / http://localhost 的 loopback 同源页面（新壳把 Web 界面跑在
      临时端口上）视为 DSH 主界面与同源对话框，全部纳入；
    - 若无以上可识别页面且恰好只有一个 page，兜底用它（dev 早期 url 可能是 about:blank）；
    - 多个无标记 page 时不猜，避免误注入无关页面。
    """
    pages = _targets(port)
    marked = [t for t in pages if PAGE_MARK in str(t.get('url', ''))]
    if marked:
        return marked
    app_pages = [t for t in pages if _is_dsh_app_target(t)]
    if app_pages:
        return app_pages
    return pages if len(pages) == 1 else []


def _is_dsh_app_target(t):
    """按 URL 判断是否为应注入的 DSH 应用页（排除新壳原生对话框 / devtools 等）。"""
    url = str(t.get('url', ''))
    if url.startswith('dsh-app://'):
        return True
    if url.startswith('file://'):
        return 'native-ui' not in url
    if url.startswith('http://127.0.0.1:') or url.startswith('http://localhost:'):
        return True
    return False


def find_any_page(port=DEFAULT_PORT):
    pages = _targets(port)
    return pages[0] if pages else None


def cdp_ready(port=DEFAULT_PORT):
    return WEBSOCKET_AVAILABLE and port_open(port) and find_page_target(port) is not None


def launch_dsh(dsh_root, port=DEFAULT_PORT):
    """经项目自带 start-desktop.cmd 启动 DSH 桌面版（dev 模式自带调试端口）。"""
    import os
    candidates = [
        os.path.join(dsh_root, 'start-desktop.cmd'),
        os.path.join(dsh_root, 'scripts', 'start-desktop.cmd'),
    ]
    cmd = next((c for c in candidates if os.path.isfile(c)), None)
    if not cmd:
        return {'ok': False, 'err': '未找到 start-desktop.cmd（已查: %s）' % ', '.join(candidates)}
    try:
        subprocess.Popen(['cmd', '/c', cmd], cwd=dsh_root,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                         | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    except OSError as e:
        return {'ok': False, 'err': str(e)}
    for _ in range(60):
        if port_open(port) and find_page_target(port):
            return {'ok': True}
        time.sleep(1)
    return {'ok': False, 'err': '已执行启动脚本，但 60s 内 CDP 端口未就绪'}


# ---------------- 哈希 ----------------
def bundle_hash(css_text, js_text=''):
    import hashlib
    return hashlib.sha1((css_text + '\x00' + js_text).encode('utf-8', 'replace')).hexdigest()[:12]


# ---------------- CDP 连接（可作为持久会话复用） ----------------
class CDP:
    def __init__(self, ws_url, timeout=30):
        self.ws = create_connection(ws_url, timeout=timeout, suppress_origin=True)
        self.ws.settimeout(timeout)
        self._id = 0
        self.script_ids = []  # addScriptToEvaluateOnNewDocument 注册的 identifier

    def cmd(self, method, params=None):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
        # 长连会收到浏览器主动推的事件，跳过非本 id 的消息
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get('id') == mid:
                return msg

    def eval(self, expr, await_promise=False):
        params = {'expression': expr, 'returnByValue': True}
        if await_promise:
            params['awaitPromise'] = True
        r = self.cmd('Runtime.evaluate', params)
        if 'error' in r:
            return {'ok': False, 'err': r['error'].get('message', 'CDP error')}
        res = r.get('result', {}).get('result', {})
        if res.get('subtype') == 'error':
            return {'ok': False, 'err': res.get('description', 'runtime error')}
        return {'ok': True, 'value': res.get('value')}

    def prepare_for_new_document(self):
        """Page/Runtime enable 并注册自举脚本（幂等：先清旧注册再注册一次）。"""
        self.cmd('Page.enable')
        self.cmd('Runtime.enable')
        for ident in self.script_ids:
            try:
                self.cmd('Page.removeScriptToEvaluateOnNewDocument', {'identifier': ident})
            except Exception:
                pass
        self.script_ids = []
        r = self.cmd('Page.addScriptToEvaluateOnNewDocument', {'source': BOOTSTRAP_JS})
        ident = r.get('result', {}).get('identifier')
        if ident:
            self.script_ids.append(ident)
        return ident

    def alive(self):
        try:
            return self.ws.connected
        except Exception:
            return False

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# ---------------- 持久会话池：每个 DSH target 一条长连 ----------------
class TargetSessions:
    """维护 targetId → CDP 长连；新窗口自动建连并注册自举脚本，关闭的窗口回收连接。"""
    def __init__(self, port=DEFAULT_PORT):
        self.port = port
        self._sess = {}          # targetId -> CDP
        self._meta = {}          # targetId -> target dict
        self._lock = threading.RLock()

    def _open(self, target):
        cdp = CDP(target['webSocketDebuggerUrl'])
        cdp.prepare_for_new_document()
        return cdp

    def sync(self, targets):
        """对齐当前 target 列表，返回 {targetId: cdp}（只含存活会话）。"""
        if not WEBSOCKET_AVAILABLE:
            return {}
        with self._lock:
            want = {t['id']: t for t in targets}
            # 回收已消失的 target
            for tid in list(self._sess):
                if tid not in want or not self._sess[tid].alive():
                    self._sess.pop(tid).close()
                    self._meta.pop(tid, None)
            # 建立新 target 长连
            for tid, t in want.items():
                if tid not in self._sess:
                    try:
                        self._sess[tid] = self._open(t)
                        self._meta[tid] = t
                    except Exception:
                        continue
            return dict(self._sess)

    def all(self):
        with self._lock:
            return dict(self._sess)

    def close_all(self):
        with self._lock:
            for c in self._sess.values():
                c.close()
            self._sess.clear()
            self._meta.clear()


# 模块级单例：主动注入与守护线程共用同一批长连，保证 addScript 注册持续有效
_SESSIONS = TargetSessions(DEFAULT_PORT)


def _cache_boot_expr(css_text, js_text, h):
    """把当前注入包写入页面 localStorage，供刷新后自举脚本读取（防闪烁）。"""
    payload = json.dumps({'css': css_text, 'js': js_text, 'hash': h})
    return "try{localStorage.setItem(%s,%s);}catch(e){}" % (
        json.dumps(BOOT_CACHE_KEY), json.dumps(payload))


def _clear_boot_expr():
    return "try{localStorage.removeItem(%s);}catch(e){}" % json.dumps(BOOT_CACHE_KEY)


# ---------------- 注入 / 移除 ----------------
_LANDMARKS_JS = r"""
(function(){
  var q=function(s){return document.querySelector(s);};
  var n=function(s){return document.querySelectorAll(s).length;};
  return JSON.stringify({
    slots:n('[data-slot]'),
    phase:n('[data-phase]'),
    composer:n('[data-composer-seat]'),
    scroll:n('[data-conversation-scroll]'),
    overlay:n('[data-shell-overlay]'),
    side:n('[data-side]'),
    rightbar:n('[data-rightbar-col]'),
    title:(document.title||'').slice(0,60)
  });
})()
"""


def _page_state_one(cdp):
    r = cdp.eval("(function(){var s=document.getElementById('dsh-skin-cdp');"
                 "return JSON.stringify({style:!!s,rt:!!window.DSHSkin,"
                 "body:document.body?document.body.hasAttribute('data-dsh-skin-cdp'):false,"
                 "hash:s?s.getAttribute('data-dsh-skin-cdp'):null});})()")
    if not r.get('ok'):
        return {'ok': False, 'err': r.get('err')}
    try:
        d = json.loads(r['value'])
        # 兼容字段：injected/runtime/hash（server 与面板沿用）
        d['injected'] = d.get('style')
        d['runtime'] = d.get('rt')
        d['ok'] = True
        return d
    except Exception:
        return {'ok': False, 'err': 'bad state payload'}


def _inject_one(cdp, css_text, js_text, h):
    """向单条会话注入完整包：写自举缓存 → 落 style → 跑运行时 → body 打标。"""
    cdp.eval(_cache_boot_expr(css_text, js_text, h))
    css_expr = (
        "(function(){var s=document.getElementById('dsh-skin-cdp');"
        "if(!s){s=document.createElement('style');s.id='dsh-skin-cdp';(document.head||document.documentElement).appendChild(s);}"
        "s.setAttribute('data-dsh-skin-cdp',%s);s.textContent=%s;return s.textContent.length;})()"
    ) % (json.dumps(h), json.dumps(css_text))
    r = cdp.eval(css_expr)
    css_len = r.get('value') if r.get('ok') else 0
    js_len = 0
    if js_text.strip():
        rj = cdp.eval(js_text)
        if rj.get('ok'):
            js_len = len(js_text)
    cdp.eval("document.body&&document.body.setAttribute(%s,%s)" %
             (json.dumps(RUNTIME_FLAG), json.dumps(h)))
    lm = cdp.eval(_LANDMARKS_JS)
    landmarks = {}
    if lm.get('ok'):
        try:
            landmarks = json.loads(lm['value'])
        except Exception:
            landmarks = {}
    return {'ok': True, 'cssLen': css_len or 0, 'jsLen': js_len, 'landmarks': landmarks}


def inject_bundle(css_text, js_text='', port=DEFAULT_PORT):
    """向所有 DSH target 注入。聚合结果平铺首个 target 字段并附 injected_count/targets。"""
    if not WEBSOCKET_AVAILABLE:
        return {'ok': False, 'err': '缺少 websocket-client（pip install websocket-client）'}
    if not port_open(port):
        return {'ok': False, 'err': 'CDP 端口 %d 未开启（DSH 是否以 dev/桌面方式运行？）' % port}
    targets = find_page_targets(port)
    if not targets:
        return {'ok': False, 'err': 'CDP 在，但没找到 DSH 页面 target'}
    _SESSIONS.port = port
    sessions = _SESSIONS.sync(targets)
    h = bundle_hash(css_text, js_text)
    results, per = [], {}
    first = None
    for t in targets:
        tid = t['id']
        cdp = sessions.get(tid)
        if cdp is None:
            per[tid] = {'ok': False, 'err': '会话未建立'}
            continue
        try:
            one = _inject_one(cdp, css_text, js_text, h)
            per[tid] = one
            results.append(one.get('ok'))
            if first is None and one.get('ok'):
                first = one
        except Exception as e:
            per[tid] = {'ok': False, 'err': str(e)}
            # 连接失效则丢弃，下轮 sync 重连
            with _SESSIONS._lock:
                old = _SESSIONS._sess.pop(tid, None)
            if old:
                old.close()
    ok_count = sum(1 for v in per.values() if v.get('ok'))
    if ok_count == 0:
        return {'ok': False, 'err': '全部 target 注入失败', 'targets': per}
    base = first or {'ok': True, 'cssLen': 0, 'jsLen': 0, 'landmarks': {}}
    base.update({'ok': True, 'hash': h, 'injected_count': ok_count,
                 'target_count': len(targets), 'targets': per})
    return base


def inject_css(css_text, port=DEFAULT_PORT):
    return inject_bundle(css_text, '', port)


def remove_css(port=DEFAULT_PORT):
    """从所有 target 移除皮肤/运行时并清自举缓存。"""
    if not WEBSOCKET_AVAILABLE or not port_open(port):
        return {'ok': False, 'err': 'CDP 未就绪'}
    targets = find_page_targets(port)
    if not targets:
        return {'ok': False, 'err': '没找到 DSH 页面 target'}
    _SESSIONS.port = port
    sessions = _SESSIONS.sync(targets)
    expr = (
        "(function(){var s=document.getElementById('dsh-skin-cdp');var a=s?s.textContent.length:0;"
        "if(s)s.remove();"
        "try{window.DSHSkin&&DSHSkin.destroy&&DSHSkin.destroy();}catch(e){}"
        "document.body&&document.body.removeAttribute('data-dsh-skin-cdp');"
        + _clear_boot_expr() + ";"
        "return a;})()"
    )
    n = 0
    for t in targets:
        cdp = sessions.get(t['id'])
        if cdp is None:
            continue
        try:
            r = cdp.eval(expr)
            if r.get('ok'):
                n += 1
        except Exception:
            continue
    return {'ok': n > 0, 'removed_count': n, 'target_count': len(targets)}


def page_state(port=DEFAULT_PORT):
    """聚合所有 target 的注入状态。"""
    if not WEBSOCKET_AVAILABLE:
        return {'ok': False, 'err': 'no websocket-client'}
    if not port_open(port):
        return {'ok': False, 'err': 'port closed'}
    t = find_page_target(port)
    if not t:
        return {'ok': False, 'err': 'no DSH target'}
    try:
        cdp = CDP(t['webSocketDebuggerUrl'])
        st = _page_state_one(cdp)
        lm = cdp.eval(_LANDMARKS_JS)
        if lm.get('ok'):
            try:
                st['landmarks'] = json.loads(lm['value'])
            except Exception:
                pass
        cdp.close()
        return st
    except Exception as e:
        return {'ok': False, 'err': str(e)}


def page_alive(port=DEFAULT_PORT):
    return cdp_ready(port)


def collect_page_logs(port=DEFAULT_PORT, since=0):
    """回收所有 DSH target 内 DSHSkin.logs 与全局错误（t>since），按时间排序聚合。

    让面板能看到「哪个增强模块/用户脚本在页面里报了什么错」——server 侧日志看不到页面异常。
    """
    since = int(since or 0)
    if not WEBSOCKET_AVAILABLE or not port_open(port):
        return []
    targets = find_page_targets(port)
    if not targets:
        return []
    sessions = _SESSIONS.sync(targets)
    out = []
    expr = (
        "(function(){var out=[];"
        "var R=window.DSHSkin;if(R&&R.logs)for(var i=0;i<R.logs.length;i++){if(R.logs[i].t>%d)out.push(R.logs[i]);}"
        "var E=window.__DSHSKIN_ERR__;if(E)for(var j=0;j<E.length;j++){if(E[j].t>%d)out.push(E[j]);}"
        "return JSON.stringify(out);})()"
    ) % (since, since)
    for t in targets:
        cdp = sessions.get(t['id'])
        if cdp is None:
            continue
        try:
            r = cdp.eval(expr)
            if not r.get('ok'):
                continue
            for e in json.loads(r.get('value') or '[]'):
                if isinstance(e, dict):
                    e.setdefault('lv', 'info')
                    e['target'] = t['id'][:8]
                    out.append(e)
        except Exception:
            continue
    out.sort(key=lambda x: x.get('t', 0))
    return out


# ---------------- 选择器实测（诊断/漂移检测） ----------------
def selector_counts(selectors, port=DEFAULT_PORT):
    """对 {region: cssSelector} 逐个实测命中数量。"""
    t = find_page_target(port)
    if not t:
        return {'ok': False, 'err': 'no target'}
    try:
        cdp = CDP(t['webSocketDebuggerUrl'])
        expr = "(function(){var M=%s;var O={};for(var k in M){O[k]=document.querySelectorAll(M[k]).length;}return JSON.stringify(O);})()" % json.dumps(selectors)
        r = cdp.eval(expr)
        cdp.close()
        if not r.get('ok'):
            return {'ok': False, 'err': r.get('err')}
        return {'ok': True, 'counts': json.loads(r['value'])}
    except Exception as e:
        return {'ok': False, 'err': str(e)}


def probe_regions(regions=None, port=DEFAULT_PORT):
    """实测各区域选择器命中情况。

    regions 形态：
    - None：延迟导入 marker_engine，取 resolve_regions()（含用户覆盖层），无需调用方关心结构；
    - list[(region, [候选选择器...])]：直接使用；
    - dict {region: "a, b"}：按逗号拆候选。
    返回（兼容旧契约）：{ok, counts, alive, dead, suggested, regions, landmarks}。
    """
    # 归一化为 [(key, [sels])]
    if regions is None:
        try:
            import marker_engine as _me
            pairs = []
            for key, reg in _me.resolve_regions().items():
                sels = [s.strip() for s in str(reg.get('selector', '')).split(',') if s.strip()]
                pairs.append((key, sels))
        except Exception as e:
            return {'ok': False, 'err': '选择器表加载失败: {0}'.format(e)}
    elif isinstance(regions, dict):
        pairs = [(k, [s.strip() for s in str(v).split(',') if s.strip()]) for k, v in regions.items()]
    else:
        pairs = list(regions)

    t = find_page_target(port)
    if not t:
        return {'ok': False, 'err': '未找到 DSH 页面（先启动 DeepSeek Harness 桌面版）'}
    try:
        cdp = CDP(t['webSocketDebuggerUrl'])
        flat = {}
        for region, cands in pairs:
            for i, sel in enumerate(cands):
                flat['%s__%d' % (region, i)] = sel
        expr = "(function(){var M=%s;var O={};for(var k in M){O[k]=document.querySelectorAll(M[k]).length;}return JSON.stringify(O);})()" % json.dumps(flat)
        r = cdp.eval(expr)
        lm = cdp.eval(_LANDMARKS_JS)
        cdp.close()
        out = {'ok': False, 'regions': {}, 'landmarks': {}, 'counts': {},
               'alive': [], 'dead': [], 'suggested': {}}
        if r.get('ok'):
            counts_raw = json.loads(r['value'])
            out['ok'] = True
            for region, cands in pairs:
                hits = [counts_raw.get('%s__%d' % (region, i), 0) for i in range(len(cands))]
                best = next((i for i, n in enumerate(hits) if n > 0), None)
                chosen = cands[best] if best is not None else None
                out['regions'][region] = {
                    'candidates': cands, 'hits': hits,
                    'matched': best is not None, 'chosen': chosen,
                }
                for i, sel in enumerate(cands):
                    out['counts'][sel] = hits[i]
                if chosen:
                    out['alive'].append(region)
                    out['suggested'][region] = chosen
                else:
                    out['dead'].append(region)
        if lm.get('ok'):
            try:
                out['landmarks'] = json.loads(lm['value'])
            except Exception:
                pass
        return out
    except Exception as e:
        return {'ok': False, 'err': str(e)}


# ---------------- 守护线程：多窗口补注 + 自愈 ----------------
class SkinWatcher(threading.Thread):
    """daemon：周期性对齐所有 DSH target，缺皮肤/版本旧了就补；新窗口自动纳入。"""
    def __init__(self, bundle_provider, port=DEFAULT_PORT, interval=6):
        super().__init__(daemon=True)
        self.bundle_provider = bundle_provider   # () -> (css, js)
        self.port = port
        self.interval = interval
        self._stop = threading.Event()
        self.last_report = {}

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                self.last_report = {'ok': False, 'err': str(e), 'ts': time.time()}
            self._stop.wait(self.interval)
        _SESSIONS.close_all()

    def _tick(self):
        css, js = self.bundle_provider()
        if not css or not WEBSOCKET_AVAILABLE or not port_open(self.port):
            self.last_report = {'ok': False, 'err': 'CDP 未就绪', 'ts': time.time()}
            return
        targets = find_page_targets(self.port)
        if not targets:
            self.last_report = {'ok': False, 'err': '无 DSH target', 'ts': time.time()}
            return
        _SESSIONS.port = self.port
        sessions = _SESSIONS.sync(targets)   # 建连新窗口 / 回收关闭窗口 / 注册自举
        h = bundle_hash(css, js)
        injected, stale, missing = 0, 0, 0
        for t in targets:
            tid = t['id']
            cdp = sessions.get(tid)
            if cdp is None:
                missing += 1
                continue
            try:
                st = _page_state_one(cdp)
                need = (not st.get('ok')) or (not st.get('style')) or (not st.get('rt'))
                cur_attr = None
                ra = cdp.eval("(function(){var s=document.getElementById('dsh-skin-cdp');return s?s.getAttribute('data-dsh-skin-cdp'):null;})()")
                if ra.get('ok'):
                    cur_attr = ra.get('value')
                if (not need) and cur_attr != h:
                    need = True
                    stale += 1
                if need:
                    one = _inject_one(cdp, css, js, h)
                    if one.get('ok'):
                        injected += 1
                    else:
                        missing += 1
                else:
                    injected += 1
            except Exception:
                with _SESSIONS._lock:
                    old = _SESSIONS._sess.pop(tid, None)
                if old:
                    old.close()
                missing += 1
        self.last_report = {
            'ok': True, 'ts': time.time(), 'target_count': len(targets),
            'injected': injected, 'stale_refreshed': stale, 'missing': missing,
        }
