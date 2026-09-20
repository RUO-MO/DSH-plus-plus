# -*- coding: utf-8 -*-
"""活体探针：REGIONS 选择器存活 + 注入状态 + 白屏现场（py 3.9 兼容）"""
import json
import urllib.request
from websocket import create_connection

SELECTORS = {
    "frame(marker)": "[data-dsh-skin='frame']",
    "sidebar(marker)": "[data-dsh-skin='sidebar']",
    "chat [data-phase]": "[data-phase]",
    "chat root only": "div[data-phase]:not([contenteditable] [data-phase])",
    "scroll": "[data-conversation-scroll]",
    "composerbar": "[data-composer-seat]",
    "composer": "[data-composer-card='true']",
    "rightbar": "[data-rightbar-col]",
    "overlay": "[data-shell-overlay]",
    "sessionhead": "[data-slot='conversation.session.header']",
    "flow-kind": "[data-chat-flow-kind]",
    "flow-kind/user": "[data-chat-flow-kind='user']",
    "flow-kind/assistant": "[data-chat-flow-kind='assistant']",
    "slot composer.bar": "[data-slot='conversation.composer.bar']",
    "slot chat.node": "[data-slot='conversation.chat.node']",
}

JS = """
(function(){
  var sels = %SELS%;
  var out = {counts:{}, state:{}, styles:{}};
  for (var k in sels) {
    try { out.counts[k] = document.querySelectorAll(sels[k]).length; }
    catch(e){ out.counts[k] = 'ERR'; }
  }
  var style = document.getElementById('dsh-skin-cdp');
  out.state.styleInjected = !!style;
  out.state.styleLen = style ? style.textContent.length : 0;
  out.state.darkAttr = document.body.getAttribute('data-ds-dark-theme');
  out.state.colorScheme = getComputedStyle(document.documentElement).colorScheme;
  out.state.dshSkinRuntime = typeof window.DSHSkin !== 'undefined';
  out.state.boot = !!window.__DSH_SKIN_BOOT__;
  out.state.bodyBgImage = getComputedStyle(document.body).backgroundImage.slice(0,80);
  out.state.bodyBgColor = getComputedStyle(document.body).backgroundColor;
  // 会话根与输入面的实际计算样式（白屏现场）
  var chat = document.querySelector('[data-phase]');
  if (chat) {
    var cs = getComputedStyle(chat);
    out.styles.chatRoot = {tag: chat.tagName, cls: (chat.className||'').toString().slice(0,40),
      bg: cs.backgroundColor, color: cs.color, blur: cs.backdropFilter || cs.webkitBackdropFilter};
  }
  var editor = document.querySelector('[data-composer-card="true"] [data-phase]');
  if (editor) {
    var cs2 = getComputedStyle(editor);
    out.styles.inputEditor = {bg: cs2.backgroundColor, color: cs2.color, blur: cs2.backdropFilter || ''};
  }
  var frame = document.querySelector("[data-dsh-skin='frame']");
  out.styles.frameBg = frame ? getComputedStyle(frame).backgroundColor : null;
  var sb = document.querySelector("[data-dsh-skin='sidebar']");
  out.styles.sidebarBg = sb ? getComputedStyle(sb).backgroundColor : null;
  // 主文本颜色样例（会话区第一个段落/文本块）
  var node = document.querySelector('[data-chat-flow-kind]');
  out.styles.firstNodeColor = node ? getComputedStyle(node).color : null;
  out.state.rootChildren = document.getElementById('root') ? document.getElementById('root').children.length : 'no-root';
  var top = document.getElementById('root');
  out.state.topChain = [];
  var n = top;
  for (var i = 0; i < 4 && n; i++) {
    out.state.topChain.push(n.tagName + (n.getAttribute && n.getAttribute('data-slot') ? '['+n.getAttribute('data-slot')+']' : '') + (n.className && typeof n.className === 'string' ? '.'+n.className.split(' ')[0] : ''));
    n = n.firstElementChild;
  }
  return JSON.stringify(out);
})();
""".replace('%SELS%', json.dumps(SELECTORS))

tabs = json.load(urllib.request.urlopen('http://127.0.0.1:9222/json', timeout=5))
page = next((t for t in tabs if t.get('type') == 'page'), tabs[0])
print('TARGET:', page.get('title'), '|', page.get('url'))
ws = create_connection(page['webSocketDebuggerUrl'], timeout=20, suppress_origin=True)
ws.send(json.dumps({'id': 1, 'method': 'Runtime.enable', 'params': {}}))
ws.recv()
ws.send(json.dumps({'id': 2, 'method': 'Runtime.evaluate',
                    'params': {'expression': JS, 'returnByValue': True}}))
while True:
    msg = json.loads(ws.recv())
    if msg.get('id') == 2:
        break
r = msg.get('result', {}).get('result', {})
if r.get('type') == 'string':
    print(json.dumps(json.loads(r['value']), ensure_ascii=False, indent=1))
else:
    print('EVAL FAIL:', msg.get('result') or msg)
ws.close()
