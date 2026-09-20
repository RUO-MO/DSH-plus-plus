// ==DSHSkin==
// @name: focus-mode
// @title: 专注模式
// @description: Ctrl+Alt+F 一键淡出左右侧栏（背景透出），再按一次或 Esc 恢复；状态记忆，刷新页面保持。
// @version: 1.0.0
// @author: DSHSkin
// @permissions: dom
// @source: DSHSkin 内置精选（随安装包分发）
// @icon: ◎
// ==/DSHSkin==
(function () {
  'use strict';
  var R = window.DSHSkin;
  if (!R) return;

  var STYLE = `
    body[data-dsh-focus="1"] [data-dsh-skin="sidebar"],
    body[data-dsh-focus="1"] [data-rightbar-col]{
      opacity:.06 !important; pointer-events:none !important;
      transition:opacity .25s ease !important;
    }
    body[data-dsh-focus="1"]{ --dsh-focus-on:1; }
  `;

  function set(on) {
    if (on) document.body.setAttribute('data-dsh-focus', '1');
    else document.body.removeAttribute('data-dsh-focus');
    R.storage.set('focus-mode', on ? '1' : '');
  }

  R.def('market/focus-mode', function () {
    R.ui.style('dsh-mkt-focus', STYLE);
    if (R.storage.get('focus-mode', '') === '1') set(true);
    document.addEventListener('keydown', function (e) {
      if (e.ctrlKey && e.altKey && (e.key === 'F' || e.key === 'f')) {
        set(!document.body.hasAttribute('data-dsh-focus'));
        R.toast(document.body.hasAttribute('data-dsh-focus') ? '专注模式：开' : '专注模式：关');
      } else if (e.key === 'Escape' && document.body.hasAttribute('data-dsh-focus')) {
        set(false);
        R.toast('专注模式：关');
      }
    });
    R.log('focus-mode 已加载（Ctrl+Alt+F 切换，Esc 退出）');
  });
})();
