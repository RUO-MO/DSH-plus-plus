// ==DSHSkin==
// @name: selection-counter
// @title: 划词统计
// @description: 选中会话文字后浮动显示字符数与 token 估算（中英分开计），写长 prompt 前先量一量。
// @version: 1.0.0
// @author: DSHSkin
// @permissions: dom
// @source: DSHSkin 内置精选（随安装包分发）
// @icon: 𝍢
// ==/DSHSkin==
(function () {
  'use strict';
  var R = window.DSHSkin;
  if (!R) return;

  function estimate(text) {
    var cjk = (text.match(/[\u4e00-\u9fff\u3400-\u4dbf]/g) || []).length;
    var latin = text.length - cjk;
    var tokens = Math.ceil(cjk * 0.6 + latin * 0.28);
    return { chars: text.length, cjk: cjk, latin: latin, tokens: tokens };
  }

  function ensureTip() {
    var tip = document.getElementById('dsh-sel-tip');
    if (tip) return tip;
    tip = document.createElement('div');
    tip.id = 'dsh-sel-tip';
    tip.style.cssText = 'position:fixed;z-index:2147483000;display:none;pointer-events:none;'
      + 'padding:4px 10px;border-radius:8px;font-size:11px;line-height:1.5;'
      + 'background:rgba(20,22,28,.92);color:#eef1f6;border:1px solid rgba(255,255,255,.14);'
      + 'box-shadow:0 4px 16px rgba(0,0,0,.3);font-family:var(--sans),sans-serif';
    document.body.appendChild(tip);
    return tip;
  }

  R.def('market/selection-counter', function () {
    var tip = ensureTip();
    document.addEventListener('mouseup', function () {
      setTimeout(function () {
        var sel = window.getSelection();
        var text = sel ? String(sel) : '';
        if (!text.trim()) { tip.style.display = 'none'; return; }
        var st = estimate(text);
        tip.textContent = st.chars + ' 字符 · 约 ' + st.tokens + ' tokens（中文 ' + st.cjk + ' / 西文 ' + st.latin + '）';
        var r = sel.getRangeAt(0).getBoundingClientRect();
        tip.style.display = 'block';
        tip.style.left = Math.max(8, Math.min(r.left + r.width / 2 - tip.offsetWidth / 2, innerWidth - tip.offsetWidth - 8)) + 'px';
        tip.style.top = Math.max(8, r.top - tip.offsetHeight - 8) + 'px';
      }, 10);
    });
    document.addEventListener('keyup', function (e) {
      if (e.shiftKey || e.key === 'Escape') {
        var sel = window.getSelection();
        if (!sel || !String(sel).trim()) tip.style.display = 'none';
      }
    });
    R.log('selection-counter 已加载');
  });
})();
