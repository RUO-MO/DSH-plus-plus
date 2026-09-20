/* DSHSkin 内置增强模块：上下文 / 用量
 * ------------------------------------------------------------------
 * 能力：实时估算当前会话的字符数、消息数与 token 占用量，右下角常驻小胶囊，
 *       点击展开明细（中英文分别计数、上下文窗口占比、可调窗口大小）。
 * 算法：中日韩字符 ≈ 1 token/字；其余字符 ≈ 1 token/4 字符（近似，仅供估算）。
 * 说明：纯前端统计 DOM 文本（消息容器走 [data-chat-flow-kind] 稳定标记），
 *       不调用任何官方接口、不上传任何数据。
 */
DSHSkin.def('usage-meter', function (R) {
  var STORE_KEY = 'usage-meter';
  var cfg = Object.assign({ window: 128000, show: true }, R.storage.get(STORE_KEY, {}) || {});
  var TURN_SEL = ['[data-chat-flow-kind]', '[data-chat-turn]'];
  var PILL_ID = 'dsh-skin-usage';
  var DETAIL_ID = 'dsh-skin-usage-detail';

  function q(sel) { try { return document.querySelectorAll(sel); } catch (e) { return []; } }
  function visible(el) {
    try { var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; } catch (e) { return false; }
  }
  function turns() {
    var out = [], seen = [];
    for (var i = 0; i < TURN_SEL.length; i++) {
      var nodes = q(TURN_SEL[i]);
      for (var j = 0; j < nodes.length; j++) {
        if (seen.indexOf(nodes[j]) < 0 && visible(nodes[j])) { seen.push(nodes[j]); out.push(nodes[j]); }
      }
    }
    return out;
  }
  function measure() {
    var list = turns(), chars = 0, cjk = 0, latin = 0, msgs = list.length;
    for (var i = 0; i < list.length; i++) {
      var t = String(list[i].innerText || list[i].textContent || '');
      chars += t.length;
      for (var k = 0; k < t.length; k++) {
        var c = t.charCodeAt(k);
        if (c >= 0x2E80 && c <= 0x9FFF || c >= 0xF900 && c <= 0xFAFF || c >= 0xFF00 && c <= 0xFF60) cjk++;
        else if (c > 127) cjk++;
      }
    }
    latin = Math.max(0, chars - cjk);
    var tokens = Math.round(cjk + latin / 4);
    return { chars: chars, cjk: cjk, latin: latin, tokens: tokens, msgs: msgs };
  }
  function fmt(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k';
    return String(n);
  }

  var pill = R.ui.el('div', 'position:fixed;right:56px;bottom:16px;z-index:2147482100;display:none;' +
    'align-items:center;gap:6px;cursor:pointer;padding:5px 10px;border-radius:999px;' +
    'background:rgba(255,255,255,.92);color:#31404c;border:1px solid rgba(0,0,0,.10);' +
    'font:11.5px/1 system-ui,-apple-system,"Segoe UI",sans-serif;box-shadow:0 4px 14px rgba(0,0,0,.12);' +
    'backdrop-filter:blur(8px);user-select:none;');
  pill.id = PILL_ID;
  pill.title = '点击查看上下文用量明细';

  var detail = null, timer = null;

  function renderPill(st) {
    var pct = cfg.window ? Math.min(999, Math.round(st.tokens / cfg.window * 1000) / 10) : 0;
    var color = pct >= 80 ? '#c0392b' : pct >= 50 ? '#b8860b' : '#3c5a4a';
    pill.innerHTML = '<span style="width:6px;height:6px;border-radius:50%;display:inline-block;background:' + color + ';"></span>' +
      '≈' + fmt(st.tokens) + ' tokens · ' + st.msgs + ' 条' +
      (cfg.window ? ' · <span style="color:' + color + ';">' + pct + '%</span>' : '');
  }

  function buildDetail() {
    var root = R.ui.el('div', 'display:none;position:fixed;right:12px;bottom:52px;z-index:2147482200;width:250px;' +
      'background:#fff;color:#1c2028;border:1px solid rgba(0,0,0,.08);border-radius:12px;' +
      'box-shadow:0 18px 50px rgba(0,0,0,.22);font:12.5px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;overflow:hidden;');
    root.id = DETAIL_ID;
    var head = R.ui.el('div', 'display:flex;align-items:center;justify-content:space-between;' +
      'padding:9px 12px;border-bottom:1px solid #eceef2;font-weight:600;font-size:12.5px;', '上下文用量');
    var close = R.ui.el('button', 'border:0;background:transparent;cursor:pointer;color:#8a94a0;', '✕');
    close.onclick = function () { root.style.display = 'none'; };
    head.appendChild(close);
    var body = R.ui.el('div', 'padding:8px 12px 12px;');
    root.appendChild(head); root.appendChild(body);
    root.__body = body;
    return root;
  }

  function rowHtml(k, v, accent) {
    return '<div style="display:flex;justify-content:space-between;padding:2px 0;">' +
      '<span style="color:#6d7883;">' + k + '</span>' +
      '<span style="font-variant-numeric:tabular-nums;' + (accent ? 'color:#3c5a4a;font-weight:600;' : '') + '">' + v + '</span></div>';
  }

  function renderDetail(st) {
    if (!detail) return;
    var body = detail.__body;
    var pct = cfg.window ? Math.min(999, Math.round(st.tokens / cfg.window * 1000) / 10) : 0;
    body.innerHTML =
      rowHtml('估算 tokens', fmt(st.tokens), true) +
      rowHtml('消息条数', st.msgs) +
      rowHtml('总字符数', fmt(st.chars)) +
      rowHtml('中日韩字符', fmt(st.cjk)) +
      rowHtml('其它字符', fmt(st.latin)) +
      rowHtml('上下文占比', cfg.window ? (pct + '%') : '未设置') +
      '<div style="margin-top:8px;height:6px;border-radius:4px;background:#eef1f4;overflow:hidden;">' +
      '<div style="height:100%;width:' + Math.min(100, pct) + '%;background:' +
      (pct >= 80 ? '#c0392b' : pct >= 50 ? '#d4a017' : '#3c5a4a') + ';"></div></div>' +
      '<div style="margin-top:10px;display:flex;align-items:center;gap:8px;">' +
      '<span style="color:#6d7883;">窗口大小</span>' +
      '<select id="dsh-skin-usage-win" style="flex:1;font-size:12px;padding:3px 6px;border-radius:6px;' +
      'border:1px solid #dfe3e8;background:#fff;color:#1c2028;">' +
      [32000, 64000, 128000, 200000, 256000, 1000000].map(function (w) {
        return '<option value="' + w + '"' + (cfg.window === w ? ' selected' : '') + '>' + fmt(w) + '</option>';
      }).join('') +
      '<option value="0"' + (!cfg.window ? ' selected' : '') + '>不显示</option></select></div>' +
      '<div style="margin-top:7px;color:#98a2ad;font-size:11px;">估算值，仅供参考；不采集、不上传。</div>';
    var sel = body.querySelector('#dsh-skin-usage-win');
    if (sel) {
      sel.onchange = function () {
        cfg.window = parseInt(sel.value, 10) || 0;
        R.storage.set(STORE_KEY, cfg);
        refresh();
      };
    }
  }

  function refresh() {
    var st = measure();
    renderPill(st);
    if (detail && detail.style.display !== 'none') renderDetail(st);
    return st;
  }

  var st0 = refresh();
  if (cfg.show) pill.style.display = 'inline-flex';

  pill.onclick = function () {
    if (!detail) {
      detail = buildDetail();
      (document.body || document.documentElement).appendChild(detail);
    }
    var open = detail.style.display === 'none';
    detail.style.display = open ? 'block' : 'none';
    if (open) renderDetail(measure());
  };

  R.ready(function () {
    try { (document.body || document.documentElement).appendChild(pill); } catch (e) { }
  });

  /* ---------- 变更监听（防抖） ---------- */
  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () { try { refresh(); } catch (e) { } }, 600);
  }
  try {
    var ob = new MutationObserver(schedule);
    ob.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  } catch (e) { }
  R.interval(function () { refresh(); }, 5000);

  /* ---------- 供用户脚本调用 ---------- */
  R.usage = function () { return measure(); };

  R.log('用量统计就绪（≈' + st0.tokens + ' tokens / ' + st0.msgs + ' 条）');
});
