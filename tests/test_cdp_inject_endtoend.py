# -*- coding: utf-8 -*-
"""端到端验证 CDP 注入机制（含新壳 target 识别），无需真实 DSH。

两个迷你服务：
  - HTTP :/json 返回「loopback http 主页面 + native-ui 对话框 + devtools」目标集；
  - 原始 TCP WebSocket 服务器 :/devtools/page/main 处理 CDP 命令（记录注入命令）。
调用 cdp_skin.inject_bundle(css, js)，断言：
  1) 命中正确 target（只选 loopback http 页，跳过 native-ui/dialog/devtools）；
  2) 建立 WS 并执行 Page.enable / addScriptToEvaluateOnNewDocument / Runtime.evaluate；
  3) 返回 ok。
"""
import sys, os, json, threading, time, socket, hashlib, base64
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cdp_skin as C

HTTP_PORT = 19222
WS_PORT = 19223
HOME = 'http://127.0.0.1:%d/app/' % HTTP_PORT
TARGETS = [
    {'type': 'page', 'id': 'main', 'url': HOME, 'title': 'DSH Desktop',
     'webSocketDebuggerUrl': 'ws://127.0.0.1:%d/devtools/page/main' % WS_PORT},
    {'type': 'page', 'id': 'dlg', 'url': 'file:///D:/x/lib/native-ui/profile-selector.html',
     'title': 'profile-selector',
     'webSocketDebuggerUrl': 'ws://127.0.0.1:%d/devtools/page/dlg' % WS_PORT},
    {'type': 'page', 'id': 'dev', 'url': 'devtools://devtools/bundled/inspector.html',
     'title': 'devtools',
     'webSocketDebuggerUrl': 'ws://127.0.0.1:%d/devtools/page/dev' % WS_PORT},
]

logs = {'commands': set(), 'eval_len': 0, 'eval_seen': []}
lock = threading.Lock()


def ws_send(conn, payload):
    data = json.dumps(payload).encode('utf-8')
    ln = len(data)
    hdr = bytearray([0x81])
    if ln < 126:
        hdr.append(ln)
    elif ln < 65536:
        hdr.append(126); hdr += ln.to_bytes(2, 'big')
    else:
        hdr.append(127); hdr += ln.to_bytes(8, 'big')
    conn.sendall(bytes(hdr) + data)


def ws_recv(conn):
    h = conn.recv(2)
    if len(h) < 2:
        return None
    ln = h[1] & 0x7F
    if ln == 126:
        ln = int.from_bytes(conn.recv(2), 'big')
    elif ln == 127:
        ln = int.from_bytes(conn.recv(8), 'big')
    mask = conn.recv(4)
    raw = conn.recv(ln)
    if len(mask) == 4:
        raw = bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
    try:
        return json.loads(raw.decode('utf-8'))
    except Exception:
        return None


def handle_ws(conn):
    while True:
        msg = ws_recv(conn)
        if msg is None:
            return
        mid = msg.get('id')
        method = msg.get('method', '')
        with lock:
            logs['commands'].add(method)
        if method == 'Page.addScriptToEvaluateOnNewDocument':
            ws_send(conn, {'id': mid, 'result': {'identifier': 'BOOT%04d' % mid}})
        elif method in ('Runtime.evaluate',):
            with lock:
                logs['eval_len'] = len(str(msg.get('params', {}).get('expression', '')))
                logs['eval_seen'].append(str(msg.get('params', {}).get('expression', ''))[:60])
            ws_send(conn, {'id': mid, 'result': {'result': {'type': 'object', 'value': {}}}})
        else:
            ws_send(conn, {'id': mid, 'result': {}})


def ws_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', WS_PORT))
    srv.listen(5)
    while True:
        conn, _ = srv.accept()
        # 握手
        req = b''
        while b'\r\n\r\n' not in req:
            c = conn.recv(4096)
            if not c:
                break
            req += c
        key = None
        for ln in req.split(b'\r\n')[1:]:
            if ln.lower().startswith(b'sec-websocket-key:'):
                key = ln.split(b':', 1)[1].strip()
                break
        if key:
            accept = base64.b64encode(hashlib.sha1(
                key + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
            conn.sendall(b'HTTP/1.1 101 Switching Protocols\r\n'
                         b'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                         b'Sec-WebSocket-Accept: ' + accept + b'\r\n\r\n')
            threading.Thread(target=handle_ws, args=(conn,), daemon=True).start()
        else:
            conn.close()


def http_server():
    from http.server import BaseHTTPRequestHandler

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == '/json':
                body = json.dumps(TARGETS).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(('127.0.0.1', HTTP_PORT), H)
    srv.serve_forever()


if __name__ == '__main__':
    threading.Thread(target=ws_server, daemon=True).start()
    threading.Thread(target=http_server, daemon=True).start()
    time.sleep(0.6)

    css = '/*TEST-THEME*/.x{color:red}' * 50
    js = 'window.TEST_ENHANCE=1;'

    C.invalidate_probe_cache()
    r = C.inject_bundle(css, js, HTTP_PORT)
    print('inject_bundle:', r)
    with lock:
        print('commands:', sorted(logs['commands']))

    ok = r.get('ok')
    assert ok, 'inject 应成功: %r' % r
    assert r.get('target_count') == 1, '应只注入主页面, 实际 %r' % r.get('target_count')
    assert 'Page.addScriptToEvaluateOnNewDocument' in logs['commands'], '应注册自举脚本'
    assert 'Runtime.evaluate' in logs['commands'], '应执行注入表达式'
    assert logs['eval_len'] > 100, '注入表达式应含较大内容(css/js), 实际 %d' % logs['eval_len']
    print('ALL CDP INJECT ENDTOEND OK (target_count=%s, eval_len=%d)' % (
        r.get('target_count'), logs['eval_len']))