// ==DSHSkin==
// @name: message-index
// @title: 消息序号标注
// @description: 给会话里每条消息左上角加序号徽标（#1、#2…），引用讨论时说「第 N 条」即可；悬停徽标显示角色。
// @version: 1.0.0
// @author: DSHSkin
// @permissions: dom
// @source: DSHSkin 内置精选（随安装包分发）
// @icon: #
// ==/DSHSkin==
(function () {
  'use strict';
  var R = window.DSHSkin;
  if (!R) return;

  function roleOf(el) {
    var k = String(el.getAttribute('data-chat-flow-kind') || '').toLowerCase();
    if (k.indexOf('user') === 0 || k === 'steering') return '用户';
    if (k.indexOf('assistant') === 0 || k === 'turn-process') return '助手';
    if (k === 'system-prompt' || k === 'context') return '系统';
    return k || '消息';
  }

  function decorate() {
    var nodes = document.querySelectorAll('[data-chat-flow-kind]');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.getAttribute('data-dsh-idx')) continue;
      el.setAttribute('data-dsh-idx', String(i + 1));
      el.style.position = el.style.position || 'relative';
      var badge = document.createElement('span');
      badge.textContent = '#' + (i + 1);
      badge.setAttribute('data-dsh-idx-badge', '1');
      badge.title = roleOf(el);
      badge.style.cssText = 'position:absolute;top:-10px;left:2px;z-index:5;padding:0 7px;height:17px;'
        + 'line-height:17px;border-radius:9px;font-size:10px;font-family:var(--mono),monospace;'
        + 'background:rgba(120,130,255,.16);border:1px solid rgba(120,130,255,.35);opacity:.65;'
        + 'color:inherit;cursor:default;transition:opacity .15s';
      badge.addEventListener('mouseenter', function () { this.style.opacity = '1'; });
      badge.addEventListener('mouseleave', function () { this.style.opacity = '.65'; });
      el.appendChild(badge);
    }
  }

  R.def('market/message-index', function () {
    R.on('[data-conversation-scroll]', function () { setTimeout(decorate, 300); });
    R.interval(decorate, 3000);
    decorate();
    R.log('message-index 已加载');
  });
})();
