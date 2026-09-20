/* DSHSkin 内置增强模块：思维链中文
 * ------------------------------------------------------------------
 * 能力：让 DeepSeek Harness 的思考链变成中文 —— 发送前向消息末尾追加一条
 *       隐形指令（零宽字符包裹），要求模型全程用中文进行思考与推理。
 * 做法：捕获输入区 Enter 发送 / 发送按钮点击，注入指令后放行原生发送，
 *       不 preventDefault，不拦截官方流程，不触碰官方代码与接口。
 * 去重：消息已含指令标记时不重复注入；输入区清空后自动复位。
 * 指令正文可在面板增强页左侧用户脚本窗口用一行 localStorage 覆盖：
 *   DSHSkin.storage.set('thinking-zh-instr', '你的自定义指令')
 */
DSHSkin.def('thinking-zh', function (R) {
  var MARK = '\u200b';
  var DEFAULT_INSTR = '【内部指令】请全程使用中文进行思考与推理，推理过程与最终回答均使用中文。';

  function currentInstr() {
    var t = R.storage.get('thinking-zh-instr', '');
    return (typeof t === 'string' && t.trim()) ? t : DEFAULT_INSTR;
  }

  function q(sel) { try { return document.querySelectorAll(sel); } catch (e) { return []; } }
  function visible(el) {
    try { var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; } catch (e) { return false; }
  }
  function isComposer(el) {
    if (!el || !el.closest) return false;
    try {
      if (el.closest('[data-composer-card="true"]') || el.closest('[data-composer-seat]')) return true;
      return !!el.closest('[contenteditable="true"]');
    } catch (e) { return false; }
  }
  function composer() {
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

  function ensureInjected(ed) {
    if (!ed) return;
    var val = ed.tagName === 'TEXTAREA' || ed.tagName === 'INPUT' ? ed.value : (ed.innerText || '');
    val = String(val || '');
    if (!val.trim()) return;
    if (val.indexOf(MARK) >= 0) return;          // 已注入过，不重复
    var ins = '\n' + MARK + currentInstr() + MARK;
    if (ed.tagName === 'TEXTAREA' || ed.tagName === 'INPUT') {
      ed.value = val.replace(/\s+$/, '') + ins;
      ed.selectionStart = ed.selectionEnd = ed.value.length;
      ed.dispatchEvent(new Event('input', { bubbles: true }));
    } else {
      try {
        ed.focus();
        var sel = window.getSelection();
        var rng = document.createRange();
        rng.selectNodeContents(ed); rng.collapse(false);   // 光标移到末尾
        sel.removeAllRanges(); sel.addRange(rng);
        document.execCommand('insertText', false, ins);
      } catch (e) { R.log('思维链指令注入失败', String(e)); }
    }
  }

  // 捕获 Enter（无修饰键）发送前注入，放行原生发送
  document.addEventListener('keydown', function (e) {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key !== 'Enter' || e.shiftKey || e.ctrlKey || e.altKey) return;
    if (!isComposer(e.target)) return;
    ensureInjected(e.target);
  }, true);

  // 捕获发送按钮点击，注入后放行原生 click
  document.addEventListener('click', function (e) {
    if (!isComposer(e.target)) return;
    var b = e.target && e.target.closest ? e.target.closest('button') : null;
    if (!b) return;
    var sig = String(b.title || '') + ' ' + String(b.getAttribute('aria-label') || '') + ' ' +
      String(b.className || '') + ' ' + String(b.textContent || '');
    if (/send|发送|submit/i.test(sig)) {
      ensureInjected(composer());
    }
  }, true);

  R.log('思维链中文就绪（发送时自动注入中文思考指令，可 open 面板增强页关闭）');
});