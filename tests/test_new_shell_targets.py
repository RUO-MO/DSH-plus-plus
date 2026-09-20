# -*- coding: utf-8 -*-
"""新壳目标选择逻辑自测（非破坏，纯内存 mock）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cdp_skin as C

pages = [
    {'type': 'page', 'url': 'http://127.0.0.1:52341/app/', 'webSocketDebuggerUrl': 'ws://1'},
    {'type': 'page', 'url': 'http://127.0.0.1:52341/desktop/settings', 'webSocketDebuggerUrl': 'ws://2'},
    {'type': 'page', 'url': 'file:///D:/x/lib/native-ui/setup-wizard.html', 'webSocketDebuggerUrl': 'ws://3'},
    {'type': 'page', 'url': 'devtools://devtools/bundled/inspector.html', 'webSocketDebuggerUrl': 'ws://4'},
]

# mock _targets 输出（ts 设为最近，命中短缓存）
C._TARGETS_CACHE['ts'] = __import__('time').time() - 0.1
C._TARGETS_CACHE['value'] = pages
C._TARGETS_CACHE['port'] = 9222
got = [t['url'] for t in C.find_page_targets(9222)]
print('cdp_skin.find_page_targets ->')
for g in got:
    print('   ', g)
assert len(got) == 2, '应命中 2 个 loopback 页面，实际 %d' % len(got)
assert all(str(u).startswith('http://127.0.0.1:') for u in got), got
print('cdp_skin target select OK')

import dsh_env as E
E._CDP_HTTP_CACHE['ts'] = __import__('time').time() - 0.1
E._CDP_HTTP_CACHE['key'] = ('/json', 9222)
E._CDP_HTTP_CACHE['value'] = pages
t = E.cdp_page_target()
print('dsh_env.cdp_page_target ->', t and t['url'])
assert t and str(t['url']).startswith('http://127.0.0.1:'), 'dsh_env 应选中 loopback 主界面'
assert 'native-ui' not in str(t['url'])
print('dsh_env target select OK')

# 兼容旧：单个 dsh-app 页
old = [{'type': 'page', 'url': 'dsh-app://main', 'webSocketDebuggerUrl': 'ws://9'}]
E._CDP_HTTP_CACHE['ts'] = __import__('time').time() - 0.1
E._CDP_HTTP_CACHE['key'] = ('/json', 9222)
E._CDP_HTTP_CACHE['value'] = old
assert E.cdp_page_target() is not None
print('legacy dsh-app target OK')
print('ALL SELFTEST OK')