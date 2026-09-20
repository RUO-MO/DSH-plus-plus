// ==DSHSkin==
// @name: wide-screen
// @title: 宽屏会话
// @description: Ctrl+Alt+W 在「默认 / 宽 / 全宽」三档间循环切换会话区宽度，长代码不再挤成窄条。
// @version: 1.0.0
// @author: DSHSkin
// @permissions: dom
// @source: DSHSkin 内置精选（随安装包分发）
// @icon: ⇔
// ==/DSHSkin==
(function () {
  'use strict';
  var R = window.DSHSkin;
  if (!R) return;

  var MODES = [
    { k: 'default', label: '默认' },
    { k: 'wide', max: '1200px', label: '宽 1200' },
    { k: 'full', max: '96%', label: '全宽' },
  ];

  function apply(idx) {
    var m = MODES[idx];
    var css = '';
    if (m.k !== 'default') {
      css = '[data-dsh-skin="chat"]{max-width:' + m.max + ' !important;margin:0 auto;width:100%;'
          + 'transition:max-width .25s ease}';
    }
    R.ui.style('dsh-mkt-wide', css);
    R.storage.set('wide-mode', String(idx));
  }

  R.def('market/wide-screen', function () {
    apply(Number(R.storage.get('wide-mode', '0')) || 0);
    document.addEventListener('keydown', function (e) {
      if (e.ctrlKey && e.altKey && (e.key === 'W' || e.key === 'w')) {
        var cur = Number(R.storage.get('wide-mode', '0')) || 0;
        var next = (cur + 1) % MODES.length;
        apply(next);
        R.toast('会话宽度：' + MODES[next].label);
      }
    });
    R.log('wide-screen 已加载（Ctrl+Alt+W 循环三档）');
  });
})();
