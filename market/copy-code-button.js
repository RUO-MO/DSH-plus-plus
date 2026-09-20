// ==DSHSkin==
// @name: copy-code-button
// @title: 代码块一键复制
// @description: 给会话里每个代码块右上角加「复制」按钮，点击复制代码内容；快捷键 Ctrl+Shift+C 复制最近一个代码块。
// @version: 1.0.0
// @author: DSHSkin
// @permissions: dom, clipboard
// @source: DSHSkin 内置精选（随安装包分发）
// @icon: ⧉
// ==/DSHSkin==
(function () {
  'use strict';
  var R = window.DSHSkin;
  if (!R) return;

  function codeBlocks() {
    var sels = ['[data-conversation-scroll] pre', '[data-conversation-scroll] .code-block', '[data-dsh-skin="chat"] pre'];
    for (var i = 0; i < sels.length; i++) {
      var els = document.querySelectorAll(sels[i]);
      if (els.length) return els;
    }
    return [];
  }

  function copy(text) {
    var done = function () { R.toast('代码已复制'); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallback(text); done(); });
    } else { fallback(text); done(); }
  }
  function fallback(text) {
    var ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    ta.remove();
  }

  function decorate() {
    var blocks = codeBlocks();
    for (var i = 0; i < blocks.length; i++) {
      var pre = blocks[i];
      if (pre.getAttribute('data-dsh-copy')) continue;
      pre.setAttribute('data-dsh-copy', '1');
      pre.style.position = pre.style.position || 'relative';
      var btn = document.createElement('button');
      btn.textContent = '复制';
      btn.setAttribute('data-dsh-copy-btn', '1');
      btn.style.cssText = 'position:absolute;top:6px;right:8px;z-index:9;padding:2px 10px;font-size:11px;'
        + 'border-radius:6px;border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.08);'
        + 'color:inherit;cursor:pointer;opacity:.55;transition:opacity .15s';
      btn.addEventListener('mouseenter', function () { this.style.opacity = '1'; });
      btn.addEventListener('mouseleave', function () { this.style.opacity = '.55'; });
      btn.addEventListener('click', function (ev) {
        ev.stopPropagation(); ev.preventDefault();
        var host = this.parentNode;
        var clone = host.cloneNode(true);
        clone.querySelectorAll('[data-dsh-copy-btn]').forEach(function (b) { b.remove(); });
        copy(clone.innerText.replace(/\n$/, ''));
      });
      pre.appendChild(btn);
    }
  }

  R.def('market/copy-code-button', function () {
    R.ui.style('dsh-mkt-copy', '');
    R.on('[data-conversation-scroll]', function () { setTimeout(decorate, 300); });
    R.interval(decorate, 2500);
    decorate();
    document.addEventListener('keydown', function (e) {
      if (e.ctrlKey && e.shiftKey && (e.key === 'C' || e.key === 'c')) {
        var blocks = codeBlocks();
        if (blocks.length) {
          var clone = blocks[blocks.length - 1].cloneNode(true);
          clone.querySelectorAll('[data-dsh-copy-btn]').forEach(function (b) { b.remove(); });
          copy(clone.innerText.replace(/\n$/, ''));
        }
      }
    });
    R.log('copy-code-button 已加载');
  });
})();
