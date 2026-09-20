/* DSHSkin 内置增强模块：会话工具
 * ------------------------------------------------------------------
 * 能力：把当前会话导出为 Markdown / 复制为 Markdown 文本。
 * 做法：纯前端从 DOM 提取消息，不依赖任何官方接口，也不落盘任何官方文件。
 * 兼容：消息容器走稳定标记 [data-chat-flow-kind]（用户/助手消息判别），
 *       DeepSeek Harness 升级改类名也能凑合工作；找不到消息时只提示，不报错。
 * 快捷键：Ctrl+Alt+E 导出 · Ctrl+Alt+C 复制
 */
DSHSkin.def('session-tools', function (R) {
  var TURN_SEL = ['[data-chat-flow-kind]', '[data-chat-turn]', '[data-conversation-scroll] > div > [data-slot] > div'];
  var CONTENT_SEL = ['[data-slot="conversation.chat.node"]', '[class*="markdown"]', '[class*="messageContent"]'];

  function q(sel) { try { return document.querySelectorAll(sel); } catch (e) { return []; } }
  function firstIn(root, list) {
    for (var i = 0; i < list.length; i++) {
      try { var n = root.querySelector(list[i]); if (n) return n; } catch (e) { }
    }
    return null;
  }
  function all(list) {
    var out = [], seen = [];
    for (var i = 0; i < list.length; i++) {
      var nodes = q(list[i]);
      for (var j = 0; j < nodes.length; j++) {
        if (seen.indexOf(nodes[j]) < 0) { seen.push(nodes[j]); out.push(nodes[j]); }
      }
    }
    return out;
  }
  function visible(el) {
    try { var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; } catch (e) { return false; }
  }
  function turns() { return all(TURN_SEL).filter(visible); }

  function roleOf(el) {
    var kind = String(el.getAttribute('data-chat-flow-kind') || '').toLowerCase();
    if (kind.indexOf('user') === 0) return 'user';
    if (kind.indexOf('assistant') === 0) return 'assistant';
    var sig = String(el.className || '') + ' ' + String(el.getAttribute('data-role') || '');
    sig = sig.toLowerCase();
    if (/(^|[^a-z])(user|human|me)([^a-z]|$)/.test(sig)) return 'user';
    if (/(^|[^a-z])(assistant|ai|bot|agent)([^a-z]|$)/.test(sig)) return 'assistant';
    return kind ? 'step' : 'message';
  }

  function contentNode(el) { return firstIn(el, CONTENT_SEL) || el; }

  function mdFrom(root) {
    var c;
    try { c = root.cloneNode(true); } catch (e) { return ''; }
    var drop = c.querySelectorAll('button,[role="button"],svg,[class*="toolbar"],[class*="Toolbar"],' +
      '[class*="actions"],[class*="Actions"],[class*="copy"],[class*="Copy"],[data-message-attachments]');
    Array.prototype.forEach.call(drop, function (n) { try { n.parentNode && n.parentNode.removeChild(n); } catch (e) { } });
    Array.prototype.forEach.call(c.querySelectorAll('pre'), function (pre) {
      try {
        var code = pre.querySelector('code');
        var cls = code ? String(code.className || '') : '';
        var m = /language-([\w+#.-]+)/.exec(cls);
        var body = String((code || pre).innerText || '').replace(/\s+$/, '');
        pre.parentNode.replaceChild(
          document.createTextNode('\n```' + (m ? m[1] : '') + '\n' + body + '\n```\n'), pre);
      } catch (e) { }
    });
    return String((c.innerText || c.textContent || '')).trim();
  }

  function stamp() {
    var d = new Date(), p = function (n) { return (n < 10 ? '0' : '') + n; };
    return '' + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '-' + p(d.getHours()) + p(d.getMinutes());
  }

  function build() {
    var list = turns();
    if (!list.length) return null;
    var lines = ['# DeepSeek Harness 会话导出', '', '> 导出时间：' + new Date().toLocaleString(),
      '> 消息数：' + list.length, ''];
    for (var i = 0; i < list.length; i++) {
      var role = roleOf(list[i]);
      var label = role === 'user' ? '用户' : role === 'assistant' ? 'DeepSeek' : role;
      lines.push('## ' + (i + 1) + '. ' + label);
      lines.push('');
      lines.push(mdFrom(contentNode(list[i])));
      lines.push('');
    }
    return lines.join('\n');
  }

  function save(text) {
    try {
      var blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'dsh-chat-' + stamp() + '.md';
      (document.body || document.documentElement).appendChild(a);
      a.click();
      setTimeout(function () {
        try { URL.revokeObjectURL(a.href); a.parentNode && a.parentNode.removeChild(a); } catch (e) { }
      }, 800);
      return true;
    } catch (e) { R.log('导出失败', String(e)); return false; }
  }

  function copy(text, tip) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
          function () { R.toast(tip || '已复制'); },
          function () { R.toast('复制失败（剪贴板被拒）', 2600); });
        return true;
      }
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;left:-9999px;top:0;';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.parentNode.removeChild(ta);
      R.toast(tip || '已复制');
      return true;
    } catch (e) { R.log('复制失败', String(e)); return false; }
  }

  function doExport() {
    var t = build();
    if (!t) { R.toast('没找到会话消息，无法导出', 2600); return; }
    if (save(t)) R.toast('已导出 Markdown（' + turns().length + ' 条消息）');
  }
  function doCopy() {
    var t = build();
    if (!t) { R.toast('没找到会话消息，无法复制', 2600); return; }
    copy(t, '会话 Markdown 已复制');
  }

  /* ---------- 浮动按钮（不改动官方 DOM 结构，只叠加） ---------- */
  var bar = R.ui.el('div', 'position:fixed;right:12px;bottom:56px;z-index:2147482000;' +
    'display:flex;gap:6px;align-items:center;');
  function mkBtn(label, title, fn) {
    var b = R.ui.el('button', 'cursor:pointer;border:1px solid rgba(0,0,0,.10);' +
      'background:rgba(255,255,255,.92);color:#20303f;border-radius:9px;padding:6px 10px;' +
      'font:12px/1 system-ui,-apple-system,"Segoe UI",sans-serif;box-shadow:0 4px 14px rgba(0,0,0,.12);');
    b.textContent = label; b.title = title; b.onclick = fn;
    return b;
  }
  var HK = (R.config && R.config.hotkeys) || {};
  bar.appendChild(mkBtn('导出 MD', '导出当前会话为 Markdown（' + (HK['session.export'] || 'Ctrl+Alt+E') + '）', doExport));
  bar.appendChild(mkBtn('复制', '复制会话 Markdown（' + (HK['session.copy'] || 'Ctrl+Alt+C') + '）', doCopy));
  R.ready(function () {
    try { (document.body || document.documentElement).appendChild(bar); } catch (e) { }
  });

  document.addEventListener('keydown', function (e) {
    if (R.isHotkey(e, 'session.export')) { e.preventDefault(); doExport(); }
    else if (R.isHotkey(e, 'session.copy')) { e.preventDefault(); doCopy(); }
  }, true);

  R.log('会话工具就绪（导出 / 复制 Markdown）');
});
