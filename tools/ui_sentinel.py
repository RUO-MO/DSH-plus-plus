# -*- coding: utf-8 -*-
"""面板渲染哨兵：无头 Edge + CDP，逐视图切换，收集 console 错误 + 关键 DOM 断言"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from websocket import create_connection

EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
PORT = 9333
BASE = 'http://127.0.0.1:8765'
TMP = os.path.join(os.environ.get('TEMP', '.'), 'dshskin-ui-test')

# 每个视图的断言：选择器 → 期望非空（文本或子元素）
VIEWS = {
    'home': ['#home-grid .stat-card', '#home-quick .btn', '#home-recent'],
    'themes': ['#theme-grid', '#gallery-count'],
    'sessions': ['#sessions-body .tbl', '#sessions-body'],
    'prov': ['#prov-body .card', '#prov-body'],
    'enh': ['#view-enh'],
    'market': ['#market-grid .mk-card'],
    'diag': ['#view-diag'],
    'logs': ['#view-logs'],
    'settings': ['#settings-form .set-row'],
}

subprocess.Popen([EDGE, '--headless=new', '--disable-gpu', '--no-first-run',
                  '--remote-debugging-port=%d' % PORT, '--window-size=1280,860',
                  '--user-data-dir=' + TMP, 'about:blank'],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    ws_url = None
    for _ in range(30):
        time.sleep(0.5)
        try:
            tabs = json.load(urllib.request.urlopen('http://127.0.0.1:%d/json' % PORT, timeout=2))
            pages = [t for t in tabs if t.get('type') == 'page']
            if pages:
                ws_url = pages[0]['webSocketDebuggerUrl']
                break
        except Exception:
            continue
    if not ws_url:
        print('FATAL: headless Edge 未就绪')
        sys.exit(1)

    ws = create_connection(ws_url, timeout=30, suppress_origin=True)
    mid = [0]
    console = []

    def call(method, params=None):
        mid[0] += 1
        ws.send(json.dumps({'id': mid[0], 'method': method, 'params': params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get('id') == mid[0]:
                return m

    def evaluate(expr):
        r = call('Runtime.evaluate', {'expression': expr, 'returnByValue': True, 'awaitPromise': True})
        res = r.get('result', {}).get('result', {})
        return res.get('value')

    call('Runtime.enable')
    call('Page.enable')
    call('Log.enable')
    # 监听 console/异常：用轮询注入收集器（简单可靠）
    call('Runtime.evaluate', {'expression': """
      window.__errs = [];
      window.addEventListener('error', function(e){ __errs.push('JS: ' + e.message); });
      window.addEventListener('unhandledrejection', function(e){ __errs.push('PROMISE: ' + e.reason); });
      var _ce = console.error;
      console.error = function(){ __errs.push('CONSOLE: ' + Array.prototype.join.call(arguments, ' ').slice(0, 200)); _ce.apply(console, arguments); };
      'hooked'
    """, 'returnByValue': True})

    evaluate("location.href = '%s/?view=home'" % BASE)
    time.sleep(3.5)

    print('=== 视图渲染矩阵 ===')
    ok_all = True
    for view, sels in VIEWS.items():
        evaluate("typeof switchView === 'function' ? switchView('%s') : null" % view)
        time.sleep(1.6)
        probs = []
        for sel in sels:
            try:
                n = evaluate("(function(){ var el = document.querySelector(%s); "
                             "if (!el) return 'MISSING'; "
                             "var t = (el.textContent || '').trim().length, c = el.children.length;"
                             " return (t > 0 || c > 0) ? 'ok(t'+t+'/c'+c+')' : 'EMPTY'; })()" % json.dumps(sel))
            except Exception as e:
                n = 'EVAL-ERR'
            if n != 'ok' and not str(n).startswith('ok('):
                if not (view == 'sessions' and sel == '#sessions-body .tbl' and n == 'EMPTY'):
                    probs.append('%s→%s' % (sel, n))
        errs = evaluate("JSON.stringify((window.__errs||[]).slice(-5))")
        err_list = json.loads(errs) if errs else []
        status = 'PASS' if not probs else 'FAIL'
        if probs:
            ok_all = False
        extra = (' console: %s' % err_list) if err_list else ''
        print('%s %-9s %s%s' % ('✓' if status == 'PASS' else '✗', view, '; '.join(probs) or 'ok', extra))

    errs = evaluate("JSON.stringify(window.__errs || [])")
    print('\n=== 全程 console/异常 ===')
    print(errs if errs else '（无）')

    # 导航计数徽标检查
    counts = evaluate("JSON.stringify({themes:(document.getElementById('nav-count-themes')||{}).textContent,"
                      "market:(document.getElementById('nav-count-market')||{}).textContent,"
                      "sessions:(document.getElementById('nav-count-sessions')||{}).textContent})")
    print('=== 侧栏徽标 ===', counts)
    ws.close()
    print('\n总体: %s' % ('PASS' if ok_all else 'FAIL'))
finally:
    subprocess.run(['taskkill', '/f', '/im', 'msedge.exe'], capture_output=True)
