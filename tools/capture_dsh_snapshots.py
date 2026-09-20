#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DSHSkin · DSH 真实面板快照抓取器
=================================
从一个正在运行、带 CDP 调试端口（9222）的 DeepSeek Harness 桌面版导出
「真实面板 DOM 快照」+ 真实 CSS，用于预览模拟器离线渲染。
预览窗口与真实应用的结构/类名/CSS 完全一致，DSHSkin 仅注入主题变量着色。

用法：
    python tools/capture_dsh_snapshots.py            # 抓取 home/conv/settings 三面板快照 + CSS
    python tools/capture_dsh_snapshots.py --port 9222

输出（写入 assets/）：
    dsh-vendor.css / dsh-index.css / dsh-inline.css   # 真实 CSS（外部 + CSS-in-JS）
    snap-home.html / snap-conv.html / snap-settings.html  # 真实面板快照预览页

前提：DeepSeek Harness 已以开发模式运行（.lnk → start-desktop.cmd → pnpm start:desktop），
      渲染进程自带 --remote-debugging-port=9222。
"""
import glob
import json
import os
import re
import shutil
import sys
import time
import urllib.request

PORT = 9222
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, 'assets')
HARNESS_DIST = r'D:\DSH\deepseek-harness\apps\web\dist\assets'

try:
    from websocket import create_connection
except ImportError:
    print('[错误] 需要 websocket-client：pip install websocket-client')
    sys.exit(1)


def cdp_connect(port):
    targets = json.load(urllib.request.urlopen('http://127.0.0.1:%d/json' % port, timeout=5))
    page = [t for t in targets if t.get('type') == 'page' and t.get('webSocketDebuggerUrl')]
    if not page:
        raise RuntimeError('端口 %d 上没有页面 target（DSH 桌面版未运行？）' % port)
    ws = create_connection(page[0]['webSocketDebuggerUrl'], suppress_origin=True, timeout=30)
    mid = {'n': 0}

    def call(method, params=None):
        mid['n'] += 1
        ws.send(json.dumps({'id': mid['n'], 'method': method, 'params': params or {}}))
        while True:
            r = json.loads(ws.recv())
            if r.get('id') == mid['n']:
                return r

    call('Runtime.enable')
    return ws, call


def grab_body(call, label, outfile):
    expr = r"""
    (() => {
      const clone = document.body.cloneNode(true);
      clone.querySelectorAll('*').forEach(el => {
        el.removeAttribute('data-dsh-skin');
        el.removeAttribute('data-dsh-skin-cdp');
      });
      return clone.innerHTML;
    })()
    """
    r = call('Runtime.evaluate', {'expression': expr, 'returnByValue': True})
    html = r['result']['result'].get('value') or ''
    open(outfile, 'w', encoding='utf-8').write(html)
    print('  [%s] %d bytes -> %s' % (label, len(html), os.path.basename(outfile)))
    return html


def grab_inline_css(call):
    expr = r"""
    (() => {
      const inline = [];
      document.querySelectorAll('style').forEach(s => { if (s.textContent) inline.push(s.textContent); });
      return JSON.stringify(inline);
    })()
    """
    r = call('Runtime.evaluate', {'expression': expr, 'returnByValue': True})
    arr = json.loads(r['result']['result'].get('value') or '[]')
    merged = '\n'.join(arr)
    open(os.path.join(ASSETS, 'dsh-inline.css'), 'w', encoding='utf-8').write(merged)
    print('  [inline-css] %d 个 style 标签, %d bytes' % (len(arr), len(merged)))


def copy_external_css():
    mapping = {'index-*.css': 'dsh-index.css', 'vendor-*.css': 'dsh-vendor.css'}
    for pat, out in mapping.items():
        hits = glob.glob(os.path.join(HARNESS_DIST, pat))
        if hits:
            shutil.copy2(hits[0], os.path.join(ASSETS, out))
            print('  [css] %s -> %s (%d bytes)' % (os.path.basename(hits[0]), out, os.path.getsize(hits[0])))
        else:
            print('  [css] 未找到 %s（harness 路径变化？）' % pat)


def build_snap_page(name, snap_file):
    snap = open(snap_file, encoding='utf-8').read()
    snap = re.sub(r'<script>.*?</script>', '', snap, flags=re.S)
    snap = re.sub(r'<link[^>]*stylesheet[^>]*>', '', snap)
    inject_js = open(os.path.join(ROOT, 'tools', 'snap-inject.js'), encoding='utf-8').read()
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>DSH 真实面板 · %s</title>
<link rel="stylesheet" href="dsh-vendor.css">
<link rel="stylesheet" href="dsh-index.css">
<link rel="stylesheet" href="dsh-inline.css">
<style>html,body{margin:0;padding:0;width:100%%;height:100%%;overflow:hidden}body{background:#fafafa}</style>
<script>
%s
</script>
</head>
<body>
%s
</body>
</html>
""" % (name, inject_js, snap)
    out = os.path.join(ASSETS, 'snap-%s.html' % name)
    open(out, 'w', encoding='utf-8').write(html)
    print('  [snap] %s -> %s (%d bytes)' % (name, os.path.basename(out), len(html)))


