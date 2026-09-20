#!/usr/bin/env node
/**
 * DSH++ embedded installer — installs dsh-plugin-dshpp into DSH Desktop.
 *
 *   node install.mjs [--profile <dir>] [--skin-root <dir>]
 *
 * Idempotent: safe to run repeatedly (replaces the bundle copy, the managed
 * patch block, and the registry entry). Never touches pnpm, the profile
 * package.json, or the lockfile. Snapshots every touched file before
 * modifying it (snapshots/<ts>-install/).
 *
 * Effect requires a DSH Desktop restart (a running desktop may pick up the
 * patch block via live patch reload, but the client half is boot-graph only).
 * This script never starts or restarts DSH Desktop — restart it yourself.
 */

import fs from 'node:fs';
import path from 'node:path';
import {
  DSHPP_HOME,
  BUNDLE_DIR,
  resolveSkinRoot,
  resolveProfileDir,
  assertInstallPosition,
  verifyBundle,
  snapshotFiles,
  copyBundle,
  installBlock,
  registryEntry,
  upsertRegistry,
  installDirFor,
} from './installer-core.mjs';

function argValue(flag, dflt) {
  const argv = process.argv.slice(2);
  const i = argv.indexOf(flag);
  if (i !== -1 && i + 1 < argv.length) return argv[i + 1];
  return dflt;
}

function main() {
  const explicitProfile = argValue('--profile', null);
  const explicitSkinRoot = argValue('--skin-root', null);

  const resolvedSkin = resolveSkinRoot();
  const resolvedProfile = resolveProfileDir(explicitSkinRoot ? path.resolve(explicitSkinRoot) : resolvedSkin.dir);

  const skinRoot = path.resolve(explicitSkinRoot ?? resolvedSkin.dir);
  if (!explicitProfile && !resolvedProfile.dir) {
    throw new Error(
      `无法解析 profile 目录：皮肤根（${skinRoot}）下 config.json 缺少 dsh_home 指向。` +
        `请用 --profile <dir> 显式指定，或修正皮肤根配置后重试。`,
    );
  }
  const profileDir = explicitProfile ? path.resolve(explicitProfile) : resolvedProfile.dir;

  console.log('DSH++ installer (dshskin file:// pattern)');
  console.log(`  skin root : ${skinRoot}   (${explicitSkinRoot ? '显式 --skin-root' : resolvedSkin.source})`);
  if (resolvedSkin.conflict) console.log(`  WARNING   : ${resolvedSkin.conflict}`);
  console.log(`  profile   : ${profileDir}`);

  if (resolvedProfile.dshHome) {
    console.log(`  dsh home  : ${resolvedProfile.dshHome}   (config dsh_home)`);
  }

  if (!fs.existsSync(path.join(profileDir, 'package.json'))) {
    throw new Error(`profile directory not found or has no package.json: ${profileDir}`);
  }

  const position = assertInstallPosition(profileDir, skinRoot);
  if (position.warn) console.log(`  self-check: ${position.warn}`);
  else console.log(`  self-check: OK (plugins 根 ${position.target} 与 live profile 现存 ${position.roots.length} 条 dshskin 行一致)`);

  const manifest = verifyBundle();
  const version = manifest.version;
  console.log(`  bundle    : ${BUNDLE_DIR}  (v${version})`);

  const snap = snapshotFiles('install', profileDir, skinRoot);
  console.log(`  snapshot  : ${snap.dir}  [${snap.copied.join(', ')}]`);

  const target = copyBundle(skinRoot, version);
  console.log(`  bundle    : copied -> ${target}`);

  const block = installBlock(profileDir, installDirFor(skinRoot), version);
  console.log(`  patch     : ${block.action}`);
  console.log(`              ${block.file}`);
  for (const line of block.after.trimEnd().split('\n')) {
    if (line.includes('dshpp:plugins') || line.includes('dsh-plugin-dshpp') || line.trimStart().startsWith('name:')) {
      console.log(`              | ${line}`);
    }
  }

  const reg = upsertRegistry(skinRoot, registryEntry(target, version, manifest));
  console.log(`  registry  : ${reg.action}`);

  console.log('');
  console.log('Installed. To activate:');
  console.log('  1. Restart DSH Desktop (this script never starts or restarts it).');
  console.log('  2. In the GUI, open /dshpp (floating DSH++ button) — or ask the model to check /dshpp/health.');
  console.log('');
  console.log(`To uninstall later:  node ${path.join(DSHPP_HOME, 'uninstall.mjs')}`);
}

try {
  main();
} catch (e) {
  console.error(`\ninstall FAILED: ${e.message}`);
  console.error('No partial state is left behind in the profile patch or registry unless a step after the snapshot completed; the snapshot dir keeps the pre-state of every touched file.');
  process.exit(1);
}
