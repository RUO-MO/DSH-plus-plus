/* DSHSkin 内置增强模块：界面微调
 * ------------------------------------------------------------------
 * 能力：会话区宽度、消息疏密、正文字号、代码字体、隐藏冗余元素。
 * 做法：注入一个 <style id="dsh-skin-tweaks">，改动全部可逆（关掉模块或恢复默认即还原）。
 * 设置：Ctrl+Alt+U 打开设置面板（也支持右下角齿轮按钮），配置存 localStorage。
 * 选择器：全部走 DSHSkin 打标器产出的 [data-dsh-skin=*] 与 dsh 稳定 data 标记。
 */
DSHSkin.def('ui-tweaks', function (R) {
  var STORE_KEY = 'ui-tweaks';
  var STYLE_ID = 'dsh-skin-tweaks';
  var DEFAULTS = { width: 0, density: 'normal', fontSize: 100, monoCode: true, hideNoise: false };
  var cfg = Object.assign({}, DEFAULTS, R.storage.get(STORE_KEY, {}) || {});

  function build() {
    var s = ['/* ---- DSHSkin 界面微调 ---- */'];
    if (cfg.width > 0) {
      s.push('[data-dsh-skin="chat"]{max-width:' + cfg.width + 'px !important;' +
        'margin-left:auto !important;margin-right:auto !important;}');
      s.push('[data-dsh-skin="chat"] [data-conversation-scroll]{' +
        'max-width:' + cfg.width + 'px !important;margin-left:auto !important;margin-right:auto !important;}');
    }
    if (cfg.density === 'compact') {
      s.push('[data-chat-flow-kind]{padding-top:6px !important;padding-bottom:6px !important;}');
      s.push('[data-conversation-scroll] > [data-slot] > div > [data-slot]{margin-top:2px !important;margin-bottom:2px !important;}');
      s.push('[data-dsh-skin="chat"] p{margin:.45em 0 !important;}');
      s.push('[data-dsh-skin="chat"] li{margin:.18em 0 !important;}');
    } else if (cfg.density === 'loose') {
      s.push('[data-chat-flow-kind]{padding-top:16px !important;padding-bottom:16px !important;}');
    }
    if (cfg.fontSize && cfg.fontSize !== 100) {
      s.push('[data-chat-flow-kind]{font-size:' + (cfg.fontSize / 100) + 'em !important;}');
    }
    if (!cfg.monoCode) {
      s.push('[data-dsh-skin="chat"] code,[data-dsh-skin="chat"] pre{font-family:inherit !important;}');
    }
    if (cfg.hideNoise) {
      s.push('[data-chat-flow-kind] [class*="actions"],[data-chat-flow-kind] [class*="Actions"],' +
        '[data-dsh-skin="chat"] [class*="scrollToBottom"],[data-dsh-skin="chat"] [class*="scroll-to-bottom"]' +
        '{display:none !important;}');
    }
    return s.join('\n');
  }

  function apply() { R.ui.style(STYLE_ID, build()); }
  function persist() { R.storage.set(STORE_KEY, cfg); }
  apply();

  /* ---------- 设置面板 ---------- */
  var PANEL_ID = 'dsh-skin-tweaks-panel';
  var panel = null;

  function row(label, node) {
    var w = R.ui.el('div', 'display:flex;align-items:center;justify-content:space-between;gap:10px;padding:6px 0;');
    w.appendChild(R.ui.el('span', 'color:#4a5560;font-size:12px;', label));
    w.appendChild(node);
    return w;
  }

  function buildPanel() {
    var root = R.ui.el('div', 'position:fixed;z-index:2147482500;top:70px;right:20px;width:280px;' +
      'background:#fff;color:#1c2028;border:1px solid rgba(0,0,0,.08);border-radius:12px;' +
      'box-shadow:0 18px 50px rgba(0,0,0,.22);font:13px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;' +
      'padding:0;overflow:hidden;display:none;');
    root.id = PANEL_ID;
    var head = R.ui.el('div', 'display:flex;align-items:center;justify-content:space-between;' +
      'padding:10px 12px;border-bottom:1px solid #eceef2;font-weight:600;font-size:13px;', '界面微调');
    var close = R.ui.el('button', 'border:0;background:transparent;cursor:pointer;color:#8a94a0;font-size:14px;', '✕');
    close.onclick = function () { root.style.display = 'none'; };
    head.appendChild(close);
    var body = R.ui.el('div', 'padding:6px 12px 12px;');

    var range = document.createElement('input');
    range.type = 'range'; range.min = '0'; range.max = '1400'; range.step = '20';
    range.value = String(cfg.width);
    var rangeTip = R.ui.el('span', 'color:#8a94a0;font-size:11px;');
    rangeTip.textContent = cfg.width ? (cfg.width + 'px') : '自适应';
    range.oninput = function () {
      cfg.width = parseInt(range.value, 10) || 0;
      rangeTip.textContent = cfg.width ? (cfg.width + 'px') : '自适应';
      persist(); apply();
    };
    var rangeBox = R.ui.el('div', 'display:flex;align-items:center;gap:8px;');
    rangeBox.appendChild(range); rangeBox.appendChild(rangeTip);
    body.appendChild(row('会话区宽度', rangeBox));

    var sel = document.createElement('select');
    sel.style.cssText = 'font-size:12px;padding:3px 6px;border-radius:6px;border:1px solid #dfe3e8;background:#fff;color:#1c2028;';
    [['compact', '紧凑'], ['normal', '默认'], ['loose', '宽松']].forEach(function (o) {
      var op = document.createElement('option'); op.value = o[0]; op.textContent = o[1];
      if (cfg.density === o[0]) op.selected = true;
      sel.appendChild(op);
    });
    sel.onchange = function () { cfg.density = sel.value; persist(); apply(); };
    body.appendChild(row('消息疏密', sel));

    var fs = document.createElement('input');
    fs.type = 'range'; fs.min = '85'; fs.max = '130'; fs.step = '5'; fs.value = String(cfg.fontSize);
    var fsTip = R.ui.el('span', 'color:#8a94a0;font-size:11px;', cfg.fontSize + '%');
    fs.oninput = function () {
      cfg.fontSize = parseInt(fs.value, 10) || 100;
      fsTip.textContent = cfg.fontSize + '%';
      persist(); apply();
    };
    var fsBox = R.ui.el('div', 'display:flex;align-items:center;gap:8px;');
    fsBox.appendChild(fs); fsBox.appendChild(fsTip);
    body.appendChild(row('正文字号', fsBox));

    function toggle(label, key) {
      var cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = !!cfg[key]; cb.style.cssText = 'cursor:pointer;';
      cb.onchange = function () { cfg[key] = cb.checked; persist(); apply(); };
      return row(label, cb);
    }
    body.appendChild(toggle('等宽代码字体', 'monoCode'));
    body.appendChild(toggle('隐藏冗余元素', 'hideNoise'));

    var reset = R.ui.el('button', 'margin-top:10px;width:100%;cursor:pointer;border:1px solid #dfe3e8;' +
      'background:#f6f8fa;color:#334;border-radius:8px;padding:7px;font-size:12px;', '恢复默认');
    reset.onclick = function () {
      cfg = Object.assign({}, DEFAULTS);
      persist(); apply(); root.style.display = 'none';
      R.toast('界面微调已恢复默认');
    };
    body.appendChild(reset);

    root.appendChild(head); root.appendChild(body);
    return root;
  }

  function togglePanel() {
    if (!panel) {
      panel = buildPanel();
      (document.body || document.documentElement).appendChild(panel);
    }
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
  }

  R.ready(function () {
    var gear = R.ui.el('button', 'position:fixed;right:12px;bottom:16px;z-index:2147482000;cursor:pointer;' +
      'border:1px solid rgba(0,0,0,.10);background:rgba(255,255,255,.92);color:#20303f;border-radius:10px;' +
      'width:32px;height:32px;font:14px/1 system-ui,sans-serif;box-shadow:0 4px 14px rgba(0,0,0,.12);');
    gear.textContent = '⚙';
    var HK_TOGGLE = (R.config && R.config.hotkeys && R.config.hotkeys['ui-tweaks.toggle']) || 'Ctrl+Alt+U';
    gear.title = '界面微调（' + HK_TOGGLE + '）';
    gear.onclick = togglePanel;
    try { (document.body || document.documentElement).appendChild(gear); } catch (e) { }
  });

  document.addEventListener('keydown', function (e) {
    if (R.isHotkey(e, 'ui-tweaks.toggle')) {
      e.preventDefault(); togglePanel();
    }
  }, true);

  R.log('界面微调就绪（⚙ / ' + HK_TOGGLE + '）');
});
