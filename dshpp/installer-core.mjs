/**
 * dshpp installer core — shared by install.mjs / uninstall.mjs.
 *
 * Install model (the "dshskin pattern", verified in production on this
 * machine 2026-09-17):
 *
 *   1. The bundle is COPIED into the DSH++ plugin tree
 *      (<skinRoot>\plugins\dsh-plugin-dshpp\<version>\); skinRoot is resolved
 *      at runtime — env DSH_SKIN_ROOT → C 盘指针文件 ~/.dsh-skins/config.json
 *      (skin_root) → ~/.dsh-skins. Never hardcoded.
 *   2. A managed block is written into the desktop profile's cordis.patch.yml
 *      (a file the boot-time pnpm reconcilers never touch):
 *
 *        # >>> dshpp:plugins (managed, do not edit inside) >>>
 *        - insert:
 *            - id: dsh-plugin-dshpp
 *              name: 'file:///<skinRoot>/plugins/dsh-plugin-dshpp/<v>/lib/index.js'
 *        # <<< dshpp:plugins <<<
 *
 *   3. The DSH++ registry (plugins\registry.json) gets an A-track entry.
 *
 * No pnpm, no profile package.json, no lockfile, no node_modules. The file://
 * row loads the host half in the Loader; the bundle package.json's
 * `dsh.client` declaration puts the client half into the browser boot graph
 * (same mechanism the four dshskin rows use). Uninstall reverses exactly
 * those three steps. Every run snapshots its pre-state first.
 *
 * Why not a profile dependency: the 2026-09-17 P0 direct install was pruned
 * by the desktop's own boot-time pnpm reconciliation (manifest + lockfile +
 * node_modules rewritten together, no market log entry), and the market UI
 * mis-handles packages it did not install itself ("no loader entry matched"
 * toggles). See DSHPP_PLUGINIZATION.md §9.5.
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const DSHPP_HOME = HERE;
export const BUNDLE_DIR = path.join(HERE, 'bundle');

export const DEFAULTS = {
  /** 运行时解析（resolveProfileDir / resolveSkinRoot），仅当解析失败时的兜底。 */
  profile: null,
  skinRoot: null,
  pluginName: 'dsh-plugin-dshpp',
  loaderRowId: 'dsh-plugin-dshpp',
};

/**
 * 解析 SKIN_ROOT（与 dsh_env.py 同链）：
 * env DSH_SKIN_ROOT → ~/.dsh-skins/config.json 的 skin_root 指针 → ~/.dsh-skins。
 * 返回 { dir, source, conflict? }；不出异常，最后一步总是可达。
 * 若 env 与 C 盘指针不一致会附带 conflict 警告（本机 User 作用域曾残留 D:\DATA 旧根）。
 */
export function resolveSkinRoot() {
  const env = process.env.DSH_SKIN_ROOT;
  const pointerDir = path.join(os.homedir(), '.dsh-skins', 'config.json');
  let ptrRoot = null;
  try {
    const cfg = JSON.parse(fs.readFileSync(pointerDir, 'utf8'));
    if (typeof cfg.skin_root === 'string' && cfg.skin_root && fs.existsSync(cfg.skin_root)) ptrRoot = cfg.skin_root;
  } catch { /* 指针文件缺失/损坏则继续 */ }
  if (env && fs.existsSync(env)) {
    const conflict =
      ptrRoot && path.resolve(ptrRoot) !== path.resolve(env)
        ? `env DSH_SKIN_ROOT（${env}）与 C 盘指针皮肤根（${ptrRoot}）不一致 —— QoS：安装位自检将据此拦截错误目标`
        : null;
    return { dir: env, source: 'env DSH_SKIN_ROOT', conflict };
  }
  if (ptrRoot) return { dir: ptrRoot, source: '~/.dsh-skins/config.json skin_root' };
  return { dir: path.join(os.homedir(), '.dsh-skins'), source: '~/.dsh-skins (default)' };
}

