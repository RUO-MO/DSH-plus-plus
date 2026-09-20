// ==DSHSkin==
// @name: night-schedule
// @title: 夜间自动暗色
// @description: 每天 22:00–07:00 自动挂暗色标记（data-ds-dark-theme），白天自动还原；可被皮肤 force_dark 覆盖，互不打架。
// @version: 1.0.0
// @author: DSHSkin
// @permissions: dom
// @source: DSHSkin 内置精选（随安装包分发）
// @icon: 🌙
// ==/DSHSkin==
(function () {
  'use strict';
  var R = window.DSHSkin;
  if (!R) return;

  function inNight(d) {
    var h = (d || new Date()).getHours();
    return h >= 22 || h < 7;
  }

  function apply() {
    // 皮肤 force_dark（打标器）持有最高优先级：它已挂标记时不抢
    if (document.body.getAttribute('data-ds-dark-theme') !== null) return;
    if (inNight()) document.body.setAttribute('data-ds-dark-theme', '');
    else document.body.removeAttribute('data-ds-dark-theme');
  }

  R.def('market/night-schedule', function () {
    R.interval(apply, 60000);
    apply();
    R.log('night-schedule 已加载（22:00–07:00 自动暗色）');
  });
})();
