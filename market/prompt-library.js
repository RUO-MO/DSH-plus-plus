// ==DSHSkin==
// @name: prompt-library
// @title: 提示词库
// @description: Ctrl+Alt+L 打开内置提示词库（代码评审/重构/排查/测试等 8 条场景），点选即插入输入框；支持自定义库（存储 key: prompt-library）。
// @version: 1.0.0
// @author: DSHSkin
// @permissions: dom
// @source: DSHSkin 内置精选（随安装包分发）
// @icon: 📚
// ==/DSHSkin==
(function () {
  'use strict';
  var R = window.DSHSkin;
  if (!R) return;

  var DEFAULTS = [
    { t: '代码评审', p: '请以资深工程师视角评审以下代码，按「正确性 / 边界 / 性能 / 可读性」分组给出问题与修复建议，最后给一个总体评分：\n\n```\n\n```' },
    { t: '写单元测试', p: '为下面的函数编写单元测试：覆盖正常路径、边界条件与异常输入；使用项目现有测试框架风格；只输出测试代码文件。\n\n```\n\n```' },
    { t: '定位 Bug', p: '以下是报错信息与相关代码。请先列出 3 个最可能的根因（按概率排序），再给出每种的验证方法与修复补丁：\n\n报错：\n\n代码：' },
    { t: '重构建议', p: '请重构以下代码：保持行为不变，提升可读性与性能；先给重构后的完整代码，再用列表说明改了什么、为什么：\n\n```\n\n```' },
    { t: '方案设计', p: '我要实现以下需求。请给出 2 个可行方案，对比复杂度/扩展性/维护成本，给出推荐与落地步骤（含目录结构与关键接口）：\n\n需求：' },
    { t: '解释代码', p: '请逐段解释下面代码的作用与设计意图，标注潜在坑点，最后用一句话总结它在整个系统中的角色：\n\n```\n\n```' },
    { t: '写文档', p: '为以下代码/模块生成 README：包含用途、快速上手、API 说明、常见问题；示例要可直接运行：\n\n```\n\n```' },
    { t: '提交信息', p: '根据以下 diff 生成一条符合 Conventional Commits 的提交信息（type(scope): subject + body 要点），不超过 72 字符标题：\n\n```diff\n\n```' },
  ];

  function editor() {
    var sels = [
      '[data-composer-card="true"] [contenteditable="true"]',
      '[data-composer-seat] [contenteditable="true"]',
      '[data-composer-card="true"] textarea',
    ];
    for (var i = 0; i < sels.length; i++) {
      var el = document.querySelector(sels[i]);
      if (el) return el;
    }
    return null;
  }

  function insert(text) {
    var ed = editor();
    if (!ed) { R.toast('找不到输入框'); return; }
    ed.focus();
    if (ed.isContentEditable) {
      document.execCommand('insertText', false, text);
    } else {
      var s = ed.selectionStart || 0;
      ed.value = ed.value.slice(0, s) + text + ed.value.slice(ed.selectionEnd || s);
      ed.dispatchEvent(new Event('input', { bubbles: true }));
    }
    R.toast('已插入：' + text.slice(0, 24) + (text.length > 24 ? '…' : ''));
  }

  function list() {
    var custom = R.storage.get('prompt-library', null);
    return Array.isArray(custom) && custom.length ? custom : DEFAULTS;
  }

  function panel() {
    var old = document.getElementById('dsh-prompt-lib');
    if (old) { old.remove(); return; }
    var box = document.createElement('div');
    box.id = 'dsh-prompt-lib';
    box.style.cssText = 'position:fixed;z-index:2147483000;right:16px;top:64px;width:320px;max-height:70vh;overflow:auto;'
      + 'background:rgba(22,24,30,.97);color:#eef1f6;border:1px solid rgba(255,255,255,.14);border-radius:12px;'
      + 'box-shadow:0 12px 40px rgba(0,0,0,.4);padding:12px;font-family:var(--sans),sans-serif;font-size:12px';
    box.innerHTML = '<div style="display:flex;align-items:center;margin-bottom:8px">'
      + '<b>提示词库</b><span style="opacity:.55;margin-left:8px">Ctrl+Alt+L 开关</span>'
      + '<span id="dsh-pl-close" style="margin-left:auto;cursor:pointer;opacity:.6;padding:2px 8px">✕</span></div>'
      + list().map(function (it, i) {
        return '<div data-i="' + i + '" style="padding:8px 10px;margin:4px 0;border-radius:8px;cursor:pointer;'
          + 'background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08)">'
          + '<b>' + it.t + '</b><div style="opacity:.55;font-size:11px;margin-top:2px;'
          + 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + it.p.replace(/</g, '&lt;').slice(0, 60) + '</div></div>';
      }).join('');
    document.body.appendChild(box);
    box.querySelector('#dsh-pl-close').onclick = function () { box.remove(); };
    box.addEventListener('click', function (e) {
      var row = e.target.closest('[data-i]');
      if (!row) return;
      insert(list()[Number(row.getAttribute('data-i'))].p);
      box.remove();
    });
  }

  R.def('market/prompt-library', function () {
    document.addEventListener('keydown', function (e) {
      if (e.ctrlKey && e.altKey && (e.key === 'L' || e.key === 'l')) {
        e.preventDefault();
        panel();
      }
    });
    R.log('prompt-library 已加载（Ctrl+Alt+L）');
  });
})();