/**
 * 解析桌面 profile 目录：<skinRoot>/config.json 的 dsh_home → <dshHome>/profiles/desktop。
 * 禁读 env DSH_HOME（P1 定案：机器上残留 D:\DATA\DSHdata\.dsh，桌面端实际不用它，
 * config.dsh_home 优先）。解析不到时返回 { dir: null }。
 */
export function resolveProfileDir(skinRoot) {
  let dshHome = null;
  try {
    const cfgPath = path.join(skinRoot, 'config.json');
    if (fs.existsSync(cfgPath)) {
      const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
      if (typeof cfg.dsh_home === 'string' && cfg.dsh_home && fs.existsSync(cfg.dsh_home)) {
        dshHome = cfg.dsh_home;
      }
    }
  } catch { /* 忽略 */ }
  if (!dshHome) {
    const fallback = path.join(os.homedir(), '.dsh');
    if (fs.existsSync(fallback)) dshHome = fallback;
  }
  return { dshHome, dir: dshHome ? path.join(dshHome, 'profiles', 'desktop') : null };
}

/**
 * 读取 live profile 现有 dshskin managed block 的 file:// 行 → plugins 根列表。
 * 用于安装位自检：解析出的 plugins 根必须与存量 dshskin 行一致，否则中止
 * （防止装进历史噪音根 D:\DATA\DSHdata）。
 */
export function livePluginsRoots(profileDir) {
  const file = path.join(profileDir, 'cordis.patch.yml');
  const raw = [];
  if (!fs.existsSync(file)) return { roots: [], raw };
  const text = fs.readFileSync(file, 'utf8');
  for (const m of text.matchAll(/name:\s*['"]?(file:\/\/[^'\s"]+)['"]?/g)) {
    raw.push(m[1]);
  }
  const roots = [];
  for (const href of raw) {
    try {
      const p = fileURLToPath(href);
      // 期望 .../plugins/<plugin>/<version>/lib/index.js → 剥掉 lib/index.js 再取一层
      const withoutEntry = p.replace(/[\\/]lib[\\/]index\.js$/i, '');
      const pluginsRoot = path.dirname(path.dirname(withoutEntry));
      roots.push(pluginsRoot);
    } catch { /* 非本机 file:// 或损坏，跳过 */ }
  }
  return { roots, raw };
}

/**
 * 安装位自检（C 强约束之一）：解析出的 plugins 根 ≠ live profile dshskin file:// 前缀则抛错中止。
 * live profile 无任何 dshskin 行时跳过（全新安装场景），仅返回 warn。
 */
export function assertInstallPosition(profileDir, skinRoot) {
  const pluginsRoot = path.join(skinRoot, 'plugins');
  const { roots, raw } = livePluginsRoots(profileDir);
  if (roots.length === 0) {
    return {
      ok: true,
      warn: 'live profile 无 dshskin 行，跳过安装位自检（全新安装场景）',
      target: pluginsRoot,
      roots: raw,
    };
  }
  const mismatch = roots.filter((r) => path.resolve(r) !== path.resolve(pluginsRoot));
  if (mismatch.length > 0) {
    throw new Error(
      `安装位自检失败：解析出的 plugins 根（${pluginsRoot}）与 live profile 现有 dshskin file:// 前缀不一致：` +
        `${mismatch.join('; ')}。已中止安装，请核对 skinRoot 来源（env DSH_SKIN_ROOT / ~/.dsh-skins/config.json skin_root）。`,
    );
  }
  return { ok: true, target: pluginsRoot, roots: raw };
}

export const BLOCK_START = '# >>> dshpp:plugins (managed, do not edit inside) >>>';
export const BLOCK_END = '# <<< dshpp:plugins <<<';

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export const BLOCK_RE = new RegExp(
  `[ \\t]*${escapeRe(BLOCK_START)}[\\s\\S]*?${escapeRe(BLOCK_END)}[ \\t]*\\r?\\n?`,
  'g',
);

function localTimestamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

function localDateTime() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** Snapshot the files an operation may touch into snapshots/<tag>-<ts>/. */
export function snapshotFiles(tag, profileDir, skinRoot) {
  const dir = path.join(HERE, 'snapshots', `${localTimestamp()}-${tag}`);
  fs.mkdirSync(dir, { recursive: true });
  const sources = [
    profileDir ? [path.join(profileDir, 'package.json'), 'profile-package.json'] : null,
    profileDir ? [path.join(profileDir, 'pnpm-lock.yaml'), 'profile-pnpm-lock.yaml'] : null,
    profileDir ? [path.join(profileDir, 'cordis.patch.yml'), 'profile-cordis.patch.yml'] : null,
    skinRoot ? [path.join(skinRoot, 'registry.json'), 'registry.json'] : null,
  ].filter(Boolean);
  const copied = [];
  for (const [src, name] of sources) {
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, path.join(dir, name));
      copied.push(name);
    }
  }
  return { dir, copied };
}

export function readBundleVersion() {
  const manifest = JSON.parse(fs.readFileSync(path.join(BUNDLE_DIR, 'package.json'), 'utf8'));
  if (manifest.name !== DEFAULTS.pluginName) {
    throw new Error(`bundle package.json name is ${manifest.name}, expected ${DEFAULTS.pluginName}`);
  }
  if (typeof manifest.version !== 'string' || manifest.version === '') {
    throw new Error('bundle package.json has no version');
  }
  return manifest;
}

export function verifyBundle() {
  const manifest = readBundleVersion();
  for (const rel of ['lib/index.js', 'lib/client.js']) {
    if (!fs.existsSync(path.join(BUNDLE_DIR, rel))) {
      throw new Error(`bundle is missing ${rel}`);
    }
  }
  return manifest;
}

function patchFile(profileDir) {
  return path.join(profileDir, 'cordis.patch.yml');
}

function readPatchText(profileDir) {
  const file = patchFile(profileDir);
  if (!fs.existsSync(file)) return '';
  return fs.readFileSync(file, 'utf8');
}

function stripManagedBlock(text) {
  return text.replace(BLOCK_RE, '');
}

