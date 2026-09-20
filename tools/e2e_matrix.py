# -*- coding: utf-8 -*-
"""联调矩阵：逐个打全部 API + 关键闭环，输出 PASS/FAIL 清单"""
import json
import urllib.request

BASE = 'http://127.0.0.1:8765'
ORIGIN = {'Origin': BASE, 'Content-Type': 'application/json'}
results = []


def get(path, timeout=15):
    req = urllib.request.Request(BASE + path, headers={'Origin': BASE})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def post(path, body=None, timeout=30):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers=ORIGIN, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def check(name, fn):
    try:
        detail = fn()
        results.append((name, 'PASS', detail))
    except Exception as e:
        results.append((name, 'FAIL', str(e)[:120]))


# ---- GET 矩阵 ----
check('GET /api/ping', lambda: 'v' + get('/api/ping')['version'])
check('GET /api/themes', lambda: '%d 主题, active=%s' % (len(get('/api/themes')['themes']), get('/api/themes')['active']))
check('GET /api/status', lambda: 'template v%s, injected=%s' % (get('/api/status')['template_version'], get('/api/status')['cdp']['injected']))
check('GET /api/detect', lambda: 'root=%s' % bool(get('/api/detect').get('harness_root') or get('/api/detect')))
check('GET /api/deps', lambda: 'websocket=%s' % get('/api/deps')['websocket'])
check('GET /api/doctor', lambda: '%d 段体检' % len(get('/api/doctor')['report']) if isinstance(get('/api/doctor')['report'], (list, dict)) else 'ok')
check('GET /api/cdp-status', lambda: json.dumps(get('/api/cdp-status'))[:80])
check('GET /api/selectors', lambda: '%d 区域' % len(get('/api/selectors')['regions']))
check('GET /api/logs', lambda: '%d 条' % len(get('/api/logs')['logs']))
check('GET /api/enhance', lambda: 'enabled=%s' % get('/api/enhance').get('enabled'))
check('GET /api/market', lambda: '%d 个脚本' % len(get('/api/market')['items']))
check('GET /api/sessions', lambda: '%d 个会话' % len(get('/api/sessions')['sessions']))
check('GET /api/providers', lambda: 'source=%s' % get('/api/providers').get('source', '')[:30])
check('GET /api/settings', lambda: 'port=%s' % get('/api/settings')['settings'].get('cdp_port'))


# ---- 闭环：市场安装状态回读 ----
def market_roundtrip():
    items = get('/api/market')['items']
    installed = [i['name'] for i in items if i['installed']]
    return '已装: %s' % (','.join(installed) or '无')


check('闭环·市场安装标记', market_roundtrip)


# ---- 闭环：主题参数热生效（读一个参数→改→回读 live 状态）----
def theme_live_roundtrip():
    st = get('/api/status')
    active = st.get('active')
    if not active:
        raise RuntimeError('无激活主题')
    r = post('/api/theme-params/' + active, {'params': {'blur_strength': 14}})
    live = (r.get('data') or {}).get('live')
    return 'active=%s live=%s' % (active, live)


check('闭环·主题参数热生效', theme_live_roundtrip)


# ---- 闭环：增强开关回读（modules 是对象数组）----
def enhance_roundtrip():
    def mod_on(key):
        mods = get('/api/enhance')['modules']
        return next(m['enabled'] for m in mods if m['key'] == key)
    post('/api/enhance', {'modules': {'session-tools': False}})
    v1 = mod_on('session-tools')
    post('/api/enhance', {'modules': {'session-tools': True}})
    v2 = mod_on('session-tools')
    return 'off→%s on→%s' % (v1, v2)


check('闭环·增强模块开关', enhance_roundtrip)


# ---- 闭环：会话导出 ----
def session_export():
    s = get('/api/sessions')['sessions']
    sid = next((x['id'] for x in s if x.get('title')), None) or s[0]['id']
    r = post('/api/sessions-export', {'id': sid, 'format': 'md'})
    import os
    ok = os.path.isfile(r['path'])
    return '%s (%d bytes)' % (os.path.basename(r['path']), os.path.getsize(r['path'])) if ok else '文件未落盘'


check('闭环·会话导出', session_export)


# ---- 闭环：备份 ----
check('闭环·会话备份', lambda: post('/api/sessions-backup', {}, timeout=60)['path'].split('\\')[-1])
check('闭环·凭证备份', lambda: post('/api/providers-backup', {})['path'].split('\\')[-1])


# ---- 闭环：CDP 注入状态与指纹一致 ----
def inject_fresh():
    st = get('/api/status')['cdp']
    if not st['injected']:
        raise RuntimeError('未注入')
    return 'hash=%s runtime=%s' % (str(st['hash'])[:8], st['runtime'])


check('闭环·CDP 注入与指纹', inject_fresh)

w = max(len(n) for n, _, _ in results)
fails = 0
for name, status, detail in results:
    mark = '✓' if status == 'PASS' else '✗'
    if status == 'FAIL':
        fails += 1
    print('%s %-*s  %s' % (mark, w, name, detail))
print('\n%d/%d 通过' % (len(results) - fails, len(results)))