def write_manifest(port):
    """记录快照来源版本/时间/文件指纹，供面板判断快照是否随 DSH 升级而过期。"""
    hv, tv = '', 0
    try:
        sys.path.insert(0, ROOT)
        import dsh_env
        import marker_engine
        hv = dsh_env.harness_version() or ''
        tv = marker_engine.MARKER_VERSION
    except Exception:
        pass
    files = {}
    for fn in ('snap-home.html', 'snap-conv.html', 'snap-settings.html',
               'dsh-vendor.css', 'dsh-index.css', 'dsh-inline.css'):
        fp = os.path.join(ASSETS, fn)
        if os.path.isfile(fp):
            files[fn] = {'size': os.path.getsize(fp), 'mtime': int(os.path.getmtime(fp))}
    manifest = {
        'capturedAt': int(time.time()), 'port': port,
        'harness_version': hv, 'marker_version': tv, 'files': files,
    }
    with open(os.path.join(ASSETS, 'snap-manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print('  [manifest] DSH=%s 模板v%s 文件%d个' % (hv or '未知', tv, len(files)))


def main():
    global PORT
    args = sys.argv[1:]
    if '--port' in args:
        PORT = int(args[args.index('--port') + 1])
    os.makedirs(ASSETS, exist_ok=True)
    print('连接 CDP %d ...' % PORT)
    ws, call = cdp_connect(PORT)
    try:
        call('Runtime.evaluate', {'expression': "document.querySelector('.Vt7x3G_mask')?.click()"})
        time.sleep(0.8)
        grab_body(call, 'home-hero', os.path.join(ASSETS, '_snap_home.html'))
        call('Runtime.evaluate', {'expression': """
        (() => {
          const items = document.querySelectorAll('[data-slot="sidebar.workspaces"] button');
          if (items.length) { items[0].click(); return 'ok'; }
          return 'none';
        })()
        """, 'returnByValue': True})
        time.sleep(1.5)
        grab_body(call, 'conversation', os.path.join(ASSETS, '_snap_conv.html'))
        call('Runtime.evaluate', {'expression': """
        (() => {
          const btns = [...document.querySelectorAll('button')];
          const b = btns.find(x => (x.innerText || '').trim() === '设置');
          if (b) { b.click(); return 'ok'; }
          return 'none';
        })()
        """, 'returnByValue': True})
        time.sleep(1.5)
        grab_body(call, 'settings', os.path.join(ASSETS, '_snap_settings.html'))
        call('Runtime.evaluate', {'expression': "document.querySelector('.Vt7x3G_mask')?.click()"})
        time.sleep(0.6)
        call('Runtime.evaluate', {'expression': """
        (() => {
          const b = [...document.querySelectorAll('button')].find(x => (x.innerText || '').trim() === '新会话');
          if (b) { b.click(); return 'ok'; }
          return 'none';
        })()
        """, 'returnByValue': True})
        time.sleep(1.0)
        grab_inline_css(call)
        copy_external_css()
        for name in ('home', 'conv', 'settings'):
            build_snap_page(name, os.path.join(ASSETS, '_snap_%s.html' % name))
        write_manifest(PORT)
        print('完成：assets/ 下已生成真实 CSS + 快照预览页')
    finally:
        ws.close()


if __name__ == '__main__':
    main()
