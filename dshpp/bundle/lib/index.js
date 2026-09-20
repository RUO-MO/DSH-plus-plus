/**
 * dsh-plugin-dshpp — host half (P1: DSH++ read-only panel runtime for DSH Desktop).
 *
 * Installed through the dshskin pattern: a managed block in the desktop
 * profile's cordis.patch.yml points at this module by file:// URL. No pnpm,
 * no profile dependency, no lockfile (verified 2026-09-17 / P0).
 *
 * P1 scope — ALL read-only, no writes:
 *
 *   GET /dshpp/health           liveness + node/platform facts
 *   GET /dshpp/capabilities     which host services are present (incl. probes)
 *   GET /dshpp/tree             snapshot of the live Loader entry tree
 *   GET /dshpp/status           skinRoot/dshHome + counts + in-host diagnostics
 *   GET /dshpp/themes           active theme + theme list (params, no css body)
 *   GET /dshpp/themes/:id       one theme's params (css preview only, 400 chars)
 *   GET /dshpp/sessions         session list (project-key -> sessions, filename-level;
 *                               zstd not available in Node => graceful degrade)
 *   GET /dshpp/sessions/search?q=  id/file-level filter
 *   GET /dshpp/session/:id      one session's metadata (no event decode in P1)
 *   GET /dshpp/providers        credential refs (masked) + llm providers leafs
 *   GET /dshpp/enhance          enhance.json state
 *   GET /dshpp/enhance/files    user scripts + builtin module flags
 *   GET /dshpp/market           builtin script catalog (mirror of DSH++ market) + installed flags
 *   GET /dshpp/plugins          plugins/registry.json (read-only mirror)
 *   GET /dshpp/plugins/tree     plugins dir walk (name -> versions)
 *   GET /dshpp/logs             logs.json (last 200, typed)
 *   GET /dshpp/settings         config.json summary + dshpp namespace (file view)
 *   GET /dshpp | /dshpp/        P1 panel page (bundled web/index.html)
 *
 * Data access: official services first where present (sessionQuery / credentials
 * are probed and reported in capabilities; their surfaces are unknown until P1
 * probes them), then file fallback with format guards — never throw 500, always
 * answer { ok, degraded?, error? }.
 *
 * `webServer` is a HARD dependency (`export const inject`), same pattern as
 * dsh-plugin-wallpaper-engine. P0 lesson: a soft `ctx.get` guard races boot
 * order and silently drops every route.
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BUNDLE_DIR = path.dirname(HERE);
const WEB_INDEX = path.join(BUNDLE_DIR, 'web', 'index.html');

const FIBER_STATE = { 0: 'pending', 1: 'loading', 2: 'active', 3: 'failed', 4: 'disposed', 5: 'unloading' };

const KNOWN_SERVICES = [
  'webServer', 'loader', 'hmr', 'timer', 'logger', 'settings', 'reflect', 'registry',
  'desktopRuntime', 'desktopPnpmBootstrap', 'desktopProfiles', 'desktopPlugins',
  'desktopActions', 'dshmarket', 'connection', 'modules', 'systemPrompt', 'cmdline',
  'sessionQuery', 'credentials', 'desktopPnpm',
];

const BLOCK_START = '# >>> dshpp:plugins (managed, do not edit inside) >>>';
const BLOCK_END = '# <<< dshpp:plugins <<<';

const SESSION_FILE_RE = /^session\.(v\d+)\.jsonl(\.zstd)?$/;

// 内置市场脚本目录（镜像 D:\DSH\DSH++\market 的 frontmatter，P1 只读；P2 起走 enhancer 模块包）。
const MARKET_CATALOG = [
  { id: 'copy-code-button', title: '代码块一键复制', description: '给会话里每个代码块右上角加「复制」按钮，点击复制代码内容；快捷键 Ctrl+Shift+C 复制最近一个代码块。', version: '1.0.0', permissions: ['dom', 'clipboard'], icon: '⧉' },
  { id: 'focus-mode', title: '专注模式', description: 'Ctrl+Alt+F 一键淡出左右侧栏（背景透出），再按一次或 Esc 恢复；状态记忆，刷新页面保持。', version: '1.0.0', permissions: ['dom'], icon: '◎' },
  { id: 'message-index', title: '消息序号标注', description: '给会话里每条消息左上角加序号徽标（#1、#2…），引用讨论时说「第 N 条」即可；悬停徽标显示角色。', version: '1.0.0', permissions: ['dom'], icon: '#' },
  { id: 'night-schedule', title: '夜间自动暗色', description: '每天 22:00–07:00 自动挂暗色标记（data-ds-dark-theme），白天自动还原；可被皮肤 force_dark 覆盖，互不打架。', version: '1.0.0', permissions: ['dom'], icon: '🌙' },
  { id: 'prompt-library', title: '提示词库', description: 'Ctrl+Alt+L 打开内置提示词库（代码评审/重构/排查/测试等 8 条场景），点选即插入输入框；支持自定义库（存储 key: prompt-library）。', version: '1.0.0', permissions: ['dom'], icon: '📚' },
  { id: 'selection-counter', title: '划词统计', description: '选中会话文字后浮动显示字符数与 token 估算（中英分开计），写长 prompt 前先量一量。', version: '1.0.0', permissions: ['dom'], icon: '𝍢' },
  { id: 'wide-screen', title: '宽屏会话', description: 'Ctrl+Alt+W 在「默认 / 宽 / 全宽」三档间循环切换会话区宽度，长代码不再挤成窄条。', version: '1.0.0', permissions: ['dom'], icon: '⇔' },
];

// ---------------- tiny utils ----------------

function safe(fn, fallback) {
  try { return fn(); } catch { return fallback === undefined ? null : fallback; }
}

function tryReadJson(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}

function maskSecret(v) {
  const s = String(v);
  if (s.length <= 8) return '****';
  return `${s.slice(0, 4)}****${s.slice(-4)}`;
}

function sizeOf(source, v) {
  // 返回 { value, source } 便于前端显示数据来源
  return { value: v, source };
}

// ---------------- path resolution (same chain as installer-core.mjs) ----------------

function resolveSkinRoot() {
  const env = process.env.DSH_SKIN_ROOT;
  if (env && fs.existsSync(env)) return { dir: env, source: 'env DSH_SKIN_ROOT' };
  const pointer = path.join(os.homedir(), '.dsh-skins', 'config.json');
  const cfg = tryReadJson(pointer);
  if (cfg && typeof cfg.skin_root === 'string' && cfg.skin_root && fs.existsSync(cfg.skin_root)) {
    return { dir: cfg.skin_root, source: '~/.dsh-skins/config.json skin_root' };
  }
  return { dir: path.join(os.homedir(), '.dsh-skins'), source: '~/.dsh-skins (default)' };
}

function resolveDshHome(skinRoot) {
  const cfg = tryReadJson(path.join(skinRoot, 'config.json'));
  if (cfg && typeof cfg.dsh_home === 'string' && cfg.dsh_home && fs.existsSync(cfg.dsh_home)) {
    return { dir: cfg.dsh_home, source: 'config dsh_home' };
  }
  const fallback = path.join(os.homedir(), '.dsh');
  if (fs.existsSync(fallback)) return { dir: fallback, source: '~/.dsh (default)' };
  return { dir: null, source: 'unresolved' };
}

// ---------------- data readers (all read-only) ----------------

function countDir(dir) {
  return safe(() => fs.readdirSync(dir).length, 0);
}

function themesView(skinRoot) {
  const cfg = tryReadJson(path.join(skinRoot, 'config.json'));
  const themes = {};
  if (cfg && cfg.themes && typeof cfg.themes === 'object') {
    for (const [id, t] of Object.entries(cfg.themes)) {
      if (!t || typeof t !== 'object') continue;
      themes[id] = {
        id,
        name: t.name ?? id,
        version: t.version ?? '?',
        appearance: t.appearance ?? 'light',
        builtin: t.builtin === true,
        hasImage: !!t.image,
        hasCss: typeof t.skin_css === 'string' && t.skin_css.length > 0,
        params: t.params ?? {},
      };
    }
  }
  return {
    active: cfg && cfg.active ? cfg.active : null,
    themes,
    count: Object.keys(themes).length,
    configFile: path.join(skinRoot, 'config.json'),
    configVersion: cfg ? cfg.config_version : null,
  };
}

function sessionsView(dshHomeDir) {
  const root = path.join(dshHomeDir, 'sessions');
  const projects = [];
  if (!fs.existsSync(root)) return { root, projects, total: 0, decode: 'no-sessions-dir' };
  let total = 0;
  for (const proj of safe(() => fs.readdirSync(root), []).sort()) {
    const projDir = path.join(root, proj);
    if (proj.startsWith('.')) continue;
    const pst = safe(() => fs.statSync(projDir), null);
    if (!pst || !pst.isDirectory()) continue;
    const items = [];
    for (const s of safe(() => fs.readdirSync(projDir), []).sort()) {
      if (s.startsWith('.')) continue;
      const sDir = path.join(projDir, s);
      const st = safe(() => fs.statSync(sDir), null);
      if (!st || !st.isDirectory()) continue;
      let file = null;
      for (const fn of safe(() => fs.readdirSync(sDir), [])) {
        if (SESSION_FILE_RE.test(fn)) { file = fn; break; }
      }
      if (!file) continue;
      const fst = safe(() => fs.statSync(path.join(sDir, file)), null);
      const m = file.match(SESSION_FILE_RE);
      items.push({
        id: s.startsWith('session-') ? s.slice('session-'.length) : s,
        dirName: s,
        file,
        format: m[1],
        compressed: !!m[2],
        sizeBytes: fst ? fst.size : -1,
        mtime: fst ? new Date(fst.mtime).toISOString() : null,
        title: null, // zstd 未解码 => 文件名级
      });
    }
    if (items.length) {
      projects.push({ key: proj, sessions: items });
      total += items.length;
    }
  }
  return { root, projects, total, decode: 'zstd-unavailable' };
}

function findSession(dshHomeDir, id) {
  const view = sessionsView(dshHomeDir);
  for (const proj of view.projects) {
    const hit = proj.sessions.find((s) => s.id === id || s.dirName === id);
    if (hit) {
      return { projectKey: proj.key, meta: hit, path: path.join(dshHomeDir, 'sessions', proj.key, hit.dirName, hit.file) };
    }
  }
  return null;
}

function zstdProbe() {
  // 尝试在 bundle 侧解析 zstd 实现；没有就去重载 node:zlib（无 zstd）→ 判断不可用。
  const req = safe(() => createRequire(import.meta.url), null);
  const candidates = ['zstd', 'zstd-codec', '@bokuweb/zstd-wasm', 'fzstd'];
  for (const c of candidates) {
    const found = req ? safe(() => req.resolve(c), '') : '';
    if (found) return { available: true, module: c };
  }
  return { available: false, reason: 'no zstd implementation in bundle (P1 filename-level listing)' };
}

function providersView(skinRoot, dshHomeDir) {
  const credentialsFile = path.join(dshHomeDir, '.credentials.yaml');
  const settingsFile = path.join(dshHomeDir, 'settings.yaml');
  const cred = tryReadCreds(credentialsFile);
  const prov = tryReadProviders(settingsFile);
  return { credentials: cred, providers: prov, credentialsFile, settingsFile };
}

function tryReadCreds(file) {
  if (!fs.existsSync(file)) return { refs: [], records: [], degraded: 'absent' };
  const text = safe(() => fs.readFileSync(file, 'utf8'), '');
  const refs = [];
  const records = [];
  let section = null;
  for (const raw of String(text).split(/\r?\n/)) {
    const line = raw.replace(/\r$/, '');
    const ind = line.length - line.trimStart().length;
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    if (ind === 0 && /^[A-Za-z_][\w-]*:$/.test(t)) { section = t.slice(0, -1); continue; }
    if (ind === 0) continue;
    if (section === 'refs' && ind === 2) {
      const i = t.indexOf(':');
      if (i > 0) {
        const k = t.slice(0, i).trim();
        const v = t.slice(i + 1).trim().replace(/^["']|["']$/g, '');
        if (k && v) refs.push({ key: k, value: maskSecret(v) });
      }
    } else if (section === 'records' && ind === 2) {
      const i = t.indexOf(':');
      if (i > 0) records.push({ key: t.slice(0, i).trim() });
    }
  }
  return { refs, records, degraded: false };
}

function tryReadProviders(file) {
  if (!fs.existsSync(file)) return { providers: [], degraded: 'absent' };
  const text = safe(() => fs.readFileSync(file, 'utf8'), '');
  const providers = [];
  let inSection = false;
  let cur = null;
  for (const raw of String(text).split(/\r?\n/)) {
    const line = raw.replace(/\r$/, '');
    const ind = line.length - line.trimStart().length;
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    if (ind === 0 && t === 'llm-pi-ai:') { inSection = true; continue; }
    if (ind === 0 && inSection) { inSection = false; continue; }
    if (!inSection) continue;
    if (ind === 2 && t === 'providers:') continue;
    if (ind === 2) { inSection = false; continue; }
    if (ind === 4 && t.endsWith(':')) {
      cur = { id: t.slice(0, -1).trim(), models: [] };
      providers.push(cur);
      continue;
    }
    if (ind === 6 && cur && /^[A-Za-z_][\w]*:/.test(t)) {
      const i = t.indexOf(':');
      const k = t.slice(0, i).trim();
      const v = t.slice(i + 1).trim().replace(/^\s*#.*$/, '').replace(/^["']|["']$/g, '').trim();
      if (k === 'models' && v === '') { cur.inModels = true; continue; }
      cur[k] = v;
      cur.inModels = false;
      continue;
    }
    if (ind === 8 && cur && cur.inModels) {
      const m = t.match(/^- id:\s*(\S+)/);
      if (m) cur.models.push(m[1]);
      continue;
    }
  }
  // 只输出需要的叶子字段（不整文件进内存；apiKeyEnv 名字可给，值不落 API）
  return {
    providers: providers.map((p) => ({
      id: p.id,
      displayName: p.displayName ?? null,
      api: p.api ?? null,
      baseURL: p.baseURL ?? null,
      apiKeyEnv: p.apiKeyEnv ?? null,
      modelCount: (p.models || []).length,
      modelIds: (p.models || []),
    })),
    degraded: false,
  };
}

function enhanceView(skinRoot) {
  const cfg = tryReadJson(path.join(skinRoot, 'enhance.json'));
  if (!cfg) return { ok: false, degraded: 'enhance.json absent' };
  const modules = (cfg.modules && typeof cfg.modules === 'object') ? cfg.modules : {};
  const scripts = (cfg.scripts && typeof cfg.scripts === 'object') ? cfg.scripts : {};
  return {
    ok: true,
    enabled: cfg.enabled !== false,
    modules,
    moduleCount: Object.keys(modules).length,
    moduleEnabledCount: Object.values(modules).filter(Boolean).length,
    scripts,
    scriptCount: Object.keys(scripts).length,
    hotkeys: cfg.hotkeys ?? {},
  };
}

function enhanceFilesView(skinRoot) {
  const dir = path.join(skinRoot, 'enhance');
  const entries = [];
  if (fs.existsSync(dir)) {
    for (const fn of safe(() => fs.readdirSync(dir), []).sort()) {
      if (fn.startsWith('.')) continue;
      const st = safe(() => fs.statSync(path.join(dir, fn)), null);
      if (!st || !st.isFile()) continue;
      entries.push({ name: fn, sizeBytes: st.size, mtime: new Date(st.mtime).toISOString() });
    }
  }
  return { dir, userScripts: entries };
}

function marketView(skinRoot) {
  const enhance = tryReadJson(path.join(skinRoot, 'enhance.json'));
  const scripts = (enhance && enhance.scripts && typeof enhance.scripts === 'object') ? enhance.scripts : {};
  return {
    source: 'builtin-catalog', // 镜像 DSH++/market frontmatter；P2 起由 enhancer 模块包接管
    degraded: true,
    scripts: MARKET_CATALOG.map((s) => ({
      ...s,
      installed: Object.prototype.hasOwnProperty.call(scripts, s.id) ? scripts[s.id] : null,
    })),
    installedCount: MARKET_CATALOG.filter((s) => scripts[s.id] === true).length,
  };
}

function pluginsView(skinRoot) {
  const reg = tryReadJson(path.join(skinRoot, 'plugins', 'registry.json'));
  const plugins = [];
  if (reg && reg.plugins && typeof reg.plugins === 'object') {
    for (const [name, p] of Object.entries(reg.plugins)) {
      if (!p || typeof p !== 'object') continue;
      plugins.push({
        name,
        version: p.version ?? '?',
        track: p.track ?? '?',
        enabled: (p.enabled && typeof p.enabled === 'object') ? p.enabled : null,
        hasBundle: !!p.hasBundle,
        hasClient: !!p.hasClient,
        installedAt: p.installedAt ?? null,
        engines: p.engines ?? null,
      });
    }
  }
  plugins.sort((a, b) => a.name.localeCompare(b.name));
  return {
    registryFile: path.join(skinRoot, 'plugins', 'registry.json'),
    globalEnabled: reg ? reg.enabled !== false : null,
    plugins,
    total: plugins.length,
  };
}

function pluginsTreeView(skinRoot) {
  const root = path.join(skinRoot, 'plugins');
  const plugins = [];
  if (fs.existsSync(root)) {
    for (const name of safe(() => fs.readdirSync(root), []).sort()) {
      if (name.startsWith('.')) continue;
      const pdir = path.join(root, name);
      const st = safe(() => fs.statSync(pdir), null);
      if (!st || !st.isDirectory()) continue;
      const versions = [];
      for (const v of safe(() => fs.readdirSync(pdir), []).sort()) {
        if (v.startsWith('.')) continue;
        const vdir = path.join(pdir, v);
        const vst = safe(() => fs.statSync(vdir), null);
        if (!vst || !vst.isDirectory()) continue;
        versions.push({
          version: v,
          hasIndex: fs.existsSync(path.join(vdir, 'lib', 'index.js')),
          hasClient: fs.existsSync(path.join(vdir, 'lib', 'client.js')),
          mtime: new Date(vst.mtime).toISOString(),
        });
      }
      plugins.push({ name, versions });
    }
  }
  return { root, plugins };
}

function logsView(skinRoot, limit = 200) {
  const arr = tryReadJson(path.join(skinRoot, 'logs.json'));
  if (!Array.isArray(arr)) return { entries: [], count: 0, degraded: 'logs.json absent or not array' };
  return { entries: arr.slice(-limit), count: arr.length, file: path.join(skinRoot, 'logs.json') };
}

function settingsView(skinRoot, dshHomeDir, profileDir) {
  const config = tryReadJson(path.join(skinRoot, 'config.json'));
  const summary = config ? {
    active: config.active ?? null,
    channel: config.channel ?? null,
    launcherMode: config.launcher_mode ?? null,
    configVersion: config.config_version ?? null,
    dshHome: config.dsh_home ?? null,
    desktopExe: config.desktop_exe ?? null,
    themeIds: config.themes ? Object.keys(config.themes) : [],
  } : null;

  // settings 命名空间 dshpp（profile settings.yaml 原始文本块，只读视图）
  let namespace = null;
  const settingsFile = path.join(profileDir, 'settings.yaml');
  if (fs.existsSync(settingsFile)) {
    const text = safe(() => fs.readFileSync(settingsFile, 'utf8'), '');
    // 抽取 "dshpp:" 顶层命名空间的原始缩进块（不解析语义，仅展示）
    const lines = String(text).split(/\r?\n/);
    const out = [];
    let open = false;
    for (const raw of lines) {
      const line = raw.replace(/\r$/, '');
      if (!open && /^dshpp:/.test(line)) { open = true; out.push(line); continue; }
      if (open) {
        if (/^\S/.test(line) && !line.startsWith('#')) { break; }
        out.push(line);
      }
    }
    if (out.length) namespace = { raw: out.join('\n'), settingsFile };
  }

  return { config: summary, namespace, configFile: path.join(skinRoot, 'config.json') };
}

function diagnostics(skinRoot, dshHomeDir, profileDir, sourceCtx) {
  const patchFile = path.join(profileDir, 'cordis.patch.yml');
  const patchText = fs.existsSync(patchFile) ? safe(() => fs.readFileSync(patchFile, 'utf8'), '') : '';
  const hasBlock = patchText.includes(BLOCK_START) && patchText.includes(BLOCK_END);
  const checker = (name, ok, detail) => ({ name, ok, detail });
  return [
    checker('skinRoot 可达', fs.existsSync(skinRoot), skinRoot),
    checker('skinRoot 可写', safe(() => { fs.accessSync(skinRoot, fs.constants.W_OK); return true; }, false), skinRoot),
    checker('dsh home', !!dshHomeDir && fs.existsSync(dshHomeDir), dshHomeDir ?? '-'),
    checker('profile cordis.patch.yml', fs.existsSync(patchFile), patchFile),
    checker('dshpp managed block', hasBlock, hasBlock ? 'present' : 'absent（未安装或已卸载）'),
    checker('plugins/registry.json', fs.existsSync(path.join(skinRoot, 'plugins', 'registry.json')), path.join(skinRoot, 'plugins', 'registry.json')),
    checker('sessions 目录', !!dshHomeDir && fs.existsSync(path.join(dshHomeDir, 'sessions')), dshHomeDir ? path.join(dshHomeDir, 'sessions') : '-'),
    checker('面板静态页', fs.existsSync(WEB_INDEX), WEB_INDEX),
    checker('sessionQuery service', safe(() => typeof sourceCtx.get('sessionQuery'), '') !== 'undefined', String(safe(() => typeof sourceCtx.get('sessionQuery'), 'undefined'))),
    checker('credentials service', safe(() => typeof sourceCtx.get('credentials'), '') !== 'undefined', String(safe(() => typeof sourceCtx.get('credentials'), 'undefined'))),
  ];
}

function serviceMap(ctx) {
  const present = [];
  for (const name of KNOWN_SERVICES) {
    if (safe(() => ctx.get(name), undefined) !== undefined) present.push(name);
  }
  return present;
}

function treeSnapshot(ctx) {
  const loader = safe(() => ctx.get('loader'));
  if (!loader) return null;
  const iterator = safe(() => loader.entries());
  if (typeof iterator === 'object' && iterator !== null && typeof iterator[Symbol.iterator] !== 'function') return null;
  const rows = [];
  for (const entry of iterator) {
    const opts = (entry && entry.options) || {};
    rows.push({
      id: opts.id === undefined || opts.id === null ? null : String(opts.id),
      name: typeof opts.name === 'string' ? opts.name : safe(() => String(opts.name), '?'),
      group: opts.group === true,
      disabled: safe(() => Boolean(entry.disabled), null),
      fiberState: entry.fiber ? (FIBER_STATE[entry.fiber.state] !== undefined ? FIBER_STATE[entry.fiber.state] : String(entry.fiber.state)) : null,
    });
  }
  return rows;
}

function probeOptionalService(ctx, name) {
  const svc = safe(() => ctx.get(name), null);
  if (svc === null || svc === undefined) return { present: false };
  const surface = {};
  for (const m of ['list', 'search', 'query', 'find', 'get', 'open', 'stats', 'count']) {
    surface[m] = typeof svc[m] === 'function';
  }
  return { present: true, surface };
}

// ---------------- panel page ----------------

function panelHtml() {
  if (fs.existsSync(WEB_INDEX)) {
    return safe(() => fs.readFileSync(WEB_INDEX, 'utf8'), '');
  }
  return '<!doctype html><html><head><meta charset="utf-8"><title>DSH++ Panel</title></head><body><h1>DSH++ Panel</h1><p>web/index.html 缺失（bundle 不完整）</p></body></html>';
}

// ---------------- routes ----------------

export const inject = ['webServer'];

export function apply(ctx) {
  const webServer = ctx.webServer;
  if (!webServer || typeof webServer.register !== 'function') {
    // Unreachable: the Loader only runs apply() after `inject` is satisfied.
    return () => {};
  }

  const skin = resolveSkinRoot();
  const home = resolveDshHome(skin.dir);
  const profileDir = home.dir ? path.join(home.dir, 'profiles', 'desktop') : null;

  function json(res, code, payload) {
    res.statusCode = code;
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    res.end(JSON.stringify(payload));
  }

  function html(res, code, body) {
    res.statusCode = code;
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    res.end(body);
  }

  function urlParts(url) {
    const q = url.indexOf('?');
    return { pathname: q === -1 ? url : url.slice(0, q), query: q === -1 ? '' : url.slice(q + 1) };
  }

  function queryParam(query, key) {
    const m = query.match(new RegExp(`(?:^|&)${key}=([^&]*)`));
    return m ? decodeURIComponent(m[1]) : null;
  }

  const disposers = [];
  const reg = (kind, pathname, handler) => {
    disposers.push(webServer.register({ kind, path: pathname, handler }));
  };

  reg('exact', '/dshpp/health', async (req, res) => json(res, 200, {
    ok: true,
    pid: process.pid,
    uptimeSec: Math.round(process.uptime()),
    node: process.version,
    platform: process.platform,
    arch: process.arch,
    electron: safe(() => process.versions && process.versions.electron),
    time: new Date().toISOString(),
  }));

  reg('exact', '/dshpp/capabilities', async (req, res) => json(res, 200, {
    ok: true,
    version: '0.2.0',
    servicesPresent: serviceMap(ctx),
    sessionQuery: probeOptionalService(ctx, 'sessionQuery'),
    credentials: probeOptionalService(ctx, 'credentials'),
    desktopProfiles: probeOptionalService(ctx, 'desktopProfiles'),
    desktopPnpm: probeOptionalService(ctx, 'desktopPnpm'),
    entryCount: safe(() => { const loader = ctx.get('loader'); let n = 0; for (const _e of loader.entries()) n += 1; return n; }, null),
  }));

  reg('exact', '/dshpp/tree', async (req, res) => json(res, 200, {
    ok: true,
    entries: treeSnapshot(ctx) || [],
  }));

  reg('exact', '/dshpp/status', async (req, res) => {
    const themes = themesView(skin.dir);
    const sessions = home.dir ? sessionsView(home.dir) : { root: null, projects: [], total: 0, decode: 'no-home' };
    const enhance = enhanceView(skin.dir);
    const plugins = pluginsView(skin.dir);
    const logs = logsView(skin.dir);
    json(res, 200, {
      ok: true,
      skinRoot: skin.dir,
      skinRootSource: skin.source,
      dshHome: home.dir,
      dshHomeSource: home.source,
      profileDir,
      counts: {
        themes: themes.count,
        sessions: sessions.total,
        sessionProjects: sessions.projects.length,
        plugins: plugins.total,
        enhanceModules: typeof enhance.moduleCount === 'number' ? enhance.moduleCount : 0,
        enhanceScripts: typeof enhance.scriptCount === 'number' ? enhance.scriptCount : 0,
        logs: logs.count,
        marketScripts: MARKET_CATALOG.length,
      },
      diagnostics: diagnostics(skin.dir, home.dir, profileDir, ctx),
      zstd: zstdProbe(),
    });
  });

  reg('exact', '/dshpp/themes', async (req, res) => {
    const view = themesView(skin.dir);
    json(res, 200, { ok: true, ...view });
  });

  reg('prefix', '/dshpp/themes/', async (req, res) => {
    const { pathname } = urlParts(req.url);
    const id = decodeURIComponent(pathname.slice('/dshpp/themes/'.length).replace(/\/+$/, ''));
    const view = themesView(skin.dir);
    const t = view.themes[id];
    if (!t) return json(res, 404, { ok: false, error: `theme not found: ${id}` });
    const full = safe(() => tryReadJson(path.join(skin.dir, 'config.json'))?.themes?.[id], null);
    const css = full && full.skin_css ? full.skin_css : '';
    json(res, 200, {
      ok: true,
      theme: {
        ...t,
        active: view.active === id,
        cssLength: css.length,
        cssPreview: css ? `${css.slice(0, 400)}…` : null,
      },
    });
  });

  reg('exact', '/dshpp/sessions', async (req, res) => {
    if (!home.dir) return json(res, 200, { ok: false, degraded: 'dsh home 未解析', projects: [], total: 0, decode: 'no-home' });
    const view = sessionsView(home.dir);
    json(res, 200, { ok: true, ...view, zstd: zstdProbe() });
  });

  reg('exact', '/dshpp/sessions/search', async (req, res) => {
    if (!home.dir) return json(res, 200, { ok: false, degraded: 'dsh home 未解析', projects: [], total: 0, decode: 'no-home' });
    const { query } = urlParts(req.url);
    const q = (queryParam(query, 'q') || '').toLowerCase();
    const view = sessionsView(home.dir);
    let projects = view.projects;
    if (q) {
      projects = projects
        .map((p) => ({ key: p.key, sessions: p.sessions.filter((s) => s.id.toLowerCase().includes(q) || s.file.toLowerCase().includes(q)) }))
        .filter((p) => p.sessions.length > 0);
    }
    json(res, 200, { ok: true, query: q, projects, total: projects.reduce((n, p) => n + p.sessions.length, 0), decode: view.decode });
  });

  reg('prefix', '/dshpp/session/', async (req, res) => {
    const { pathname } = urlParts(req.url);
    const id = decodeURIComponent(pathname.slice('/dshpp/session/'.length).replace(/\/+$/, ''));
    if (!home.dir) return json(res, 404, { ok: false, error: 'dsh home 未解析' });
    const hit = findSession(home.dir, id);
    if (!hit) return json(res, 404, { ok: false, error: `session not found: ${id}` });
    json(res, 200, {
      ok: true,
      session: {
        ...hit.meta,
        projectKey: hit.projectKey,
        path: hit.path,
        decoded: false,
        note: 'P1：Node 运行时无 zstd，仅文件名级元数据；正文解码随 sessionQuery 服务探测（见 /capabilities）后续版本提供。',
      },
    });
  });

  reg('exact', '/dshpp/providers', async (req, res) => {
    if (!home.dir) return json(res, 200, { ok: false, degraded: 'dsh home 未解析' });
    const view = providersView(skin.dir, home.dir);
    json(res, 200, { ok: true, ...view, apiKeysMasked: true, officialService: probeOptionalService(ctx, 'credentials') });
  });

  reg('exact', '/dshpp/enhance', async (req, res) => {
    const view = enhanceView(skin.dir);
    if (view.ok) json(res, 200, view);
    else json(res, 200, { ok: false, degraded: view.degraded });
  });

  reg('exact', '/dshpp/enhance/files', async (req, res) => {
    const files = enhanceFilesView(skin.dir);
    const enhance = enhanceView(skin.dir);
    json(res, 200, {
      ok: true,
      dir: files.dir,
      userScripts: files.userScripts,
      builtinModules: enhance.ok
        ? Object.entries(enhance.modules).map(([id, enabled]) => ({
            id, enabled: !!enabled, note: '运行时随 enhancer 插件（P3），本分区仅状态视图',
          }))
        : [],
    });
  });

  reg('exact', '/dshpp/market', async (req, res) => {
    const view = marketView(skin.dir);
    json(res, 200, { ok: true, ...view });
  });

  reg('exact', '/dshpp/plugins', async (req, res) => {
    const view = pluginsView(skin.dir);
    json(res, 200, { ok: true, ...view });
  });

  reg('exact', '/dshpp/plugins/tree', async (req, res) => {
    const view = pluginsTreeView(skin.dir);
    json(res, 200, { ok: true, ...view });
  });

  reg('exact', '/dshpp/logs', async (req, res) => {
    const view = logsView(skin.dir);
    json(res, 200, { ok: true, ...view });
  });

  reg('exact', '/dshpp/settings', async (req, res) => {
    const view = settingsView(skin.dir, home.dir, profileDir);
    json(res, 200, { ok: true, ...view });
  });

  reg('exact', '/dshpp', async (req, res) => html(res, 200, panelHtml()));
  reg('exact', '/dshpp/', async (req, res) => html(res, 200, panelHtml()));

  return () => {
    for (const d of disposers) {
      try { d(); } catch { /* already unwound */ }
    }
  };
}