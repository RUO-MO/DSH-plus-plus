// DSHSkin · 快照页注入逻辑（打标器 + 外观/侧栏控制）
// 换肤已移除，本脚本只保留「打标器」用途：给快照页打 data-dsh-skin 区域标记，
// 供选择器漂移巡检（probe/doctor）在离线快照上复现区域结构。
(function () {
  var previewDark = false;  // 预览器选择的外观（深色/浅色）

  // ===== 打标器：给真实 DOM 打 data-dsh-skin 标记（稳定区域契约） =====
  function run() {
    function mark(k, fn) {
      try { var el = fn(); if (el && !el.getAttribute('data-dsh-skin')) el.setAttribute('data-dsh-skin', k); } catch (e) {}
    }
    var frame = document.querySelector('.KDVgQq_frame');
    var overlay = document.querySelector('.Vt7x3G_overlay');
    if (frame) {
      mark('frame', function () { return frame; });
      var col = frame.children[0];
      mark('sidebar', function () {
        var s = col && col.querySelector('[data-slot="sidebar"] > div');
        if (!s || !s.children || !s.children.length) s = col && col.querySelector('[data-slot] > div');
        return (s && s.children && s.children.length) ? s : col;
      });
      mark('rightbar', function () { return frame.querySelector('[data-rightbar-col]'); });
      mark('overlay', function () { return overlay; });
    }
    mark('chat', function () { return document.querySelector('[data-phase]:not([contenteditable])'); });
    mark('scroll', function () { return document.querySelector('[data-conversation-scroll]'); });
    mark('composerbar', function () { return document.querySelector('[data-composer-seat]'); });
    mark('composer', function () { return document.querySelector('[data-composer-card="true"]'); });
    mark('sessionhead', function () { return document.querySelector('[data-slot="conversation.session.header"]'); });
  }
  function markAll() {
    if (window.__SNAP_BOOT__) { run(); return; }
    window.__SNAP_BOOT__ = 1;
    run();
    if (window.setInterval) setInterval(run, 1200);
    try { window.addEventListener('load', run); } catch (e) {}
  }
  window.markAll = markAll;

  // ===== 外观：只切 DSH 暗色标记（供双轨 CSS 预演） =====
  function applyDark() {
    var dark = !!previewDark;
    try {
      if (dark) document.body.setAttribute('data-ds-dark-theme', '');
      else document.body.removeAttribute('data-ds-dark-theme');
    } catch (e) {}
  }

  // ===== 父窗口控制（只接受同源消息） =====
  window.addEventListener('message', function (ev) {
    var sameOrigin = (ev.origin === location.origin) ||
                     (ev.origin === 'null' && String(location.origin) === 'null');
    if (!sameOrigin) return;
    var d = ev.data || {};
    if (d.type === 'dshskin:appearance') {
      previewDark = (d.appearance === 'dark');
      applyDark();
    } else if (d.type === 'dshskin:sidebar') {
      var sb = document.querySelector('[data-dsh-skin="sidebar"]');
      if (sb) sb.style.display = d.mode === 'collapse' ? 'none' : '';
    }
  });

  markAll();
})();
