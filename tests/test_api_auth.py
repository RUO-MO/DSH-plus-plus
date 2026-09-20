# -*- coding: utf-8 -*-
"""P2-5 本机 API token 鉴权集成测试（真实起 ThreadingHTTPServer）"""
import sys, os, tempfile, threading, json, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as SV
from http.server import ThreadingHTTPServer

SV.SERVER_TOKEN = 't' * 48
srv = ThreadingHTTPServer(('127.0.0.1', 0), SV.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d' % port

def req(method, path, headers=None, body=None):
    h = headers or {}
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')

# 1) ping 永远放行
code, _ = req('GET', '/api/ping')
assert code == 200
print('1) ping 无需令牌: PASS')

# 2) 非敏感 GET（主题列表）无需令牌
code, _ = req('GET', '/api/themes')
assert code == 200
print('2) 非敏感 GET 放行: PASS')

# 3) 敏感 GET 无令牌 → 401
code, body = req('GET', '/api/providers')
assert code == 401 and '未授权' in body
print('3) 敏感 GET 无令牌 401: PASS')

# 4) 敏感 GET 带正确 token（query）→ 非 401
code, _ = req('GET', '/api/providers?token=' + SV.SERVER_TOKEN)
assert code != 401
print('4) 敏感 GET 带令牌放行: PASS')

# 5) POST 无令牌 → 401
code, body = req('POST', '/api/switch', body={'id': 'keus'})
assert code == 401
print('5) POST 无令牌 401: PASS')

# 6) POST 错误令牌 → 401
code, _ = req('POST', '/api/switch', {'X-DSHSkin-Token': 'wrong'}, {'id': 'keus'})
assert code == 401
print('6) POST 错误令牌 401: PASS')

# 7) POST 正确令牌 → 非 401（进入业务校验，哪怕参数报错也不是鉴权问题）
code, _ = req('POST', '/api/switch', {'X-DSHSkin-Token': SV.SERVER_TOKEN,
                                      'Content-Type': 'application/json'}, {})
assert code != 401
print('7) POST 正确令牌越过鉴权: PASS')

# 8) 跨站 Origin POST（带正确 token 也应被 Origin 拦成 403，纵深防御）
code, _ = req('POST', '/api/switch', {'Origin': 'http://evil.example',
                                      'X-DSHSkin-Token': SV.SERVER_TOKEN}, {})
assert code == 403
print('8) 跨站 Origin 仍被 403: PASS')

# 9) 托管 HTML 注入 token 引导
code, body = req('GET', '/panel.html')
assert code == 200 and 'window.__DSHSKIN_TOKEN__' in body and SV.SERVER_TOKEN in body
print('9) 托管页注入令牌引导: PASS')

srv.shutdown()
print('=== API 鉴权全部 PASS ===')
