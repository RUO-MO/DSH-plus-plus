/* DSHSkin 内置增强模块：输入增强
 * ------------------------------------------------------------------
 * 能力：
 *   1) 纯文本粘贴   —— 从网页/文档复制内容时自动剥离富文本格式
 *   2) 提示词片段   —— Ctrl+Alt+/ 打开片段面板，一键插入常用提示词
 *   3) 快捷发送     —— Ctrl+Alt+Enter 直接发送（找不到发送按钮时回退为模拟回车）
 *   4) 输入历史     —— Alt+↑ / Alt+↓ 翻阅本会话发过的输入
 * 说明：全部基于事件监听 + execCommand，不改动官方 DOM 结构。
 * 选择器：输入框定位走 [data-composer-card] 稳定标记，DSH 升级也可用。
 */
DSHSkin.def('input-plus', function (R) {
  var STORE_SNIP = 'snippets';
  var STORE_HIST = 'input-history';
  var DEFAULT_SNIPPETS = [
    { t: '解释代码', c: '请逐行解释这段代码的作用、依赖与潜在问题：\n\n' },
    { t: '写单元测试', c: '为下面的函数补全单元测试，覆盖边界值与异常分支：\n\n' },
    { t: '性能优化', c: '分析这段代码的性能瓶颈，给出可落地的优化方案：\n\n' },
    { t: '代码审查', c: '以严格的 code review 视角审查这段改动，指出风险、缺陷与改进点：\n\n' },
    { t: '生成提交信息', c: '根据以下改动生成规范的 commit message（中文一版、英文一版）：\n\n' },
    { t: '写注释', c: '为下面的代码补充清晰的中文注释，不要改动逻辑：\n\n' }
  ];
  var snippets = R.storage.get(STORE_SNIP, null);
  if (!snippets || !snippets.length) snippets = DEFAULT_SNIPPETS.slice();
  var history = R.storage.get(STORE_HIST, []) || [];
  var histIdx = -1;
  var lastEditable = null;

  function q(sel) { try { return document.querySelectorAll(sel); } catch (e) { return []; } }
  function visible(el) {
    try { var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; } catch (e) { return false; }
  }
  function isComposer(el) {
    if (!el || !el.closest) return false;
    try {
      if (el.closest('[data-composer-card="true"]') || el.closest('[data-composer-seat]')) return true;
      var c = el.closest('[contenteditable="true"]');
      return !!c;
    } catch (e) { return false; }
  }
  function composer() {
    if (lastEditable && lastEditable.isConnected && visible(lastEditable)) return lastEditable;
    var sels = ['[data-composer-card="true"] textarea', '[data-composer-card="true"] [contenteditable="true"]',
      '[data-composer-seat] textarea', '[data-composer-seat] [contenteditable="true"]',
      'textarea', '[contenteditable="true"]'];
    for (var i = 0; i < sels.length; i++) {
      var els = q(sels[i]);
      for (var j = 0; j < els.length; j++) {
        if (visible(els[j]) && isComposer(els[j])) return els[j];
      }
    }
    return null;
  }
  function insert(text, opts) {
    var ed = composer();
    if (!ed) { R.toast('没找到输入框'); return false; }
    try {
      ed.focus();
      if (ed.tagName === 'TEXTAREA' || ed.tagName === 'INPUT') {
        var st = ed.selectionStart == null ? ed.value.length : ed.selectionStart;
        var en = ed.selectionEnd == null ? st : ed.selectionEnd;
        ed.value = ed.value.slice(0, st) + text + ed.value.slice(en);
        ed.selectionStart = ed.selectionEnd = st + text.length;
        ed.dispatchEvent(new Event('input', { bubbles: true }));
      } else {
        document.execCommand('insertText', false, text);
      }
      if (opts && opts.toast !== false) R.toast('已插入');
      return true;
    } catch (e) { R.log('插入失败', String(e)); R.toast('插入失败'); return false; }
  }

  /* ---------- 1) 纯文本粘贴 ---------- */
  document.addEventListener('paste', function (e) {
    var t = e.target;
    if (!isComposer(t)) return;
    var dt = e.clipboardData || window.clipboardData;
    if (!dt) return;
    var text = dt.getData('text/plain');
    if (text == null) return;
    var html = dt.getData('text/html');
    if (!html) return;                      // 本来就是纯文本，交给浏览器默认行为
    try {
      e.preventDefault(); e.stopPropagation();
      if (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT') {
        var st = t.selectionStart == null ? t.value.length : t.selectionStart;
        var en = t.selectionEnd == null ? st : t.selectionEnd;
        t.value = t.value.slice(0, st) + text + t.value.slice(en);
        t.selectionStart = t.selectionEnd = st + text.length;
        t.dispatchEvent(new Event('input', { bubbles: true }));
      } else {
        document.execCommand('insertText', false, text);
      }
      R.toast('已转为纯文本粘贴');
    } catch (err) { R.log('纯文本粘贴失败', String(err)); }
  }, true);

  /* ---------- 4) 输入历史记录（在输入框里按 Enter 发送时记一笔） ---------- */
  document.addEventListener('keydown', function (e) {
    var t = e.target;
    if (!isComposer(t)) return;
    if (e.isComposing || e.keyCode === 229) return;
    if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      if (!history.length) return;
      e.preventDefault();
      if (e.key === 'ArrowUp') histIdx = Math.min(histIdx + 1, history.length - 1);
      else histIdx = Math.max(histIdx - 1, -1);
      var text = histIdx < 0 ? '' : history[histIdx];
      if (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT') {
        t.value = text; t.selectionStart = t.selectionEnd = text.length;
        t.dispatchEvent(new Event('input', { bubbles: true }));
      } else {
        var sel = window.getSelection();
        if (sel) {
          var rng = document.createRange();
          rng.selectNodeContents(t);
          sel.removeAllRanges(); sel.addRange(rng);
          document.execCommand('insertText', false, text);
        }
      }
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.altKey) {
      var cur = t.tagName === 'TEXTAREA' || t.tagName === 'INPUT' ? t.value : (t.innerText || t.textContent || '');
      cur = String(cur).trim();
      if (cur) {
        if (history[0] !== cur) history.unshift(cur);
        if (history.length > 50) history.length = 50;
        histIdx = -1;
        R.storage.set(STORE_HIST, history);
      }
    }
  }, true);

  /* ---------- 3) 快捷发送 ---------- */
  function send() {
    var scopes = q('[data-composer-card="true"], [data-composer-seat]');
    for (var i = 0; i < scopes.length; i++) {
      var btns = scopes[i].querySelectorAll('button');
      for (var j = 0; j < btns.length; j++) {
        var b = btns[j];
        var sig = String(b.title || '') + ' ' + String(b.getAttribute('aria-label') || '') + ' ' +
          String(b.className || '') + ' ' + String(b.textContent || '');
        if (visible(b) && !b.disabled && /send|发送|submit|enter/i.test(sig)) {
          b.click(); R.log('已点击发送按钮'); return true;
        }
      }
    }
    var ed = composer();
    if (ed) {
      ed.focus();
      try {
        ed.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
        R.log('发送按钮未找到，已模拟回车');
        return true;
      } catch (e) { R.log('模拟回车失败', String(e)); }
    }
    R.toast('找不到发送方式');
    return false;
  }

  /* ---------- 2) 提示词片段面板 ---------- */
  var PANEL_ID = 'dsh-skin-snippets';
  var panel = null;
  function persist() { R.storage.set(STORE_SNIP, snippets); }

  function buildPanel() {
    var root = R.ui.el('div', 'position:fixed;z-index:2147482600;left:50%;top:18%;transform:translateX(-50%);' +
      'width:420px;max-height:60vh;display:none;flex-direction:column;background:#fff;color:#1c2028;' +
      'border:1px solid rgba(0,0,0,.08);border-radius:14px;box-shadow:0 22px 60px rgba(0,0,0,.26);' +
      'font:13px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;overflow:hidden;');
    root.id = PANEL_ID;

    var head = R.ui.el('div', 'display:flex;align-items:center;justify-content:space-between;' +
      'padding:10px 12px;border-bottom:1px solid #eceef2;font-weight:600;', '提示词片段');
    var close = R.ui.el('button', 'border:0;background:transparent;cursor:pointer;color:#8a94a0;', '✕');
    close.onclick = function () { root.style.display = 'none'; };
    head.appendChild(close);

    var search = document.createElement('input');
    search.placeholder = '搜索片段…';
    search.style.cssText = 'margin:10px 12px 4px;padding:7px 10px;border:1px solid #dfe3e8;border-radius:8px;font-size:12px;outline:none;';
    var list = R.ui.el('div', 'overflow:auto;padding:4px 8px 10px;');

    function render() {
      var kw = String(search.value || '').trim().toLowerCase();
      list.innerHTML = '';
      var shown = snippets.filter(function (s) {
        return !kw || (s.t + ' ' + s.c).toLowerCase().indexOf(kw) >= 0;
      });
      if (!shown.length) {
        list.appendChild(R.ui.el('div', 'padding:14px 8px;color:#8a94a0;font-size:12px;', '没有匹配的片段'));
      }
      shown.forEach(function (s) {
        var item = R.ui.el('div', 'padding:8px 10px;border-radius:9px;cursor:pointer;');
        item.onmouseenter = function () { item.style.background = '#f3f6f9'; };
        item.onmouseleave = function () { item.style.background = 'transparent'; };
        item.appendChild(R.ui.el('div', 'font-weight:600;font-size:12.5px;', s.t || '(未命名)'));
        item.appendChild(R.ui.el('div', 'color:#6d7883;font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;',
          String(s.c || '').replace(/\n/g, ' ').slice(0, 80)));
        item.onclick = function () {
          if (insert(s.c || '')) { root.style.display = 'none'; }
        };
        list.appendChild(item);
      });
    }
    search.oninput = render;

    var foot = R.ui.el('div', 'display:flex;gap:8px;padding:8px 12px;border-top:1px solid #eceef2;background:#fafbfc;');
    var add = R.ui.el('button', 'cursor:pointer;border:1px solid #dfe3e8;background:#fff;border-radius:8px;' +
      'padding:5px 10px;font-size:12px;color:#334;', '＋ 用选中文本新建');
    add.onclick = function () {
      var selText = '';
      try { selText = String(window.getSelection ? window.getSelection().toString() : '') || ''; } catch (e) { }
      var name = window.prompt('片段名称：', '新片段');
      if (name == null) return;
      var content = window.prompt('片段内容（可先在 DeepSeek Harness 里选中文本再点此按钮自动带入）：', selText);
      if (content == null || !content.trim()) return;
      snippets.unshift({ t: name.trim() || '新片段', c: content });
      persist(); render(); R.toast('片段已保存');
    };
    var resetSnip = R.ui.el('button', 'cursor:pointer;border:1px solid #dfe3e8;background:#fff;border-radius:8px;' +
      'padding:5px 10px;font-size:12px;color:#334;', '恢复内置片段');
    resetSnip.onclick = function () {
      snippets = DEFAULT_SNIPPETS.slice(); persist(); render(); R.toast('已恢复内置片段');
    };
    foot.appendChild(add); foot.appendChild(resetSnip);

    root.appendChild(head); root.appendChild(search); root.appendChild(list); root.appendChild(foot);
    root.__render = render;
    render();
    return root;
  }

  function togglePanel() {
    if (!panel) {
      panel = buildPanel();
      (document.body || document.documentElement).appendChild(panel);
    }
    panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    if (panel.style.display === 'flex') {
      var s = panel.querySelector('input');
      if (s) { s.value = ''; setTimeout(function () { s.focus(); }, 30); }
      var render = panel.__render; if (typeof render === 'function') render();
    }
  }

  /* ---------- 焦点跟踪 + 快捷键 ---------- */
  document.addEventListener('focusin', function (e) {
    if (isComposer(e.target)) lastEditable = e.target;
  }, true);

  document.addEventListener('keydown', function (e) {
    if (R.isHotkey(e, 'input.snippets')) { e.preventDefault(); togglePanel(); }
    else if (R.isHotkey(e, 'input.send')) { e.preventDefault(); send(); }
  }, true);

  var _hk = (R.config && R.config.hotkeys) || {};
  R.log('输入增强就绪（纯文本粘贴 / 片段 ' + (_hk['input.snippets'] || 'Ctrl+Alt+/') + ' / 发送 ' + (_hk['input.send'] || 'Ctrl+Alt+Enter') + ' / 历史 Alt+↑↓）');
});