function isEmptyPatch(text) {
  // A patch file is empty when it carries no YAML content at all
  // (comments and whitespace are fine; a bare `[]` is also "empty").
  const noComments = text
    .split(/\r?\n/)
    .map((l) => l.replace(/#.*$/, ''))
    .join('\n')
    .trim();
  return noComments === '' || noComments === '[]';
}

export function makeBlock(installDir, version) {
  const entry = path.join(installDir, version, 'lib', 'index.js');
  const url = pathToFileURL(entry).href;
  return [
    BLOCK_START,
    '- insert:',
    `    - id: ${DEFAULTS.loaderRowId}`,
    `      name: '${url}'`,
    BLOCK_END,
    '',
  ].join('\n');
}

/** Write the managed block into the profile patch file. Returns a report. */
export function installBlock(profileDir, installDir, version) {
  const file = patchFile(profileDir);
  const before = readPatchText(profileDir);
  const block = makeBlock(installDir, version);

  let after;
  let action;
  if (BLOCK_RE.test(before)) {
    after = before.replace(BLOCK_RE, block);
    action = 'replaced existing dshpp block';
  } else if (isEmptyPatch(before)) {
    after = block;
    action = before.trim() === '' ? 'created patch file' : 'replaced empty patch file';
  } else {
    const trimmed = before.replace(/\s*$/, '');
    after = `${trimmed}\n\n${block}`;
    action = 'appended dshpp block';
  }

  fs.writeFileSync(file, after, 'utf8');
  return { file, action, before, after };
}

/** Remove the managed block. Returns a report. */
export function uninstallBlock(profileDir) {
  const file = patchFile(profileDir);
  const before = readPatchText(profileDir);
  let action;
  if (!fs.existsSync(file)) {
    return { file, action: 'patch file absent — nothing to do', before, after: before };
  }
  if (!BLOCK_RE.test(before)) {
    return { file, action: 'no dshpp block present — nothing to do', before, after: before };
  }
  const stripped = stripManagedBlock(before).replace(/\n{3,}/g, '\n\n');
  let after;
  if (isEmptyPatch(stripped)) {
    after = '[]\n';
    action = 'removed dshpp block; file left as empty list';
  } else {
    after = stripped.replace(/\s*$/, '') + '\n';
    action = 'removed dshpp block';
  }
  fs.writeFileSync(file, after, 'utf8');
  return { file, action, before, after };
}

export function installDirFor(skinRoot) {
  return path.join(skinRoot, DEFAULTS.pluginName);
}

/** Copy the bundle into the skin tree under <version>/. Returns the dir. */
export function copyBundle(skinRoot, version) {
  const base = installDirFor(skinRoot);
  const target = path.join(base, version);
  if (fs.existsSync(target)) fs.rmSync(target, { recursive: true, force: true });
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.cpSync(BUNDLE_DIR, target, { recursive: true });
  return target;
}

export function removeBundle(skinRoot) {
  const base = installDirFor(skinRoot);
  if (!fs.existsSync(base)) return { removed: false };
  fs.rmSync(base, { recursive: true, force: true });
  return { removed: true, dir: base };
}

export function registryPath(skinRoot) {
  return path.join(skinRoot, 'registry.json');
}

export function registryEntry(installedTarget, version, manifest) {
  return {
    name: DEFAULTS.pluginName,
    version,
    track: 'A',
    origin: 'dshpp',
    description:
      'DSH++ self-installing runtime for DSH Desktop: host console + (P1) panel, plugin/skin management.',
    hasBundle: true,
    hasClient: true,
    engines: typeof manifest.engines === 'object' && manifest.engines !== null ? (manifest.engines.dsh ?? null) : null,
    deps: [],
    entryRel: 'lib/index.js',
    dir: installedTarget,
    src: fileURLToPath(new URL('./bundle/', import.meta.url)),
    installedAt: localDateTime(),
    targets: { web: false, desktop: true },
    enabled: { web: false, desktop: true },
    config: {},
    settings: null,
  };
}

export function upsertRegistry(skinRoot, entry) {
  const file = registryPath(skinRoot);
  let doc = { plugins: {}, enabled: true };
  if (fs.existsSync(file)) {
    doc = JSON.parse(fs.readFileSync(file, 'utf8'));
  }
  if (doc.plugins === null || typeof doc.plugins !== 'object' || Array.isArray(doc.plugins)) {
    doc.plugins = {};
  }
  const existed = Object.prototype.hasOwnProperty.call(doc.plugins, DEFAULTS.pluginName);
  doc.plugins[DEFAULTS.pluginName] = entry;
  fs.writeFileSync(file, JSON.stringify(doc, null, 2) + '\n', 'utf8');
  return { file, action: existed ? 'updated existing registry entry' : 'added registry entry', existed };
}

export function removeRegistry(skinRoot) {
  const file = registryPath(skinRoot);
  if (!fs.existsSync(file)) return { file, action: 'registry absent — nothing to do', existed: false };
  const doc = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (doc.plugins === null || typeof doc.plugins !== 'object' || Array.isArray(doc.plugins)) {
    return { file, action: 'registry has no plugins map — nothing to do', existed: false };
  }
  const existed = Object.prototype.hasOwnProperty.call(doc.plugins, DEFAULTS.pluginName);
  if (existed) delete doc.plugins[DEFAULTS.pluginName];
  fs.writeFileSync(file, JSON.stringify(doc, null, 2) + '\n', 'utf8');
  return { file, action: existed ? 'removed registry entry' : 'no dshpp entry — nothing to do', existed };
}

export { localTimestamp, localDateTime };
